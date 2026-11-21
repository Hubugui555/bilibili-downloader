"""Download engine: resumable streaming, CDN speed ranking, and concurrent pipelines."""

import os
import random
import shutil
import threading
import time
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Optional

import requests

from .api import (
    get_bangumi_playurl,
    get_course_playurl,
    get_media_collection,
    get_playurl,
    get_season_info,
    get_series_info,
    get_video_info,
    get_video_info_by_aid,
)
from .config import build_headers
from .constants import HEADERS, QUALITY_MAP
from .danmaku import (
    download_cover,
    download_episode_extras,
    download_extras_and_merge,
)
from .media import extract_audio, merge_video_audio, select_best_quality
from .parser import (
    extract_bvid,
    extract_epid,
    extract_series_id,
    extract_ssid,
    format_filename,
    safe_filename,
)

_LINE_CACHE_LOCK = threading.Lock()
_LINE_CACHE: dict[str, tuple[float, float]] = {}
_LINE_CACHE_TTL = 600.0


def stream_urls(stream: Optional[dict]) -> list[str]:
    """Return deduplicated primary + backup CDN URLs in priority order."""
    if not stream:
        return []
    urls: list[str] = []
    for key in ("base_url", "baseUrl", "url"):
        value = stream.get(key)
        if value:
            urls.append(value)
    for key in ("backup_url", "backupUrl"):
        value = stream.get(key) or []
        if isinstance(value, str):
            value = [value]
        urls.extend(value)
    seen = set()
    result = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _probe_url_speed(
    url: str,
    start: int = 1 * 1024 * 1024,
    length: int = 4 * 1024 * 1024,
    timeout=(8, 25),
    stop_event=None,
) -> float:
    """Measure sustained CDN throughput (MB/s) using a bounded Range probe."""
    headers = HEADERS.copy()
    headers["Range"] = f"bytes={start}-{start + length - 1}"
    got = 0
    started = time.monotonic()
    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if stop_event and stop_event.is_set():
                    return 0.0
                if not chunk:
                    continue
                got += len(chunk)
                if got >= length:
                    break
    except Exception:
        return 0.0
    elapsed = time.monotonic() - started
    return got / 1024 / 1024 / elapsed if elapsed > 0 else 0.0


def rank_stream_urls(
    urls: list[str],
    stop_event=None,
    quiet: bool = False,
    small: bool = False,
    enabled: bool = True,
) -> list[str]:
    """Sort CDN URLs by measured throughput with a 10-minute host cache."""
    if not enabled or len(urls) <= 1:
        return urls
    if stop_event and stop_event.is_set():
        return urls

    now = time.time()
    start = 512 * 1024 if small else 2 * 1024 * 1024
    length = 2 * 1024 * 1024 if small else 6 * 1024 * 1024
    speeds: dict[str, float] = {}
    to_probe: list[str] = []

    with _LINE_CACHE_LOCK:
        for url in urls:
            host = urllib.parse.urlparse(url).hostname or ""
            cached = _LINE_CACHE.get(host)
            if cached and now - cached[0] < _LINE_CACHE_TTL:
                speeds[url] = cached[1]
            else:
                to_probe.append(url)

    if to_probe:
        if not quiet:
            print("  正在测速选择最佳 CDN 线路...")
        with ThreadPoolExecutor(max_workers=min(4, len(to_probe))) as pool:
            probed = list(
                pool.map(
                    lambda u: _probe_url_speed(
                        u, start=start, length=length, stop_event=stop_event
                    ),
                    to_probe,
                )
            )
        with _LINE_CACHE_LOCK:
            for url, speed in zip(to_probe, probed):
                host = urllib.parse.urlparse(url).hostname or ""
                speeds[url] = speed
                if speed > 0:
                    _LINE_CACHE[host] = (now, speed)

    ranked = sorted(urls, key=lambda u: speeds.get(u, 0.0), reverse=True)
    if to_probe and not quiet:
        for url in ranked:
            host = urllib.parse.urlparse(url).hostname or ""
            print(f"  线路测速: {host} ({speeds.get(url, 0):.2f} MB/s)")
    if all(speeds.get(u, 0.0) <= 0 for u in urls):
        return urls
    return ranked


