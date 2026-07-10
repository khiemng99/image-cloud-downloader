"""Filester folder/file handler."""

from __future__ import annotations

import random
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..core import FileEntry, Listing, ResolveFn, SiteHandler, register


class FilesterHandler(SiteHandler):
    name = "filester"
    SUPPORTED_HOSTS = ("filester.me", "filester.gg")
    CDN_HOSTS = (
        # "https://cache1.filester.me",
        # "https://cache6.filester.me",
        "https://cn1.filester.me/v2",
    )

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host.endswith(supported) for supported in FilesterHandler.SUPPORTED_HOSTS)

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @classmethod
    def _make_resolver(cls, origin: str, slug: str) -> ResolveFn:
        async def resolve(client: httpx.AsyncClient) -> str:
            r = await client.post(
                f"{origin}/api/public/download",
                json={"file_slug": slug},
                headers={"Content-Type": "application/json", "Referer": f"{origin}/d/{slug}"},
            )
            r.raise_for_status()
            data = r.json()
            path = data["download_url"]
            cdn = random.choice(cls.CDN_HOSTS)
            sep = "&" if "?" in path else "?"
            return f"{cdn}{path}{sep}download=true"

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
            name = (og.get("content") if og else None) or slug
            return Listing(
                title=name,
                files=[
                    FileEntry(name=name, size=0, resolve=self._make_resolver(origin, slug))
                ],
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
            onclick = item.get("onclick", "")
            m = re.search(r"/d/([A-Za-z0-9_-]+)", onclick)
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            name = item.get("data-name") or slug
            try:
                size = int(item.get("data-size") or 0)
            except ValueError:
                size = 0
            entries.append(
                FileEntry(name=name, size=size, resolve=self._make_resolver(origin, slug))
            )
        return Listing(title=title, files=entries)


register(FilesterHandler())
