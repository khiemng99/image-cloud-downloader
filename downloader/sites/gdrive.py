"""Google Drive public shared-folder handler.

Lists images and videos inside a publicly shared ("Anyone with the link")
Google Drive folder, recursing through sub-folders, using the Drive API v3.
Requires a free Google API key supplied via the GOOGLE_API_KEY env var.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .. import config
from ..core import FileEntry, Listing, SiteHandler, register

API_BASE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleDriveHandler(SiteHandler):
    name = "gdrive"

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in {"drive.google.com", "www.drive.google.com"}

    @staticmethod
    def _folder_id(url: str) -> str:
        parsed = urlparse(url)
        # /drive/folders/<ID> or /drive/u/0/folders/<ID>
        match = re.search(r"/folders/([A-Za-z0-9_-]+)", parsed.path)
        if match:
            return match.group(1)
        # /open?id=<ID> (or any ?id=<ID>)
        ids = parse_qs(parsed.query).get("id")
        if ids and ids[0]:
            return ids[0]
        return ""

    @staticmethod
    def _api_key() -> str:
        key = config.google_api_key()
        if not key:
            raise ValueError(
                f"Google Drive requires an API key. Set {config.GOOGLE_API_KEY_ENV} in your "
                "environment or a .env file (create one at https://console.cloud.google.com/ "
                "with the Drive API enabled)."
            )
        return key

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_media(mime: str) -> bool:
        return mime.startswith("image/") or mime.startswith("video/")

    @classmethod
    def _make_resolver(cls, file_id: str, key: str):
        url = f"{API_BASE}/files/{file_id}?alt=media&key={key}"

        async def resolve(_client: httpx.AsyncClient, _url: str = url) -> str:
            return _url

        return resolve

    async def _folder_name(self, client: httpx.AsyncClient, folder_id: str, key: str) -> str:
        resp = await client.get(
            f"{API_BASE}/files/{folder_id}",
            params={"fields": "name", "key": key, "supportsAllDrives": "true"},
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("name") or f"gdrive_{folder_id}")

    async def _list_children(self, client: httpx.AsyncClient, folder_id: str, key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "nextPageToken, files(id, name, mimeType, size)",
                "pageSize": "1000",
                "key": key,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "orderBy": "folder,name",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(f"{API_BASE}/files", params=params)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("files") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return items

    async def _walk(
        self,
        client: httpx.AsyncClient,
        folder_id: str,
        key: str,
        subdir: str,
        visited: set[str],
        entries: list[FileEntry],
        skipped: list[str],
    ) -> None:
        if folder_id in visited:
            return
        visited.add(folder_id)

        for item in await self._list_children(client, folder_id, key):
            item_id = str(item.get("id") or "")
            name = str(item.get("name") or item_id)
            mime = str(item.get("mimeType") or "")
            if not item_id:
                continue

            if mime == FOLDER_MIME:
                child_subdir = f"{subdir}/{name}" if subdir else name
                await self._walk(client, item_id, key, child_subdir, visited, entries, skipped)
            elif self._is_media(mime):
                entries.append(
                    FileEntry(
                        name=name,
                        size=self._to_int(item.get("size"), default=0),
                        resolve=self._make_resolver(item_id, key),
                        subdir=subdir,
                    )
                )
            else:
                skipped.append(f"{subdir}/{name}" if subdir else name)

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        folder_id = self._folder_id(url)
        if not folder_id:
            raise ValueError(f"Could not find a Google Drive folder id in URL: {url}")

        key = self._api_key()
        title = await self._folder_name(client, folder_id, key)

        entries: list[FileEntry] = []
        skipped: list[str] = []
        await self._walk(client, folder_id, key, "", set(), entries, skipped)

        if skipped:
            print(f"[gdrive] Skipped {len(skipped)} non-media file(s) (images/videos only).")

        return Listing(title=title, files=entries)


register(GoogleDriveHandler())