def download_stream(
    url: str,
    filepath: Path,
    desc: str = "",
    stop_event=None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    telemetry_callback: Optional[Callable[[dict], None]] = None,
    quiet: bool = False,
    max_retries: int = 5,
) -> bool:
    """Download a single stream with Range resume, exponential backoff, and live speed telemetry."""
    filepath = Path(filepath)
    temp_path = filepath.with_suffix(filepath.suffix + ".tmp")
    host = urllib.parse.urlparse(url).hostname or ""
    last_error = "未知错误"

    for attempt in range(max_retries + 1):
        if stop_event and stop_event.is_set():
            return False

        headers = build_headers()
        resume_size = temp_path.stat().st_size if temp_path.exists() else 0
        if resume_size:
            headers["Range"] = f"bytes={resume_size}-"

        resp = None
        downloaded = resume_size
        total = 0
        try:
            resp = requests.get(url, headers=headers, stream=True, timeout=(15, 120))

            if resp.status_code == 416:
                if resume_size > 0:
                    os.replace(temp_path, filepath)
                    return True
                raise RuntimeError("服务器拒绝断点范围请求 (HTTP 416)")

            if resp.status_code not in (200, 206):
                detail = resp.text[:120] if resp.text else ""
                raise RuntimeError(f"HTTP {resp.status_code} {detail}".strip())

            if resume_size > 0 and resp.status_code == 200:
                resume_size = 0
                downloaded = 0

            total = int(resp.headers.get("content-length", 0)) + resume_size
            mode = "ab" if resume_size > 0 else "wb"

            if resume_size > 0 and not quiet:
                print(
                    f"  断点续传: {resume_size / 1024 / 1024:.1f}MB / "
                    f"{total / 1024 / 1024:.1f}MB"
                )
            if progress_callback and total:
                progress_callback(downloaded, total)

            win_start = time.monotonic()
            win_bytes = 0
            last_print_pct = -1.0
            last_print_bytes = 0

            with open(temp_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if stop_event and stop_event.is_set():
                        if not quiet:
                            print("  ⏹ 已取消")
                        return False
                    if not chunk:
                        continue
                    f.write(chunk)
                    clen = len(chunk)
                    downloaded += clen
                    win_bytes += clen

                    now = time.monotonic()
                    dt = now - win_start
                    if dt >= 0.35 or (total and downloaded >= total):
                        speed_bps = win_bytes / max(dt, 0.001)
                        eta = ((total - downloaded) / speed_bps) if (total and speed_bps > 1) else 0.0
                        if telemetry_callback:
                            telemetry_callback(
                                {
                                    "desc": desc,
                                    "downloaded": downloaded,
                                    "total": total,
                                    "speed_bps": speed_bps,
                                    "eta": eta,
                                    "host": host,
                                }
                            )
                        win_start = now
                        win_bytes = 0

                    if total:
                        pct = downloaded / total * 100
                        if progress_callback:
                            progress_callback(downloaded, total)
                        if not quiet and (
                            pct - last_print_pct >= 2
                            or downloaded - last_print_bytes >= 2 * 1024 * 1024
                        ):
                            bar_len = 28
                            filled = int(bar_len * downloaded / total)
                            bar = "█" * filled + "░" * (bar_len - filled)
                            print(
                                f"\r  {desc} [{bar}] {pct:.1f}% "
                                f"({downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f}MB)",
                                end="",
                            )
                            last_print_pct = pct
                            last_print_bytes = downloaded

            if not quiet:
                print()

            if not total or downloaded >= total:
                os.replace(temp_path, filepath)
                return True

            raise RuntimeError(f"连接提前结束: {downloaded}/{total} bytes")
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        except Exception as e:
            last_error = str(e)
        finally:
            if resp is not None:
                resp.close()

        if stop_event and stop_event.is_set():
            return False
        if attempt < max_retries:
            delay = min(2 ** attempt, 20) + random.random()
            if not quiet:
                print(
                    f"  ⚠ {desc} 中断，{delay:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries}): {last_error}"
                )
            if stop_event is not None:
                if stop_event.wait(delay):
                    return False
            else:
                time.sleep(delay)

    raise RuntimeError(f"{desc or '数据流'}下载失败，已重试 {max_retries} 次: {last_error}")


def download_with_fallback(
    urls: list[str],
    path: Path,
    desc: str,
    stop_event=None,
    progress_callback=None,
    telemetry_callback=None,
    quiet: bool = False,
    stream_downloader=None,
) -> bool:
    """Try primary and backup CDN URLs sequentially."""
    if not urls:
        return False
    downloader = stream_downloader or download_stream
    last_error: Optional[Exception] = None
    max_retries = 5 if len(urls) == 1 else 2

    for index, url in enumerate(urls):
        if stop_event and stop_event.is_set():
            return False
        if len(urls) > 1 and not quiet:
            print(f"  线路 {index + 1}/{len(urls)}...")
        try:
            kwargs = {
                "desc": desc,
                "stop_event": stop_event,
                "progress_callback": progress_callback,
                "quiet": quiet,
                "max_retries": max_retries,
            }
            if stream_downloader is None:
                kwargs["telemetry_callback"] = telemetry_callback
            if downloader(url, path, **kwargs):
                return True
        except Exception as exc:
            last_error = exc
            if not quiet:
                print(f"  ⚠ 线路 {index + 1} 失败: {exc}")
    if last_error:
        raise last_error
    return False


def download_streams(
    title: str,
    part: str,
    playurl: dict,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    telemetry_callback=None,
    quiet: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
    metadata: Optional[dict] = None,
    cover_path: Optional[Path] = None,
    stream_downloader=None,
) -> Optional[dict]:
    """Download video/audio stream fragments and return a merge job descriptor."""
    qn = QUALITY_MAP.get(quality, 80)
    video_stream, audio_stream = select_best_quality(playurl, target_qn=qn, codec=codec)

    if audio_only:
        if not audio_stream:
            if not quiet:
                print("  ✗ 未找到可用音频流")
            return None
        aud_urls = stream_urls(audio_stream)
        if not aud_urls:
            if not quiet:
                print("  ✗ 音频 URL 为空")
            return None
        if shutil.which("ffmpeg") is None:
            if not quiet:
                print("  ✗ 未检测到 ffmpeg（音频提取必需），请安装 FFmpeg 后重试")
            return None

        video_dir = Path(output_dir) / title
        video_dir.mkdir(parents=True, exist_ok=True)
        aud_path = video_dir / f"{part}_audio.m4s"
        need_audio = not aud_path.exists()
        if need_audio:
            aud_urls = rank_stream_urls(
                aud_urls, stop_event, quiet=quiet, small=True, enabled=cdn_probe
            )
            if not quiet:
                print("  🎵 下载音频流...")
            if not download_with_fallback(
                aud_urls,
                aud_path,
                "音频",
                stop_event=stop_event,
                progress_callback=progress_callback,
                telemetry_callback=telemetry_callback,
                quiet=quiet,
                stream_downloader=stream_downloader,
            ):
                return None

        fmt = (audio_format or "m4a").lower()
        if fmt not in ("m4a", "mp3", "flac"):
            fmt = "m4a"
        output_path = video_dir / f"{part}.{fmt}"
        return {
            "video": None,
            "audio": aud_path,
            "output": output_path,
            "audio_only": True,
            "audio_format": fmt,
            "metadata": metadata or {},
            "cover_path": cover_path,
        }

    if not video_stream:
        if not quiet:
            print("  ✗ 未找到可用视频流")
        return None

    vid_urls = stream_urls(video_stream)
    aud_urls = stream_urls(audio_stream) if audio_stream else []
    if not vid_urls:
        if not quiet:
            print("  ✗ 视频 URL 为空")
        return None

    if aud_urls and shutil.which("ffmpeg") is None:
        if not quiet:
            print("  ✗ 未检测到 ffmpeg（音视频合并必需），请安装 FFmpeg 后重试")
        return None

    video_dir = Path(output_dir) / title
    video_dir.mkdir(parents=True, exist_ok=True)

    ext = "m4s" if "dash" in playurl else "flv"
    vid_path = video_dir / f"{part}_video.{ext}"
    aud_path = video_dir / f"{part}_audio.{ext}" if aud_urls else None
    need_video = not vid_path.exists()
    need_audio = bool(aud_path) and not aud_path.exists()

    if need_video:
        vid_urls = rank_stream_urls(
            vid_urls, stop_event, quiet=quiet, small=False, enabled=cdn_probe
        )
    if need_audio:
        aud_urls = rank_stream_urls(
            aud_urls, stop_event, quiet=quiet, small=True, enabled=cdn_probe
        )

    progress_scale = 1_000_000

    def _stream_cb(kind: str):
        def cb(downloaded: int, total: int):
            if not progress_callback or not total:
                return
            if need_video and need_audio:
                lo, hi = (0.0, 0.85) if kind == "video" else (0.85, 1.0)
            else:
                lo, hi = 0.0, 1.0
            fraction = lo + (hi - lo) * min(downloaded / total, 1.0)
            progress_callback(int(fraction * progress_scale), progress_scale)

        return cb

    if not quiet:
        print("  📥 下载视频流...")
    if need_video:
        if not download_with_fallback(
            vid_urls,
            vid_path,
            "视频",
            stop_event=stop_event,
            progress_callback=_stream_cb("video"),
            telemetry_callback=telemetry_callback,
            quiet=quiet,
            stream_downloader=stream_downloader,
        ):
            if stop_event and stop_event.is_set():
                return None
            if not quiet:
                print("  ✗ 视频下载失败")
            return None
    elif not quiet:
        print("  ✓ 视频文件已存在, 跳过")

    if aud_path:
        if not quiet:
            print("  📥 下载音频流...")
        if need_audio:
            if not download_with_fallback(
                aud_urls,
                aud_path,
                "音频",
                stop_event=stop_event,
                progress_callback=_stream_cb("audio"),
                telemetry_callback=telemetry_callback,
                quiet=quiet,
                stream_downloader=stream_downloader,
            ):
                if stop_event and stop_event.is_set():
                    return None
                if not quiet:
                    print("  ✗ 音频下载失败")
                return None
        elif not quiet:
            print("  ✓ 音频文件已存在, 跳过")

    output_path = video_dir / (f"{part}.flv" if ext == "flv" else f"{part}.mp4")
    return {
        "video": vid_path,
        "audio": aud_path,
        "output": output_path,
        "audio_only": False,
        "metadata": metadata or {},
    }


def merge_downloaded_streams(
    job: dict,
    quiet: bool = False,
    video_audio_merger=None,
) -> bool:
    """Merge downloaded stream fragments into the final media file."""
    vid_path = job.get("video")
    aud_path = job.get("audio")
    output_path = Path(job["output"])

    if job.get("audio_only"):
        if not quiet:
            print(f"  🎵 提取音频 ({job.get('audio_format', 'm4a').upper()})...")
        ok = extract_audio(
            Path(aud_path),
            output_path,
            audio_format=job.get("audio_format", "m4a"),
            cover_path=job.get("cover_path"),
            metadata=job.get("metadata"),
            quiet=quiet,
        )
        if ok and not quiet:
            print(f"  ✓ 音频提取完成: {output_path.name}")
        return ok

    if aud_path:
        if not quiet:
            print("  🔗 合并音视频...")
        merger = video_audio_merger or merge_video_audio
        if video_audio_merger is not None:
            ok = merger(vid_path, aud_path, output_path)
        else:
            ok = merger(
                vid_path,
                aud_path,
                output_path,
                metadata=job.get("metadata"),
                quiet=quiet,
            )
        if ok:
            if not quiet:
                print(f"  ✓ 下载完成: {output_path}")
            return True
        if not quiet:
            print("  ⚠ 合并失败, 保留单独文件")
        return False

    if output_path.exists():
        try:
            Path(vid_path).unlink(missing_ok=True)
        except OSError:
            pass
    else:
        try:
            os.replace(vid_path, output_path)
        except OSError as e:
            if not quiet:
                print(f"  ✗ 保存视频失败: {e}")
            return False
    if not quiet:
        print(f"  ✓ 下载完成: {output_path}")
    return True


def download_playurl_streams(
    title: str,
    part: str,
    playurl: dict,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    telemetry_callback=None,
    quiet: bool = False,
    defer_merge: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
    metadata: Optional[dict] = None,
    cover_path: Optional[Path] = None,
    stream_downloader=None,
    video_audio_merger=None,
):
    job = download_streams(
        title,
        part,
        playurl,
        output_dir,
        quality=quality,
        stop_event=stop_event,
        progress_callback=progress_callback,
        telemetry_callback=telemetry_callback,
        quiet=quiet,
        codec=codec,
        audio_only=audio_only,
        audio_format=audio_format,
        cdn_probe=cdn_probe,
        metadata=metadata,
        cover_path=cover_path,
        stream_downloader=stream_downloader,
    )
    if job is None:
        return False
    if defer_merge:
        return job
    return merge_downloaded_streams(job, quiet=quiet, video_audio_merger=video_audio_merger)


def download_single(
    bvid: str,
    output_dir: Path,
    quality: str = "1080P",
    page: int = 0,
    stop_event=None,
    progress_callback=None,
    telemetry_callback=None,
    quiet: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
    filename_template: str = "{title}",
    folder_override: Optional[str] = None,
    index: Optional[int] = None,
    info: Optional[dict] = None,
    defer_merge: bool = False,
) -> bool | dict:
    """Download a single video (or specific page of a multi-P video)."""
    if not quiet:
        print(f"\n📺 获取视频信息: {bvid}")
    try:
        info = info or get_video_info(bvid)
    except Exception as e:
        if not quiet:
            print(f"  ✗ {e}")
        return False

    raw_title = info["title"]
    owner = (info.get("owner") or {}).get("name", "")
    folder_name = safe_filename(folder_override or raw_title)
    aid = info["aid"]
    pages = info.get("pages", [])

    if pages and page > 1:
        idx = page - 1
        if idx >= len(pages):
            if not quiet:
                print(f"  ✗ 只有 {len(pages)} 个分P")
            return False
        p = pages[idx]
        cid = p["cid"]
        part_raw = p.get("part", f"P{page}")
        part = format_filename(
            filename_template if filename_template != "{title}" else "{index}_{part}",
            title=raw_title,
            part=part_raw,
            index=index if index is not None else page,
            owner=owner,
            bvid=bvid,
            quality=quality,
        )
    elif pages and page == 1 and len(pages) > 1:
        p = pages[0]
        cid = p["cid"]
        part_raw = p.get("part", "P1")
        part = format_filename(
            filename_template if filename_template != "{title}" else "{index}_{part}",
            title=raw_title,
            part=part_raw,
            index=index if index is not None else 1,
            owner=owner,
            bvid=bvid,
            quality=quality,
        )
    else:
        cid = info["cid"]
        part = format_filename(
            filename_template,
            title=raw_title,
            part=raw_title,
            index=index,
            owner=owner,
            bvid=bvid,
            quality=quality,
        )

    qn = QUALITY_MAP.get(quality, 80)
    if not quiet:
        print(f"  标题: {raw_title}")
        print(f"  文件: {part}")
        print(f"  画质: {quality} (qn={qn}) | 编码: {codec}")

    cover_path = None
    if audio_only and info.get("pic"):
        cover_path = download_cover(
            cover_url=info.get("pic"),
            output_dir=Path(output_dir) / folder_name,
            base_name=part,
            quiet=True,
        )

    playurl = get_playurl(aid, cid, qn)
    metadata = {"title": part, "artist": owner, "album": raw_title}
    return download_playurl_streams(
        folder_name,
        part,
        playurl,
        Path(output_dir),
        quality=quality,
        stop_event=stop_event,
        progress_callback=progress_callback,
        telemetry_callback=telemetry_callback,
        quiet=quiet,
        defer_merge=defer_merge,
        codec=codec,
        audio_only=audio_only,
        audio_format=audio_format,
        cdn_probe=cdn_probe,
        metadata=metadata,
        cover_path=cover_path,
    )


def download_course_episode(
    course: dict,
    episode: dict,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    telemetry_callback=None,
    part_prefix: str = "",
    quiet: bool = False,
    defer_merge: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
):
    ep_id = episode.get("id")
    aid = episode.get("aid")
    cid = episode.get("cid")
    if not all((ep_id, aid, cid)):
        raise RuntimeError("课程课时缺少播放所需的 ep_id/aid/cid")

    title = safe_filename(course.get("title", "课程"))
    ep_title = safe_filename(episode.get("title", f"EP{ep_id}"))
    part = safe_filename(f"{part_prefix}{ep_title}") if part_prefix else ep_title
    qn = QUALITY_MAP.get(quality, 80)
    if not quiet:
        print(f"\n📺 获取课程课时: {part}")
        print(f"  课程: {title} | 画质: {quality}")
    playurl = get_course_playurl(aid, cid, ep_id, qn)
    return download_playurl_streams(
        title,
        part,
        playurl,
        Path(output_dir),
        quality=quality,
        stop_event=stop_event,
        progress_callback=progress_callback,
        telemetry_callback=telemetry_callback,
        quiet=quiet,
        defer_merge=defer_merge,
        codec=codec,
        audio_only=audio_only,
        audio_format=audio_format,
        cdn_probe=cdn_probe,
        metadata={"title": ep_title, "album": title},
    )


def download_bangumi_episode(
    collection_title: str,
    episode: dict,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    telemetry_callback=None,
    part_prefix: str = "",
    quiet: bool = False,
    defer_merge: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
):
    """Download a single bangumi episode (PGC API with fallback; preserves target quality!)."""
    ep_id = episode.get("ep_id")
    aid = episode.get("aid")
    cid = episode.get("cid")
    if not all((ep_id, aid, cid)):
        raise RuntimeError("番剧集数缺少播放所需的 ep_id/aid/cid")

    title = safe_filename(collection_title or "番剧")
    ep_title = safe_filename(episode.get("title", f"EP{ep_id}"))
    part = safe_filename(f"{part_prefix}{ep_title}") if part_prefix else ep_title
    qn = QUALITY_MAP.get(quality, 80)
    if not quiet:
        print(f"\n📺 获取番剧: {part}")
        print(f"  系列: {title} | 画质: {quality}")
    try:
        playurl = get_bangumi_playurl(aid, cid, ep_id, qn)
    except Exception:
        playurl = get_playurl(aid, cid, qn)
    return download_playurl_streams(
        title,
        part,
        playurl,
        Path(output_dir),
        quality=quality,
        stop_event=stop_event,
        progress_callback=progress_callback,
        telemetry_callback=telemetry_callback,
        quiet=quiet,
        defer_merge=defer_merge,
        codec=codec,
        audio_only=audio_only,
        audio_format=audio_format,
        cdn_probe=cdn_probe,
        metadata={"title": ep_title, "album": title},
    )


def download_selected_episodes(
    collection: dict,
    selected_ep_ids: list,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    max_workers: int = 1,
    episode_status_callback=None,
    danmaku: bool = False,
    subtitle: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
    embed_extras: bool = True,
    danmaku_style: Optional[dict] = None,
    all_subtitles: bool = False,
    telemetry_callback=None,
    course_downloader=None,
    bangumi_downloader=None,
    stream_merger=None,
) -> dict:
    """Download selected episodes concurrently with a separate download & FFmpeg merge pipeline."""
    max_workers = max(1, int(max_workers))
    media_type = collection["media_type"]
    collection_title = collection["title"]
    all_eps = collection["episodes"]

    selected_set = set(selected_ep_ids)
    ordered_selected = [ep for ep in all_eps if ep["ep_id"] in selected_set]

    total = len(ordered_selected)
    if total == 0:
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "cancelled": False,
            "cancelled_count": 0,
            "failures": [],
            "max_workers": max_workers,
        }

    course_dl = course_downloader or download_course_episode
    bangumi_dl = bangumi_downloader or download_bangumi_episode
    merger = stream_merger or merge_downloaded_streams
    merge_workers = max(1, min(2, max_workers))

    def make_part_prefix(ep: dict) -> str:
        return f"{ep['index']:03d}_"

    def _event(status: str, ordinal: int, ep: dict, error: str = "", progress: Optional[float] = None):
        if episode_status_callback:
            payload = {
                "status": status,
                "ep_id": ep["ep_id"],
                "index": ep.get("index", 0),
                "title": ep.get("title", ""),
                "ordinal": ordinal,
                "total": total,
                "error": error,
            }
            if progress is not None:
                payload["progress"] = progress
            episode_status_callback(payload)

    def _result(status: str, ordinal: int, ep: dict, error: str = "") -> dict:
        return {
            "status": status,
            "ep_id": ep["ep_id"],
            "title": ep.get("title", ""),
            "index": ep.get("index", 0),
            "ordinal": ordinal,
            "total": total,
            "error": error,
        }

    def download_one(ordinal: int, ep: dict):
        if stop_event and stop_event.is_set():
            return _result("cancelled", ordinal, ep), None

        _event("started", ordinal, ep, progress=0.0)

        def ep_progress(done: int, tot: int):
            if progress_callback:
                progress_callback(done, tot)
            if tot > 0:
                _event("downloading", ordinal, ep, progress=min(done / tot * 100.0, 99.0))

        try:
            prefix = make_part_prefix(ep)
            if media_type == "course":
                if course_downloader is not None:
                    job = course_dl(
                        collection["raw"],
                        ep["raw"],
                        output_dir,
                        quality,
                        stop_event=stop_event,
                        progress_callback=progress_callback,
                        part_prefix=prefix,
                        quiet=True,
                        defer_merge=True,
                    )
                else:
                    job = course_dl(
                        collection["raw"],
                        ep["raw"],
                        output_dir,
                        quality,
                        stop_event=stop_event,
                        progress_callback=ep_progress,
                        telemetry_callback=telemetry_callback,
                        part_prefix=prefix,
                        quiet=True,
                        defer_merge=True,
                        codec=codec,
                        audio_only=audio_only,
                        audio_format=audio_format,
                        cdn_probe=cdn_probe,
                    )
            else:
                if bangumi_downloader is not None:
                    job = bangumi_dl(
                        collection_title,
                        ep,
                        output_dir,
                        quality,
                        stop_event=stop_event,
                        progress_callback=progress_callback,
                        part_prefix=prefix,
                        quiet=True,
                        defer_merge=True,
                    )
                else:
                    job = bangumi_dl(
                        collection_title,
                        ep,
                        output_dir,
                        quality,
                        stop_event=stop_event,
                        progress_callback=ep_progress,
                        telemetry_callback=telemetry_callback,
                        part_prefix=prefix,
                        quiet=True,
                        defer_merge=True,
                        codec=codec,
                        audio_only=audio_only,
                        audio_format=audio_format,
                        cdn_probe=cdn_probe,
                    )
        except Exception as e:
            err_msg = str(e)
            _event("failed", ordinal, ep, err_msg)
            return _result("failed", ordinal, ep, err_msg), None

        if not job:
            if stop_event and stop_event.is_set():
                return _result("cancelled", ordinal, ep), None
            _event("failed", ordinal, ep, "下载未完成")
            return _result("failed", ordinal, ep, "下载未完成"), None

        if not isinstance(job, dict):
            _event("completed", ordinal, ep, progress=100.0)
            return _result("completed", ordinal, ep), None

        return None, job

    def merge_one(ordinal: int, ep: dict, job: dict) -> dict:
        _event("merging", ordinal, ep, progress=95.0)
        err_msg = ""
        try:
            ok = merger(job, quiet=True)
        except Exception as e:
            ok = False
            err_msg = str(e)
        if ok:
            if (danmaku or subtitle) and not audio_only:
                raw = ep.get("raw") or ep
                aid, cid = raw.get("aid") or ep.get("aid"), raw.get("cid") or ep.get("cid")
                if aid and cid:
                    try:
                        download_episode_extras(
                            Path(job["output"]).parent,
                            Path(job["output"]).stem,
                            aid,
                            cid,
                            danmaku=danmaku,
                            subtitle=subtitle,
                            merge=embed_extras,
                            danmaku_style=danmaku_style,
                            all_subtitles=all_subtitles,
                        )
                    except Exception as e:
                        print(f"  ⚠ 第{ep.get('index', '?')}集弹幕/字幕合并失败: {e}")
            _event("completed", ordinal, ep, progress=100.0)
            return _result("completed", ordinal, ep)
        if not err_msg:
            err_msg = "合并失败"
        _event("failed", ordinal, ep, err_msg)
        return _result("failed", ordinal, ep, err_msg)

    results = []
    cancelled = False
    pending_iter = iter(list(enumerate(ordered_selected, 1)))
    running_downloads = {}
    running_merges = {}

    with ThreadPoolExecutor(max_workers=max_workers) as download_executor, ThreadPoolExecutor(
        max_workers=merge_workers
    ) as merge_executor:

        def submit_next():
            try:
                ordinal, ep = next(pending_iter)
            except StopIteration:
                return
            if stop_event and stop_event.is_set():
                results.append(_result("cancelled", ordinal, ep))
                return
            fut = download_executor.submit(download_one, ordinal, ep)
            running_downloads[fut] = (ordinal, ep)

        for _ in range(max_workers):
            submit_next()

        while running_downloads or running_merges:
            done_set, _ = wait(
                list(running_downloads) + list(running_merges),
                return_when=FIRST_COMPLETED,
            )
            for fut in done_set:
                if fut in running_downloads:
                    ordinal, ep = running_downloads.pop(fut)
                    result, job = fut.result()
                    if result is not None:
                        results.append(result)
                        if result["status"] == "cancelled":
                            cancelled = True
                    if job is not None:
                        merge_fut = merge_executor.submit(merge_one, ordinal, ep, job)
                        running_merges[merge_fut] = (ordinal, ep)
                    if not (stop_event and stop_event.is_set()):
                        submit_next()
                else:
                    running_merges.pop(fut)
                    results.append(fut.result())

    if stop_event and stop_event.is_set():
        cancelled = True
        for ordinal, ep in pending_iter:
            results.append(_result("cancelled", ordinal, ep))

    ep_order = {ep["ep_id"]: i for i, ep in enumerate(all_eps)}
    results.sort(key=lambda r: ep_order.get(r["ep_id"], 999999))

    success = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] == "failed")
    cancelled_count = sum(1 for r in results if r["status"] == "cancelled")
    failures = [
        {
            "ep_id": r["ep_id"],
            "title": r["title"],
            "index": r.get("index", 0),
            "error": r["error"],
        }
        for r in results
        if r["status"] == "failed"
    ]

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "cancelled": cancelled,
        "cancelled_count": cancelled_count,
        "failures": failures,
        "max_workers": max_workers,
    }


