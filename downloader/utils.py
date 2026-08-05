"""Shared helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    cleaned = INVALID_FS_CHARS.sub("_", name).strip(" .")
    return cleaned or "file"


def attr(tag: Any, name: str, default: str = "") -> str:
    """Read an HTML attribute off a BeautifulSoup tag as a single string.

    bs4 hands back a list for multi-valued attributes (``class``, ``rel``, ...), which
    would otherwise reach `re` calls and `urljoin` as a list and raise at runtime. A
    missing tag or attribute yields `default`.
    """
    value = tag.get(name) if tag is not None else None
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def read_urls_file(path: Path) -> list[str]:
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls
