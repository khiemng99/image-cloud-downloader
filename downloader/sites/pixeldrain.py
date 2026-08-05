"""Pixeldrain file/list handler."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core import FileEntry, Listing, SiteHandler, register


class PixeldrainHandler(SiteHandler):
    name = "pixeldrain"
    API_BASE = "https://pixeldrain.com/api"
    SUPPORTED_HOSTS = {
        "pixeldrain.com",
        "pixeldrain.net",
        "pixeldra.in",
        "pixeldrain.nl",
        "pixeldrain.biz",
        "pixeldrain.tech",
        "pixeldrain.dev",
    }

    @staticmethod
    def matches(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in PixeldrainHandler.SUPPORTED_HOSTS

    @staticmethod
    def _extract_id(path: str, prefix: str) -> str:
        if not path.startswith(prefix):
            return ""
        return path.split(prefix, 1)[1].strip("/").split("/")[0]

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _unwrap_payload(data: Any) -> Any:
        if isinstance(data, dict) and "value" in data and "success" in data:
            return data.get("value")
        return data

    @staticmethod
    def _extract_file_id(item: dict[str, Any]) -> str:
        file_id = str(item.get("id") or "").strip()
        if file_id:
            return file_id

        detail_href = str(item.get("detail_href") or "")
        match = re.search(r"/file/([A-Za-z0-9_-]+)/info", detail_href)
        if match:
            return match.group(1)
        return ""

    @classmethod
    def _make_download_resolver(cls, file_id: str):
        async def resolve(_client: httpx.AsyncClient, _id: str = file_id) -> str:
            return f"{cls.API_BASE}/file/{_id}?download"

        return resolve

    async def _list_single_file(self, client: httpx.AsyncClient, file_id: str) -> Listing:
        resp = await client.get(f"{self.API_BASE}/file/{file_id}/info")
        resp.raise_for_status()
        raw = self._unwrap_payload(resp.json())
        if not isinstance(raw, dict):
            raise ValueError("Unexpected pixeldrain file info response format")

        name = str(raw.get("name") or file_id)
        size = self._to_int(raw.get("size"), default=0)
        return Listing(
            title=name,
            files=[FileEntry(name=name, size=size, resolve=self._make_download_resolver(file_id))],
        )

    async def _list_collection(self, client: httpx.AsyncClient, list_id: str) -> Listing:
        resp = await client.get(f"{self.API_BASE}/list/{list_id}")
        resp.raise_for_status()
        raw = self._unwrap_payload(resp.json())
        if not isinstance(raw, dict):
            raise ValueError("Unexpected pixeldrain list response format")

        title = str(raw.get("title") or raw.get("name") or f"pixeldrain_{list_id}")
        files = raw.get("files")
        if not isinstance(files, list):
            files = []

        entries: list[FileEntry] = []
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                continue
            file_id = self._extract_file_id(item)
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)

            name = str(item.get("name") or item.get("filename") or file_id)
            size = self._to_int(item.get("size"), default=0)
            entries.append(FileEntry(name=name, size=size, resolve=self._make_download_resolver(file_id)))

        return Listing(title=title, files=entries)

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        path = urlparse(url).path

        if path.startswith("/u/"):
            file_id = self._extract_id(path, "/u/")
            if not file_id:
                raise ValueError(f"Missing pixeldrain file id in URL: {url}")
            return await self._list_single_file(client, file_id)

        if path.startswith("/l/"):
            list_id = self._extract_id(path, "/l/")
            if not list_id:
                raise ValueError(f"Missing pixeldrain list id in URL: {url}")
            return await self._list_collection(client, list_id)

        raise ValueError(f"Unsupported pixeldrain URL: {url}")


register(PixeldrainHandler())
