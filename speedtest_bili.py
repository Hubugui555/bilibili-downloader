#!/usr/bin/env python3
"""Quick Bilibili CDN throughput test for choosing download concurrency."""

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

import bili_downloader as bd

BVID = "BV1XJn26BEa7"
CHUNK = 4 * 1024 * 1024  # 4 MiB per connection
WARMUP = 512 * 1024


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


def main():
    info = bd.get_video_info(BVID)
    playurl = bd.get_playurl(info["aid"], info["cid"], 80)
    video, audio = bd.select_best_quality(playurl, target_qn=80)
    url = video.get("base_url") or video.get("baseUrl")
    host = urlparse(url).hostname
    print(f"video: {info['title'][:40]}")
    print(f"quality id: {video.get('id')}, bandwidth: {video.get('bandwidth')}, "
          f"size: {video.get('size')}, host: {host}")

    # 预热，排除 TLS/DNS 首次开销
    fetch_range(url, 0, WARMUP)

    for workers in (1, 2, 4, 8):
        starts = [i * CHUNK for i in range(workers)]
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                lambda start: fetch_range(url, start, CHUNK), starts))
        elapsed = time.monotonic() - t0
        total = sum(r[0] for r in results)
        mb = total / 1024 / 1024
        speed = mb / elapsed
        print(f"workers={workers:>2}  data={mb:6.1f}MB  time={elapsed:5.2f}s  "
              f"speed={speed:6.2f} MB/s  ({speed * 8:6.1f} Mbps)")
        time.sleep(1)


if __name__ == "__main__":
    main()
