"""Async download pipeline."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from tqdm import tqdm

from .core import FileEntry, detect_handler
from .utils import USER_AGENT, sanitize


async def download_one(
    client: httpx.AsyncClient,
    entry: FileEntry,
    out_dir: Path,
    sem: asyncio.Semaphore,
    files_bar: tqdm,
    bytes_bar: tqdm,
) -> tuple[str, str | None]:
    async with sem:
        name = sanitize(entry.name)
        dest = out_dir / name

        if dest.exists() and entry.size and dest.stat().st_size == entry.size:
            files_bar.update(1)
            bytes_bar.update(entry.size)
            return name, None

        try:
            cdn_url = await entry.resolve(client)
        except Exception as e:
            files_bar.update(1)
            return name, f"resolve error: {e}"

        tmp = dest.with_suffix(dest.suffix + ".part")
        downloaded = 0
        try:
            async with client.stream("GET", cdn_url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length") or entry.size or 0)
                with tmp.open("wb") as fh, tqdm(
                    total=total or None,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=name[:40],
                    leave=False,
                ) as bar:
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        fh.write(chunk)
                        n = len(chunk)
                        downloaded += n
                        bar.update(n)
                        bytes_bar.update(n)
            tmp.replace(dest)
        except Exception as e:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            files_bar.update(1)
            if downloaded:
                bytes_bar.update(-downloaded)
            return name, f"download error: {e}"

        files_bar.update(1)
        return name, None


async def process_url(
    client: httpx.AsyncClient,
    url: str,
    out_root: Path,
    concurrency: int,
) -> tuple[int, int]:
    try:
        handler = detect_handler(url)
    except ValueError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 0, 1

    print(f"\n[*] [{handler.name}] {url}")
    try:
        listing = await handler.list_files(client, url)
    except Exception as e:
        print(f"[!] Failed to list {url}: {e}", file=sys.stderr)
        return 0, 1

    if not listing.files:
        print(f"[!] No files in {url}", file=sys.stderr)
        return 0, 1

    out_dir = out_root / sanitize(listing.title)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] {listing.title}: {len(listing.files)} files -> {out_dir}")

    total_bytes = sum(f.size for f in listing.files)
    sem = asyncio.Semaphore(concurrency)
    success = 0
    failures: list[tuple[str, str]] = []
    with (
        tqdm(total=len(listing.files), desc="Files", unit="file", position=0) as files_bar,
        tqdm(
            total=total_bytes or None,
            desc="Bytes",
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            position=1,
        ) as bytes_bar,
    ):
        results = await asyncio.gather(
            *(
                download_one(client, e, out_dir, sem, files_bar, bytes_bar)
                for e in listing.files
            )
        )

    for name, err in results:
        if err:
            failures.append((name, err))
        else:
            success += 1

    if failures:
        print(f"[!] {len(failures)} file(s) failed in '{listing.title}':")
        for name, err in failures:
            print(f"    - {name}: {err}")
    print(f"[OK] {listing.title}: {success}/{len(listing.files)} files saved to {out_dir}")
    return success, len(failures)


async def run(urls: list[str], out_root: Path, concurrency: int) -> int:
    timeout = httpx.Timeout(30.0, read=300.0)
    headers = {"User-Agent": USER_AGENT}

    total_ok = 0
    total_fail = 0
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        with tqdm(total=len(urls), desc="Sources", unit="src", position=2) as src_bar:
            for url in urls:
                ok, fail = await process_url(client, url, out_root, concurrency)
                total_ok += ok
                total_fail += fail
                src_bar.update(1)

    print(f"\n[Summary] sources={len(urls)} files_ok={total_ok} files_failed={total_fail}")
    return 0 if total_fail == 0 else 2
