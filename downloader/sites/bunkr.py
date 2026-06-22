"""Bunkr album/file handler."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from ..core import FileEntry, Listing, ResolveFn, SiteHandler, register

_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?B)\s*$", re.IGNORECASE)
_UNIT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def _parse_size(text: str) -> int:
    m = _SIZE_RE.match(text or "")
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _UNIT[m.group(2).upper()])
    except (ValueError, KeyError):
        return 0


class BunkrHandler(SiteHandler):
    name = "bunkr"
    SIGN_URL = "https://glb-apisign.cdn.cr/sign"

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return "bunkr" in host.split(".")

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @classmethod
    def _make_resolver(cls, origin: str, slug: str) -> ResolveFn:
        async def resolve(client: httpx.AsyncClient) -> str:
            file_page = f"{origin}/f/{slug}"
            r = await client.get(file_page)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            dl_link = soup.select_one('a[href*="/file/"]')
            if not dl_link or not dl_link.get("href"):
                raise RuntimeError(f"no download link for {slug}")
            dl_url = dl_link["href"]
            m = re.search(r"/file/(\d+)", dl_url)
            if not m:
                raise RuntimeError(f"unparseable download url: {dl_url}")
            file_id = m.group(1)
            dl_origin = f"{urlparse(dl_url).scheme}://{urlparse(dl_url).netloc}"

            meta = await client.post(
                f"{dl_origin}/api/_001_v2",
                json={"id": file_id},
                headers={"Content-Type": "application/json", "Referer": dl_url},
            )
            meta.raise_for_status()
            md = meta.json()
            raw = httpx.URL(md["mediafiles"] + md["path"])
            original = md.get("original") or ""
            if original:
                raw = raw.copy_set_param("n", original)

            sign = await client.get(cls.SIGN_URL, params={"path": unquote(raw.path)})
            sign.raise_for_status()
            sd = sign.json()
            return str(raw.copy_set_param("token", sd["token"]).copy_set_param("ex", str(sd["ex"])))

        return resolve

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        path = urlparse(url).path
        origin = self._origin(url)

        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        og_title = soup.find("meta", attrs={"property": "og:title"})
        title = (og_title.get("content") if og_title else None) or "bunkr"

        if path.startswith("/f/"):
            slug = path.split("/f/", 1)[1].strip("/").split("/")[0]
            size_el = soup.find("p", string=_SIZE_RE)
            size = _parse_size(size_el.get_text(strip=True)) if size_el else 0
            return Listing(
                title=title,
                files=[
                    FileEntry(name=title, size=size, resolve=self._make_resolver(origin, slug))
                ],
            )

        if not path.startswith("/a/"):
            raise ValueError(f"Unsupported bunkr URL: {url}")

        entries: list[FileEntry] = []
        seen: set[str] = set()
        for item in soup.select(".theItem"):
            link = item.select_one('a[href^="/f/"]')
            if not link:
                continue
            m = re.match(r"^/f/([A-Za-z0-9_-]+)", link.get("href", ""))
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            name_el = item.select_one(".theName")
            name = name_el.get_text(strip=True) if name_el else (item.get("title") or slug)
            size_el = item.select_one(".theSize")
            size = _parse_size(size_el.get_text(strip=True)) if size_el else 0
            entries.append(
                FileEntry(name=name, size=size, resolve=self._make_resolver(origin, slug))
            )
        return Listing(title=title, files=entries)


register(BunkrHandler())
