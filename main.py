"""Multi-site album/folder downloader.

Currently supported:
    - cyberdrop.cr / cyberdrop.me  (album:  /a/<id>)
    - filester.me                  (folder: /f/<id>, single file: /d/<slug>)
    - bunkr.site (and mirrors)     (album:  /a/<id>, single file: /f/<slug>)
    - jpg6.su                      (user/album listing, e.g. /<username>)

Usage:
    uv run main.py <url> [<url> ...] [-o OUTPUT_DIR] [-c CONCURRENCY]
    uv run main.py -f urls.txt [-o OUTPUT_DIR] [-c CONCURRENCY]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from downloader import run
from downloader.utils import read_urls_file


def main() -> None:
    p = argparse.ArgumentParser(description="Multi-site downloader (cyberdrop, filester, bunkr, jpg6).")
    p.add_argument("urls", nargs="*", help="Album/folder/file URL(s)")
    p.add_argument("-f", "--file", help="Path to a text file with one URL per line (# comments allowed)")
    p.add_argument("-o", "--output", default="downloads", help="Output directory (default: downloads)")
    p.add_argument("-c", "--concurrency", type=int, default=4, help="Parallel downloads per source (default: 4)")
    args = p.parse_args()

    urls: list[str] = list(args.urls)
    if args.file:
        try:
            urls.extend(read_urls_file(Path(args.file)))
        except OSError as e:
            print(f"[!] Could not read {args.file}: {e}", file=sys.stderr)
            sys.exit(1)

    if not urls:
        p.error("Provide at least one URL or use -f <file>.")

    try:
        rc = asyncio.run(run(urls, Path(args.output), args.concurrency))
    except KeyboardInterrupt:
        print("\n[!] Interrupted.", file=sys.stderr)
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
