"""JPG6 (Chevereto) image listing handler."""

from __future__ import annotations

import json
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..core import FileEntry, Listing, SiteHandler, register


class Jpg6Handler(SiteHandler):
    name = "jpg6"

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith("jpg6.su")

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def _parse_items(soup: BeautifulSoup) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for item in soup.select(".list-item[data-object]"):
            raw = item.get("data-object") or ""
            try:
                obj = json.loads(unquote(raw))
            except (ValueError, TypeError):
                continue
            img = obj.get("image") or {}
            url = img.get("url")
            if not url:
                continue
            name = img.get("filename") or img.get("name") or "image"
            try:
                size = int(img.get("size") or 0)
            except (ValueError, TypeError):
                size = 0

            async def resolve(_c: httpx.AsyncClient, _u: str = url) -> str:
                return _u

            entries.append(FileEntry(name=name, size=size, resolve=resolve))
        return entries

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        origin = self._origin(url)
        page_url: str | None = url
        title = ""
        entries: list[FileEntry] = []
        seen: set[str] = set()

        while page_url:
            r = await client.get(page_url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            if not title:
                og = soup.find("meta", attrs={"property": "og:title"})
                title = (og.get("content") if og else None) or "jpg6"

            for entry in self._parse_items(soup):
                if entry.name in seen:
                    continue
                seen.add(entry.name)
                entries.append(entry)

            next_link = soup.select_one('.content-listing-pagination li.pagination-next a[href]')
            page_url = urljoin(origin, next_link["href"]) if next_link else None

        return Listing(title=title, files=entries)


register(Jpg6Handler())
