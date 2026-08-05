"""Filester folder/file handler."""

from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

from .. import config
from ..core import FileEntry, Listing, ResolveFn, SiteHandler, register
from ..utils import attr

# The site publishes its CDN pool as a `const CDN_URLS = [...]` array in /js/file_dl.js.
_CDN_URLS_RE = re.compile(r"CDN_URLS\s*=\s*\[(.*?)\]", re.S)
_JS_HTTPS_STRING_RE = re.compile(r"['\"](https://[^'\"]+)['\"]")


class FilesterHandler(SiteHandler):
    name = "filester"
    SUPPORTED_HOSTS = ("filester.me", "filester.gg")
    # Last-resort pool, used only if scraping /js/file_dl.js fails. Both are consulted
    # only when a token response omits its own "server".
    CDN_HOSTS = (
        "https://cn1.filester.me",
        "https://p1.filester.me",
        "https://rs2.filester.me",
    )
    _cdn_cache: dict[str, tuple[str, ...]] = {}
    _cdn_lock = asyncio.Lock()

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host.endswith(supported) for supported in FilesterHandler.SUPPORTED_HOSTS)

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Bearer header for filester's own API, empty when no key is configured.

        Only ever attach this to filester hosts — never to the CDN URL returned by
        the token endpoint, which is a third-party origin.
        """
        key = config.filester_api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    @classmethod
    async def _cdn_hosts(cls, client: httpx.AsyncClient, origin: str) -> tuple[str, ...]:
        """CDN pool advertised by the site's own JS, fetched at most once per origin.

        Falls back to `CDN_HOSTS` if the script moves, stops parsing, or fails to load.
        """
        cached = cls._cdn_cache.get(origin)
        if cached is not None:
            return cached

        async with cls._cdn_lock:
            cached = cls._cdn_cache.get(origin)
            if cached is not None:
                return cached

            hosts: tuple[str, ...] = ()
            try:
                r = await client.get(f"{origin}/js/file_dl.js")
                r.raise_for_status()
                m = _CDN_URLS_RE.search(r.text)
                if m:
                    hosts = tuple(_JS_HTTPS_STRING_RE.findall(m.group(1)))
            except Exception:  # pylint: disable=broad-exception-caught
                # This is itself the fallback path: any failure here must degrade to
                # CDN_HOSTS rather than break an otherwise working download.
                hosts = ()

            cls._cdn_cache[origin] = hosts or cls.CDN_HOSTS
            return cls._cdn_cache[origin]

    @classmethod
    def _make_resolver(cls, origin: str, slug: str) -> ResolveFn:
        async def resolve(client: httpx.AsyncClient) -> str:
            # Mirrors generatePublicDownloadToken() + buildMediaUrl() in /js/file_dl.js.
            # The token is short-lived (~30 min) and bound to the requesting IP.
            r = await client.post(
                f"{origin}/v2/api/public/download",
                json={"file_slug": slug},
                headers={
                    "Content-Type": "application/json",
                    "Referer": f"{origin}/d/{slug}",
                    **cls._auth_headers(),
                },
            )
            r.raise_for_status()
            data = r.json()

            file_path = str(data.get("file") or "")
            token = str(data.get("token") or "")
            if not file_path or not token:
                raise RuntimeError(f"no download token for {slug}: {data}")

            base = str(data.get("server") or "")
            if not base:
                base = random.choice(await cls._cdn_hosts(client, origin))
            url = f"{base.rstrip('/')}/v2/{file_path}?token={quote(token, safe='')}&download=true"
            name = str(data.get("name") or "")
            if name:
                url += f"&n={quote(name, safe='')}"
            return url

        return resolve

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        path = urlparse(url).path
        origin = self._origin(url)

        if path.startswith("/d/"):
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            slug = path.split("/d/")[-1].strip("/").split("/")[0]
            og = soup.find("meta", attrs={"property": "og:title"})
            name = attr(og, "content") or slug
            return Listing(
                title=name,
                files=[FileEntry(name=name, size=0, resolve=self._make_resolver(origin, slug))],
            )

        if not path.startswith("/f/"):
            raise ValueError(f"Unsupported filester URL: {url}")

        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        title_el = soup.select_one(".folder-title")
        title = title_el.get_text(strip=True) if title_el else "filester_folder"

        entries: list[FileEntry] = []
        seen: set[str] = set()
        for item in soup.select(".file-item"):
            m = re.search(r"/d/([A-Za-z0-9_-]+)", attr(item, "onclick"))
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            name = attr(item, "data-name") or slug
            try:
                size = int(attr(item, "data-size") or 0)
            except ValueError:
                size = 0
            entries.append(FileEntry(name=name, size=size, resolve=self._make_resolver(origin, slug)))
        return Listing(title=title, files=entries)


register(FilesterHandler())
