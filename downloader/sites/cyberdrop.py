"""Cyberdrop album handler."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tqdm import tqdm

from ..core import FileEntry, Listing, SiteHandler, register


class CyberdropHandler(SiteHandler):
    name = "cyberdrop"

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith("cyberdrop.cr") or host.endswith("cyberdrop.me")

    @staticmethod
    def _api_base(url: str) -> str:
        host = urlparse(url).hostname or "cyberdrop.cr"
        root = ".".join(host.split(".")[-2:])
        return f"https://api.{root}"

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        if "/a/" not in urlparse(url).path:
            raise ValueError(f"Not a cyberdrop album URL: {url}")

        api = self._api_base(url)
        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        title_el = soup.select_one("#title")
        title = title_el.get_text(strip=True) if title_el else "cyberdrop_album"

        slugs: list[str] = []
        seen: set[str] = set()
        for a in soup.select('a[href^="/f/"]'):
            m = re.match(r"^/f/([A-Za-z0-9_-]+)", a.get("href", ""))
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                slugs.append(m.group(1))

        entries: list[FileEntry] = []
        for slug in tqdm(slugs, desc="Resolving", unit="file", leave=False):
            try:
                resp = await client.get(f"{api}/api/file/info/{slug}")
                resp.raise_for_status()
                info = resp.json()
            except Exception:
                continue

            auth_url = info["auth_url"]

            async def resolve(c: httpx.AsyncClient, _auth=auth_url) -> str:
                ar = await c.get(_auth)
                ar.raise_for_status()
                return ar.json()["url"]

            entries.append(
                FileEntry(
                    name=info.get("name") or slug,
                    size=int(info.get("size") or 0),
                    resolve=resolve,
                )
            )
        return Listing(title=title, files=entries)


register(CyberdropHandler())
