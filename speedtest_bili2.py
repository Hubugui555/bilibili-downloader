#!/usr/bin/env python3
"""More careful Bilibili throughput test: distinct videos + sustained single stream."""

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

import bili_downloader as bd

BVIDS = [
    "BV1XJn26BEa7",
    "BV1htnt6nENM",
    "BV1xwnE6jEUZ",
    "BV15ins67ERt",
    "BV14Snw6gEjL",
    "BV1psnb63Epe",
    "BV1M1np6YE8c",
    "BV1iUnH6rEJw",
]
SINGLE_MB = 16
PARALLEL_MB = 8
WARMUP = 256 * 1024


def get_stream(bvid, qn=80):
    info = bd.get_video_info(bvid)
    playurl = bd.get_playurl(info["aid"], info["cid"], qn)
    video, _ = bd.select_best_quality(playurl, target_qn=qn)
    if not video:
        raise RuntimeError("no stream")
    url = video.get("base_url") or video.get("baseUrl")
    backups = video.get("backup_url") or video.get("backupUrl") or []
    if not url:
        raise RuntimeError("no url")
    return {
        "bvid": bvid,
        "title": info.get("title", "")[:28],
        "url": url,
        "backups": backups,
        "host": urlparse(url).hostname,
    }


def fetch_range(url, start, length, timeout=(10, 30)):
    headers = bd.HEADERS.copy()
    headers["Range"] = f"bytes={start}-{start + length - 1}"
    got = 0
    started = time.monotonic()
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            got += len(chunk)
            if got >= length:
                break
    return got, time.monotonic() - started


def warmup(url):
    try:
        fetch_range(url, 0, WARMUP)
    except Exception:
        pass


def main():
    streams = []
    for bvid in BVIDS:
        try:
            stream = get_stream(bvid)
            streams.append(stream)
            print(f"OK  {bvid}  {stream['host']:<35} {stream['title']}")
        except Exception as exc:
            print(f"SKIP {bvid}: {exc}")
    if not streams:
        raise SystemExit("no usable streams")

    # 单连接持续下载
    first = streams[0]
    warmup(first["url"])
    got, elapsed = fetch_range(first["url"], 0, SINGLE_MB * 1024 * 1024)
    mb = got / 1024 / 1024
    print(f"\nsingle connection: {mb:.1f}MB in {elapsed:.2f}s = "
          f"{mb / elapsed:.2f} MB/s ({mb / elapsed * 8:.1f} Mbps)")

    # 不同视频并发，模拟批量剧集
    for n in (2, 4, 8):
        subset = streams[:n]
        if len(subset) < n:
            break
        for s in subset:
            warmup(s["url"])
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(
                lambda s: fetch_range(s["url"], 0, PARALLEL_MB * 1024 * 1024),
                subset))
        elapsed = time.monotonic() - t0
        total = sum(r[0] for r in results)
        mb = total / 1024 / 1024
        speed = mb / elapsed
        per = [r[0] / 1024 / 1024 / r[1] for r in results]
        print(f"distinct videos n={n}: {mb:.1f}MB in {elapsed:.2f}s = "
              f"{speed:.2f} MB/s ({speed * 8:.1f} Mbps), "
              f"per-conn {min(per):.2f}-{max(per):.2f} MB/s")
        time.sleep(1)


if __name__ == "__main__":
    main()
