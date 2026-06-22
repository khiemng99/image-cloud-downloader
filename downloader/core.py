"""Site-agnostic types and the handler registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx

ResolveFn = Callable[[httpx.AsyncClient], Awaitable[str]]


@dataclass
class FileEntry:
    name: str
    size: int  # 0 if unknown
    resolve: ResolveFn  # late-binds the actual CDN URL


@dataclass
class Listing:
    title: str
    files: list["FileEntry"]


class SiteHandler:
    name: str = ""

    @staticmethod
    def matches(url: str) -> bool:
        raise NotImplementedError

    async def list_files(self, client: httpx.AsyncClient, url: str) -> Listing:
        raise NotImplementedError


_HANDLERS: list[SiteHandler] = []


def register(handler: SiteHandler) -> SiteHandler:
    _HANDLERS.append(handler)
    return handler


def detect_handler(url: str) -> SiteHandler:
    for h in _HANDLERS:
        if h.matches(url):
            return h
    supported = ", ".join(h.name for h in _HANDLERS) or "(none registered)"
    host = urlparse(url).hostname or url
    raise ValueError(f"No handler for {host} (supported: {supported})")