def download_batch(
    url_or_bvid: str,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    danmaku: bool = False,
    subtitle: bool = False,
    *,
    codec: str = "auto",
    audio_only: bool = False,
    audio_format: str = "m4a",
    cdn_probe: bool = True,
):
    """Auto-detect and download BV / ep / ss / series / course."""
    bvid = extract_bvid(url_or_bvid)
    ep_id = extract_epid(url_or_bvid)
    ss_id = extract_ssid(url_or_bvid)
    sid = extract_series_id(url_or_bvid)
    is_cheese = "/cheese/" in (url_or_bvid or "").lower()

    if sid:
        print(f"\n📂 检测到合集 series_id={sid}")
        archives = get_series_info(sid)
        print(f"   共 {len(archives)} 个视频")
        for i, arc in enumerate(archives):
            if stop_event and stop_event.is_set():
                print("⏹ 已停止")
                return
            bv = arc["bvid"]
            p_title = safe_filename(arc.get("title", f"video_{i + 1}"))
            print(f"\n[{i + 1}/{len(archives)}] {p_title} ({bv})")
            ok = download_single(
                bv,
                output_dir,
                quality,
                stop_event=stop_event,
                progress_callback=progress_callback,
                codec=codec,
                audio_only=audio_only,
                audio_format=audio_format,
                cdn_probe=cdn_probe,
            )
            if ok and not audio_only and (danmaku or subtitle):
                download_extras_and_merge(bv, output_dir, danmaku=danmaku, subtitle=subtitle)
        return

    if ep_id or ss_id:
        type_name = "课程" if is_cheese else "番剧"
        ident = f"ep_id={ep_id}" if ep_id else f"season_id={ss_id}"
        print(f"\n📺 检测到{type_name} {ident}")
        season = get_season_info(ep_id=ep_id, season_id=ss_id, is_cheese=is_cheese)
        episodes = season.get("episodes", [])
        season_title = season.get("title") or season.get("season_title") or "未知"

        if ep_id:
            selected = next((ep for ep in episodes if ep.get("id") == ep_id), None)
            if selected is None:
                raise RuntimeError(f"{type_name}中未找到 ep{ep_id}")
            batch = [(selected, "")]
        else:
            batch = [(ep, f"{i:03d}_") for i, ep in enumerate(episodes, 1)]
            print(f"   共 {len(batch)} 集")

        for i, (ep, prefix) in enumerate(batch, 1):
            if stop_event and stop_event.is_set():
                print("⏹ 已停止")
                return
            ep_title = safe_filename(
                ep.get("long_title")
                or ep.get("share_copy")
                or ep.get("title")
                or f"EP{i}"
            )
            print(f"\n[{i}/{len(batch)}] {ep_title}")
            if is_cheese:
                ok = download_course_episode(
                    season,
                    ep,
                    output_dir,
                    quality,
                    stop_event=stop_event,
                    progress_callback=progress_callback,
                    part_prefix=prefix,
                    codec=codec,
                    audio_only=audio_only,
                    audio_format=audio_format,
                    cdn_probe=cdn_probe,
                )
            else:
                episode = {
                    "ep_id": ep.get("id") or ep.get("ep_id"),
                    "aid": ep.get("aid"),
                    "cid": ep.get("cid"),
                    "title": ep_title,
                }
                if not all((episode["ep_id"], episode["aid"], episode["cid"])):
                    bv = ep.get("bvid", "")
                    aid = ep.get("aid") or episode["ep_id"]
                    if not bv and aid:
                        try:
                            bv = get_video_info_by_aid(aid).get("bvid", "")
                        except Exception:
                            bv = ""
                    if not bv:
                        print("  ⚠ 该集缺少播放信息，跳过")
                        continue
                    download_single(
                        bv,
                        output_dir,
                        quality,
                        stop_event=stop_event,
                        progress_callback=progress_callback,
                        codec=codec,
                        audio_only=audio_only,
                        audio_format=audio_format,
                        cdn_probe=cdn_probe,
                    )
                    continue
                ok = download_bangumi_episode(
                    season_title,
                    episode,
                    output_dir,
                    quality,
                    stop_event=stop_event,
                    progress_callback=progress_callback,
                    part_prefix=prefix,
                    codec=codec,
                    audio_only=audio_only,
                    audio_format=audio_format,
                    cdn_probe=cdn_probe,
                )
            if not ok and not (stop_event and stop_event.is_set()):
                raise RuntimeError(f"{type_name}下载未完成")
            if ok and not audio_only and (danmaku or subtitle):
                download_episode_extras(
                    Path(output_dir) / safe_filename(season_title),
                    safe_filename(f"{prefix}{ep_title}"),
                    ep.get("aid"),
                    ep.get("cid"),
                    danmaku=danmaku,
                    subtitle=subtitle,
                )
        return

    if bvid:
        info = get_video_info(bvid)
        pages = info.get("pages", [])
        if len(pages) > 1:
            print(f"\n📑 检测到多P视频: {len(pages)} 个分P")
            for i, p in enumerate(pages):
                if stop_event and stop_event.is_set():
                    print("⏹ 已停止")
                    return
                print(f"\n[{i + 1}/{len(pages)}] {p.get('part', f'P{i + 1}')}")
                ok = download_single(
                    bvid,
                    output_dir,
                    quality,
                    page=i + 1,
                    stop_event=stop_event,
                    progress_callback=progress_callback,
                    codec=codec,
                    audio_only=audio_only,
                    audio_format=audio_format,
                    cdn_probe=cdn_probe,
                    info=info,
                )
                if ok and not audio_only and (danmaku or subtitle):
                    part_name = format_filename(
                        "{index}_{part}",
                        title=info["title"],
                        part=p.get("part", f"P{i + 1}"),
                        index=i + 1,
                    )
                    download_episode_extras(
                        Path(output_dir) / safe_filename(info["title"]),
                        part_name,
                        info["aid"],
                        p.get("cid"),
                        danmaku=danmaku,
                        subtitle=subtitle,
                    )
        else:
            ok = download_single(
                bvid,
                output_dir,
                quality,
                stop_event=stop_event,
                progress_callback=progress_callback,
                codec=codec,
                audio_only=audio_only,
                audio_format=audio_format,
                cdn_probe=cdn_probe,
                info=info,
            )
            if ok and not audio_only and (danmaku or subtitle):
                download_extras_and_merge(
                    bvid, output_dir, danmaku=danmaku, subtitle=subtitle, info=info
                )
        return

    print("✗ 无法解析链接或BV号")
