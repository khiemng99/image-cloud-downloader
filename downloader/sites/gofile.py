"""Gofile folder/file handler.

Lists a gofile share (``https://gofile.io/d/<code>``), recursing through
sub-folders, via the documented ``GET /contents/{contentId}`` endpoint
(https://gofile.io/api).

Two things about that endpoint are not obvious from the docs:

* It is Premium-only -- a free or guest token alone is answered with
  ``error-notPremium``. The website serves free visitors by also sending an
  ``X-Website-Token`` header, reproduced by `_website_token` below, which is what
  lets public shares list here without a paid account. Set ``GOFILE_API_TOKEN``
  to list with your own account instead of a throwaway guest one.
* The storage servers authorize the download itself off an ``accountToken``
  cookie, not the Authorization header, so the token is also planted in the
  shared client's cookie jar for the ``.gofile.<tld>`` domain.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx

from .. import config
from ..core import FileEntry, Listing, SiteHandler, register
from ..utils import USER_AGENT

PAGE_SIZE = 100

# `X-Website-Token` recipe, lifted from the site's own (obfuscated) /js/wt.obf.js:
#     sha256(f"{userAgent}::{language}::{token}::{epoch_ms // BUCKET_MS}::{SALT}")
# The salt is a constant baked into that bundle and the bucket rotates every four
# hours, so a token stays valid for at most that long. Gofile rotating the salt
# breaks guest listing (the API starts answering `error-notPremium` again); the
# fix is to re-read the value out of the bundle, or to set GOFILE_API_TOKEN to a
# Premium token, which does not need this header at all.
WEBSITE_TOKEN_SALT = "12af056dacea0b"
WEBSITE_TOKEN_BUCKET_MS = 4 * 60 * 60 * 1000
ACCEPT_LANGUAGE = "en-US"


@dataclass(frozen=True)
class _Session:
    """Per-source invariants threaded through the recursive listing walk."""

    api_base: str
    token: str
    tld: str


# API statuses worth translating; anything else is surfaced verbatim.
STATUS_HINTS = {
    "error-notPremium": (
        "gofile refused the listing as non-Premium. The X-Website-Token that normally covers "
        "free accounts was rejected -- its salt has probably been rotated. Set "
        f"{config.GOFILE_API_TOKEN_ENV} to a Premium account token to list regardless."
    ),
    "error-notFound": "No such gofile content (wrong code, or the share was removed).",
    "error-rateLimit": "gofile is rate-limiting this IP; try again in a few minutes.",
}


class GofileHandler(SiteHandler):
    name = "gofile"
    SUPPORTED_HOSTS = ("gofile.io", "gofile.net")

    # A guest account is minted at most once per process and reused across sources.
    _guest_token: str = ""
    _token_lock = asyncio.Lock()

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == supported or host.endswith(f".{supported}") for supported in GofileHandler.SUPPORTED_HOSTS)

    @staticmethod
    def _tld(url: str) -> str:
        """Site TLD ("io"/"net") of the incoming URL, mirroring gofile's own cookie logic."""
        host = (urlparse(url).hostname or "").lower()
        return "net" if host.endswith("gofile.net") else "io"

    @staticmethod
    def _content_id(url: str) -> str:
        """Folder/file UUID or share code from a /d/<id> link (or a legacy ?c=<id> one)."""
        parsed = urlparse(url)
        parts = [segment for segment in parsed.path.split("/") if segment]
        if len(parts) >= 2 and parts[0] == "d":
            return parts[1]
        codes = parse_qs(parsed.query).get("c")
        if codes and codes[0]:
            return codes[0]
        return ""

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _website_token(token: str) -> str:
        """Reproduce the browser's `X-Website-Token` for `token` in the current time bucket.

        The hash covers the User-Agent, and gofile checks it against the one on the request,
        so this only validates while the client sends `utils.USER_AGENT` -- which the engine's
        shared client does. A caller overriding that header would have to hash the same value.
        """
        bucket = int(time.time() * 1000) // WEBSITE_TOKEN_BUCKET_MS
        raw = f"{USER_AGENT}::{ACCEPT_LANGUAGE}::{token}::{bucket}::{WEBSITE_TOKEN_SALT}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _auth_headers(cls, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Website-Token": cls._website_token(token),
            "X-BL": ACCEPT_LANGUAGE,
        }

    @classmethod
    async def _token(cls, client: httpx.AsyncClient, api_base: str) -> str:
        """Configured account token, else a guest one minted (once) via POST /accounts."""
        configured = config.gofile_api_token()
        if configured:
            return configured

        async with cls._token_lock:
            if cls._guest_token:
                return cls._guest_token
            resp = await client.post(f"{api_base}/accounts")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != "ok":
                raise ValueError(cls._status_error(str(payload.get("status") or "error-noResponse")))
            token = str((payload.get("data") or {}).get("token") or "")
            if not token:
                raise ValueError("gofile returned a guest account without a token")
            cls._guest_token = token
            return token

    @staticmethod
    def _status_error(status: str) -> str:
        hint = STATUS_HINTS.get(status)
        return f"gofile API error '{status}'" + (f": {hint}" if hint else "")

    @staticmethod
    def _check_access(data: dict[str, Any], content_id: str) -> None:
        """Reject the gated 200s -- password prompts and expired shares are not errors to the API."""
        if data.get("canAccess") is False:
            reason = str(data.get("passwordStatus") or "")
            if reason and reason != "passwordOk":
                raise ValueError(f"gofile content {content_id} is password-protected ({reason})")
            raise ValueError(f"gofile content {content_id} is not accessible (private, expired or removed)")

    @staticmethod
    def _child_items(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Children as a list; the API keys them by id, but tolerate an array too."""
        raw = data.get("children")
        if isinstance(raw, dict):
            return [child for child in raw.values() if isinstance(child, dict)]
        if isinstance(raw, list):
            return [child for child in raw if isinstance(child, dict)]
        return []

    @classmethod
    def _make_resolver(cls, child: dict[str, Any], tld: str):
        """Static store URL for a file child; the listing already carries a usable link.

        A child missing both `link` and a server fails inside `resolve`, not here, so one
        unplayable file costs that file rather than the whole listing.
        """
        link = str(child.get("link") or "")
        servers = child.get("servers")
        server = str(child.get("serverSelected") or "") or (
            str(servers[0]) if isinstance(servers, list) and servers else ""
        )
        file_id = str(child.get("id") or "")
        name = str(child.get("name") or file_id)

        async def resolve(
            _client: httpx.AsyncClient,
            _link: str = link,
            _server: str = server,
            _id: str = file_id,
            _name: str = name,
        ) -> str:
            if _link:
                return _link
            if not _server or not _id:
                raise ValueError("no download link in the gofile listing")
            return f"https://{_server}.gofile.{tld}/download/web/{_id}/{quote(_name)}"

        return resolve

    async def _get_page(
        self,
        client: httpx.AsyncClient,
        session: _Session,
        content_id: str,
        page: int,
    ) -> dict[str, Any]:
        resp = await client.get(
            f"{session.api_base}/contents/{quote(content_id, safe='')}",
            params={
                "page": page,
                "pageSize": PAGE_SIZE,
                "sortField": "name",
                "sortDirection": 1,
            },
            headers=self._auth_headers(session.token),
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected gofile contents response format")
        if payload.get("status") != "ok":
            raise ValueError(self._status_error(str(payload.get("status") or "error-noResponse")))
        return payload

    async def _fetch_content(
        self,
        client: httpx.AsyncClient,
        session: _Session,
        content_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """A folder's own metadata plus every child, paging until the API says it is done."""
        data: dict[str, Any] = {}
        children: list[dict[str, Any]] = []
        seen: set[str] = set()
        page = 1
        while True:
            payload = await self._get_page(client, session, content_id, page)
            page_data = payload.get("data")
            if not isinstance(page_data, dict):
                raise ValueError("Unexpected gofile contents response format")
            if page == 1:
                data = page_data
                self._check_access(page_data, content_id)

            added = 0
            for child in self._child_items(page_data):
                child_id = str(child.get("id") or "")
                if not child_id or child_id in seen:
                    continue
                seen.add(child_id)
                children.append(child)
                added += 1

            metadata = payload.get("metadata")
            if not (isinstance(metadata, dict) and metadata.get("hasNextPage")):
                break
            # `hasNextPage` is the server's word for it; a page that adds nothing new means
            # it is repeating itself, and trusting the flag alone would spin forever.
            if added == 0:
                break
            page += 1
        return data, children

    async def _walk(
        self,
        client: httpx.AsyncClient,
        session: _Session,
        content_id: str,
        subdir: str,
        visited: set[str],
        entries: list[FileEntry],
    ) -> dict[str, Any]:
        """Append every file under `content_id` to `entries`; returns the folder's own metadata."""
        if content_id in visited:
            return {}
        visited.add(content_id)

        data, children = await self._fetch_content(client, session, content_id)

        # A share code can point straight at a file, which comes back without children.
        if str(data.get("type") or "") == "file":
            entries.append(self._entry(data, session.tld, subdir))
            return data

        for child in children:
            if str(child.get("type") or "") == "folder":
                child_id = str(child.get("id") or "")
                if not child_id:
                    # Recursing on "" would request /contents/ and abort the whole source.
                    continue
                name = str(child.get("name") or child_id)
                child_subdir = f"{subdir}/{name}" if subdir else name
                await self._walk(client, session, child_id, child_subdir, visited, entries)
            else:
                entries.append(self._entry(child, session.tld, subdir))
        return data

    @classmethod
    def _entry(cls, child: dict[str, Any], tld: str, subdir: str) -> FileEntry:
        name = str(child.get("name") or child.get("id") or "file")
        return FileEntry(
            name=name,
            size=cls._to_int(child.get("size"), default=0),
            resolve=cls._make_resolver(child, tld),
            subdir=subdir,
        )

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        content_id = self._content_id(url)
        if not content_id:
            raise ValueError(f"Could not find a gofile content id in URL: {url}")

        tld = self._tld(url)
        api_base = f"https://api.gofile.{tld}"
        token = await self._token(client, api_base)

        # The storage servers read the token off this cookie, not the Authorization
        # header. Scoped to the gofile parent domain so it reaches store-N.gofile.<tld>
        # (and nothing else) when the engine streams the file.
        client.cookies.set("accountToken", token, domain=f".gofile.{tld}")

        entries: list[FileEntry] = []
        session = _Session(api_base=api_base, token=token, tld=tld)
        data = await self._walk(client, session, content_id, "", set(), entries)

        title = str(data.get("name") or f"gofile_{content_id}")
        return Listing(title=title, files=entries)


register(GofileHandler())
