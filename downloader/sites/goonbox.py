"""Goonbox album handler."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from ..core import FileEntry, Listing, SiteHandler, register


class GoonboxHandler(SiteHandler):
    name = "goonbox"

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith("goonbox.cr")

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def _extract_slug(path: str, prefix: str) -> str:
        if not path.startswith(prefix):
            return ""
        return path.split(prefix, 1)[1].strip("/").split("/")[0]

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _image_to_entry(
        cls,
        image: dict[str, Any],
        fallback_name: str,
    ) -> tuple[str, FileEntry] | None:
        file_url = image.get("original_url")
        if not file_url:
            return None

        name = image.get("original_filename") or image.get("encoded_id") or fallback_name
        size = cls._to_int(image.get("size_bytes"), default=0)

        async def resolve(_c: httpx.AsyncClient, _u: str = str(file_url)) -> str:
            return _u

        return str(file_url), FileEntry(name=str(name), size=size, resolve=resolve)

    @classmethod
    async def _list_album_entries(
        cls,
        client: httpx.AsyncClient,
        origin: str,
        album_id: str,
        seen_urls: set[str],
    ) -> tuple[str, list[FileEntry]]:
        page = 1
        last_page = 1
        title = album_id
        entries: list[FileEntry] = []

        while page <= last_page:
            resp = await client.get(f"{origin}/api/albums/{album_id}", params={"page": page})
            resp.raise_for_status()
            data = resp.json()

            album = data.get("album") or {}
            if album.get("title"):
                title = str(album["title"])

            pagination = data.get("pagination") or {}
            last_page = cls._to_int(pagination.get("last_page"), default=1)

            for image in data.get("images") or []:
                item = cls._image_to_entry(image, fallback_name=f"goonbox_{len(entries) + 1}")
                if not item:
                    continue
                file_url, entry = item
                if file_url in seen_urls:
                    continue
                seen_urls.add(file_url)
                entries.append(entry)

            page += 1

        return title, entries

    @classmethod
    async def _list_user_images(
        cls,
        client: httpx.AsyncClient,
        origin: str,
        username: str,
        seen_urls: set[str],
    ) -> list[FileEntry]:
        page = 1
        last_page = 1
        entries: list[FileEntry] = []

        while page <= last_page:
            resp = await client.get(
                f"{origin}/api/users/{username}/images",
                params={"page": page},
            )
            resp.raise_for_status()
            data = resp.json()

            pagination = data.get("pagination") or {}
            last_page = cls._to_int(pagination.get("last_page"), default=1)

            for image in data.get("images") or []:
                item = cls._image_to_entry(
                    image,
                    fallback_name=f"{username}_image_{len(entries) + 1}",
                )
                if not item:
                    continue
                file_url, entry = item
                if file_url in seen_urls:
                    continue
                seen_urls.add(file_url)
                entries.append(entry)

            page += 1

        return entries

    @classmethod
    async def _list_user_albums(
        cls,
        client: httpx.AsyncClient,
        origin: str,
        username: str,
        seen_urls: set[str],
    ) -> list[FileEntry]:
        page = 1
        last_page = 1
        entries: list[FileEntry] = []

        while page <= last_page:
            resp = await client.get(
                f"{origin}/api/users/{username}/albums",
                params={"page": page},
            )
            resp.raise_for_status()
            data = resp.json()

            pagination = data.get("pagination") or {}
            last_page = cls._to_int(pagination.get("last_page"), default=1)

            for album in data.get("albums") or []:
                album_id = str(album.get("encoded_id") or "").strip()
                if not album_id:
                    continue
                _, album_entries = await cls._list_album_entries(
                    client=client,
                    origin=origin,
                    album_id=album_id,
                    seen_urls=seen_urls,
                )
                entries.extend(album_entries)

            page += 1

        return entries

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        path = urlparse(url).path
        origin = self._origin(url)
        if path.startswith("/a/"):
            album_id = self._extract_slug(path, "/a/")
            if not album_id:
                raise ValueError(f"Missing goonbox album id in URL: {url}")

            title, entries = await self._list_album_entries(
                client=client,
                origin=origin,
                album_id=album_id,
                seen_urls=set(),
            )
            return Listing(title=title or "goonbox_album", files=entries)

        if path.startswith("/u/"):
            username = self._extract_slug(path, "/u/")
            if not username:
                raise ValueError(f"Missing goonbox username in URL: {url}")

            seen_urls: set[str] = set()
            image_entries = await self._list_user_images(
                client=client,
                origin=origin,
                username=username,
                seen_urls=seen_urls,
            )
            album_entries = await self._list_user_albums(
                client=client,
                origin=origin,
                username=username,
                seen_urls=seen_urls,
            )
            return Listing(
                title=f"goonbox_{username}",
                files=[*image_entries, *album_entries],
            )

        raise ValueError(f"Unsupported goonbox URL: {url}")


register(GoonboxHandler())
