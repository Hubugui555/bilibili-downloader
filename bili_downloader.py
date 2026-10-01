#!/usr/bin/env python3
"""
Bilibili Video Downloader
支持: 单个视频 / 合集 / 番剧 / 课程
功能: 最高画质下载, 弹幕下载, 字幕下载, 封面下载, 断点续传
"""

import os
import re
import sys
import io as _io

# Windows GBK 终端兼容
if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import json
import time
import shutil
import subprocess
import urllib.parse
import random
from pathlib import Path
from typing import Optional

import requests

# ─── 常量 ────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}
BASE_URL = "https://api.bilibili.com"

# 画质映射: qn 值
QUALITY_MAP = {
    "8K": 127, "杜比视界": 126, "HDR": 125,
    "4K": 120, "1080P60": 116, "1080P+": 112,
    "1080P": 80, "720P60": 74, "720P": 64,
    "480P": 32, "360P": 16, "240P": 6,
}

# 音质排序: 数值越大音质越好。
# 注意 30250(杜比全景声) / 30251(Hi-Res) 的 id 数值小于 30280(192K)，
# 但实际音质更高，不能直接按 id 降序排。
AUDIO_RANK = {30216: 0, 30232: 1, 30280: 2, 30250: 3, 30251: 4}


# ─── 工具函数 ────────────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _config_path() -> Path:
    """config.json 固定放在脚本/exe 所在目录，避免 CWD 不同导致登录态丢失。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path(__file__).parent / "config.json"


def get_sessdata() -> str:
    """从配置文件读取 SESSDATA (可选)"""
    cfg = _config_path()
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data.get("sessdata", "")
    return ""


def get_cookie_string() -> str:
    """读取完整登录 Cookie，同时兼容旧版仅保存 SESSDATA 的配置。"""
    cfg = _config_path()
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
        if data.get("cookie"):
            return data["cookie"]
        if data.get("cookies"):
            return "; ".join(f"{key}={value}" for key, value in data["cookies"].items())
        if data.get("sessdata"):
            return f"SESSDATA={data['sessdata']}"
    return ""


def save_cookie_string(cookie: str) -> dict:
    """保存浏览器/扫码得到的完整 Cookie，并提取常用字段。"""
    cookie = cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    cookies = {}
    for item in cookie.split(";"):
        if "=" not in item:
            continue
        key, value = item.strip().split("=", 1)
        if key:
            cookies[key] = value
    sessdata = cookies.get("SESSDATA", "")
    if not sessdata:
        raise Exception("Cookie 中未找到 SESSDATA，请粘贴已登录的 bilibili.com 请求 Cookie")
    cfg = {"sessdata": sessdata, "cookie": cookie, "cookies": cookies}
    _config_path().write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return cfg


def build_headers():
    h = HEADERS.copy()
    cookie = get_cookie_string()
    if cookie:
        h["Cookie"] = cookie
    return h


# ─── Bilibili API ────────────────────────────────────────────────────────

def parse_api_response(resp: requests.Response, action: str) -> dict:
    """解析 API JSON 响应，并为失效端点/拦截页面提供可读错误。"""
    try:
        return resp.json()
    except ValueError as exc:
        content_type = resp.headers.get("Content-Type", "未知类型").split(";")[0]
        raise Exception(
            f"{action}失败: 接口返回 HTTP {resp.status_code} ({content_type})，不是有效 JSON"
        ) from exc


def get_video_info(bvid: str) -> dict:
    """获取视频基本信息"""
    url = f"{BASE_URL}/x/web-interface/view?bvid={bvid}"
    resp = requests.get(url, headers=build_headers(), timeout=15)
    data = resp.json()
    if data["code"] != 0:
        raise Exception(f"API 错误: {data.get('message', '未知错误')}")
    return data["data"]


def get_video_info_by_aid(aid: int) -> dict:
    """通过 aid 获取视频基本信息"""
    url = f"{BASE_URL}/x/web-interface/view?aid={aid}"
    resp = requests.get(url, headers=build_headers(), timeout=15)
    data = resp.json()
    if data["code"] != 0:
        raise Exception(f"API 错误: {data.get('message', '未知错误')}")
    return data["data"]


def get_playurl(aid: int, cid: int, qn: int = 80, fourk: bool = True) -> dict:
    """获取视频播放地址"""
    params = {
        "avid": aid, "cid": cid, "qn": qn,
        "fnval": 4048, "fnver": 0, "fourk": 1 if fourk else 0,
    }
    url = f"{BASE_URL}/x/player/playurl"
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = resp.json()
    if data["code"] != 0:
        raise Exception(f"获取播放地址失败: {data.get('message', '未知错误')}")
    return data["data"]


def get_season_info(ep_id: Optional[int] = None, season_id: Optional[int] = None,
                    is_cheese: bool = False) -> dict:
    """获取番剧/课程信息，ep_id 与 season_id 至少提供一个。

    Args:
        ep_id: 番剧/课程的 EP ID
        season_id: 番剧/课程的整季 SS ID
        is_cheese: 是否为 B站课程 (cheese)
    """
    if ep_id is None and season_id is None:
        raise Exception("获取系列信息需要 ep_id 或 season_id")
    params = {"ep_id": ep_id} if ep_id is not None else {"season_id": season_id}
    if is_cheese:
        url = f"{BASE_URL}/pugv/view/web/season"
    else:
        url = f"{BASE_URL}/pgc/view/web/season"
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取课程信息" if is_cheese else "获取番剧信息")
    if data["code"] != 0:
        type_name = "课程" if is_cheese else "番剧"
        raise Exception(f"获取{type_name}信息失败: {data.get('message', '未知错误')}")
    return data.get("data", data.get("result", {}))


def get_media_collection(url: str) -> dict:
    """根据课程或番剧 ep 链接获取所属系列及规范化集数列表。

    仅支持 ep 链接（/cheese/play/ep... 或 /bangumi/play/ep...）。
    对于 ss 链接，ep_id 由 extract_epid 返回 None，本函数会抛出异常。

    返回:
        {
            "media_type": "course" | "bangumi",
            "title": str,
            "current_ep_id": int,
            "episodes": [
                {
                    "ep_id": int,
                    "title": str,
                    "index": int,
                    "aid": int | None,
                    "cid": int | None,
                    "bvid": str | None,
                    "raw": dict,
                }
            ],
            "raw": dict,
        }
    """
    ep_id = extract_epid(url)
    if not ep_id:
        raise Exception("请使用课程或番剧的单集链接（包含 /ep 的链接），而非整季链接")

    is_cheese = "/cheese/" in url.lower()
    season = get_season_info(ep_id, is_cheese=is_cheese)

    media_type = "course" if is_cheese else "bangumi"
    title = season.get("title", season.get("season_title", "未知"))

    episodes_raw = season.get("episodes", [])
    episodes = []
    for idx, ep in enumerate(episodes_raw):
        if is_cheese:
            episodes.append({
                "ep_id": ep.get("id"),
                "title": ep.get("title", f"EP{idx + 1}"),
                "index": idx + 1,
                "aid": ep.get("aid"),
                "cid": ep.get("cid"),
                "bvid": None,
                "raw": ep,
            })
        else:
            episodes.append({
                "ep_id": ep.get("id") or ep.get("ep_id"),
                "title": (ep.get("long_title") or ep.get("share_copy")
                          or ep.get("title") or f"EP{idx + 1}"),
                "index": idx + 1,
                "aid": ep.get("aid"),
                "cid": ep.get("cid"),
                "bvid": ep.get("bvid"),
                "raw": ep,
            })

    return {
        "media_type": media_type,
        "title": title,
        "current_ep_id": ep_id,
        "episodes": episodes,
        "raw": season,
    }


def get_course_playurl(aid: int, cid: int, ep_id: int, qn: int = 80) -> dict:
    """获取已购课程单集播放地址。课程稿件不能通过普通视频接口播放。"""
    params = {
        "avid": aid, "cid": cid, "ep_id": ep_id, "qn": qn,
        "fnval": 4048, "fnver": 0, "fourk": 1,
    }
    url = f"{BASE_URL}/pugv/player/web/playurl"
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取课程播放地址")
    if data["code"] != 0:
        if data["code"] == -403:
            raise Exception(
                "获取课程播放地址失败: B站未向当前 Cookie 会话返回该课时的播放权限；"
                "请重新扫码登录，或导入网页已能播放此课时的完整 Cookie"
            )
        raise Exception(f"获取课程播放地址失败: {data.get('message', '未知错误')}")
    return data["data"]


def get_bangumi_playurl(aid: int, cid: int, ep_id: int, qn: int = 80) -> dict:
    """获取番剧单集播放地址（PGC 接口）。会员番剧需要走此接口而非普通 playurl。"""
    params = {
        "avid": aid, "cid": cid, "ep_id": ep_id, "qn": qn,
        "fnval": 4048, "fnver": 0, "fourk": 1,
    }
    url = f"{BASE_URL}/pgc/player/web/playurl"
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取番剧播放地址")
    if data["code"] != 0:
        if data["code"] == -403:
            raise Exception(
                "获取番剧播放地址失败: B站未向当前 Cookie 会话返回该集的播放权限；"
                "请确认已登录大会员账号，或导入已能播放此番剧的完整 Cookie"
            )
        raise Exception(f"获取番剧播放地址失败: {data.get('message', '未知错误')}")
    return data.get("result", data.get("data", {}))


def get_series_info(series_id: int) -> list:
    """获取合集列表"""
    url = f"{BASE_URL}/x/web-interface/arc/search?series_id={series_id}&type=series"
    resp = requests.get(url, headers=build_headers(), timeout=15)
    data = resp.json()
    if data["code"] != 0:
        raise Exception(f"获取合集信息失败: {data.get('message', '未知错误')}")
    return data["data"]["archives"]


# ─── 下载核心 ────────────────────────────────────────────────────────────

def download_stream(url: str, filepath: Path, desc: str = "",
                     stop_event=None, progress_callback=None, quiet: bool = False,
                     max_retries: int = 5) -> bool:
    """下载单个数据流, 支持断点续传"""
    temp_path = filepath.with_suffix(filepath.suffix + ".tmp")

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
                    temp_path.rename(filepath)
                    return True
                raise Exception("服务器拒绝断点范围请求 (HTTP 416)")

            if resp.status_code not in (200, 206):
                detail = resp.text[:120] if resp.text else ""
                raise Exception(f"HTTP {resp.status_code} {detail}".strip())

            if resume_size > 0 and resp.status_code == 200:
                # CDN 没接受 Range，重新下载该流，避免把完整响应追加到旧 tmp。
                resume_size = 0
                downloaded = 0

            total = int(resp.headers.get("content-length", 0)) + resume_size
            mode = "ab" if resume_size > 0 else "wb"

            if resume_size > 0 and not quiet:
                print(f"  断点续传: {resume_size / 1024 / 1024:.1f}MB / {total / 1024 / 1024:.1f}MB")
            if progress_callback and total:
                progress_callback(downloaded, total)

            with open(temp_path, mode) as f:
                last_print = 0
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if stop_event and stop_event.is_set():
                        if not quiet:
                            print("  ⏹ 已取消")
                        return False
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            if progress_callback:
                                progress_callback(downloaded, total)
                            if not quiet and (pct - last_print >= 2 or downloaded - last_print >= 2 * 1024 * 1024):
                                bar_len = 30
                                filled = int(bar_len * downloaded / total)
                                bar = "█" * filled + "░" * (bar_len - filled)
                                print(f"\r  {desc} [{bar}] {pct:.1f}% ({downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f}MB)", end="")
                                last_print = pct

            if not quiet:
                print()

            if not total or downloaded >= total:
                # total==0 时服务器未返回 Content-Length（如 chunked 传输），
                # 流正常结束即视为完整。
                temp_path.rename(filepath)
                return True

            raise Exception(f"连接提前结束: {downloaded}/{total} bytes")
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
                print(f"  ⚠ {desc} 中断，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}): {last_error}")
            time.sleep(delay)

    raise Exception(f"{desc or '数据流'}下载失败，已重试 {max_retries} 次: {last_error}")


def merge_video_audio(video_path: Path, audio_path: Path, output_path: Path) -> bool:
    """用 ffmpeg 合并视频和音频"""
    if output_path.exists():
        print(f"  ✓ {output_path.name} 已存在, 跳过合并")
        return True

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
        return True
    except FileNotFoundError:
        print("  ✗ 未找到 ffmpeg，无法合并音视频；请安装 FFmpeg 并确认其在 PATH 中")
        return False
    except subprocess.CalledProcessError as e:
        print(f"  ✗ 合并失败: {e.stderr.decode('utf-8', errors='replace')[:200]}")
        return False


# ─── 选择最佳画质/音质 ───────────────────────────────────────────────────

def select_best_quality(playurl_data: dict, target_qn: Optional[int] = None) -> tuple:
    """从 dash 数据中选择画质和音质。

    Args:
        target_qn: 期望的画质 qn 值；None 表示取最高画质。
            实际选取不超过 target_qn 的最高流（不存在时取最低流），
            否则用户选 480P 也会下回 4K。

    Returns:
        (video_stream, audio_stream)，找不到时对应项为 None。
    """
    dash = playurl_data.get("dash")
    if not dash:
        # flv 格式
        video_list = playurl_data.get("video", [])
        if video_list:
            return video_list[0], None
        return None, None

    # 视频流: 优先取不超过目标 qn 的最高流
    videos = dash.get("video", [])
    if not videos:
        return None, None
    if target_qn is None:
        best_video = max(videos, key=lambda v: v.get("id", 0))
    else:
        pool = [v for v in videos if v.get("id", 0) <= target_qn] or videos
        best_video = max(pool, key=lambda v: v.get("id", 0))

    # 音频流: 按 AUDIO_RANK 排序；Hi-Res/杜比不在 dash.audio 里，
    # 而是单独挂在 flac/dolby 字段下，需要合并后再选。
    audios = list(dash.get("audio", []))
    audios += dash.get("flac", {}).get("audio", []) or []
    audios += dash.get("dolby", {}).get("audio", []) or []
    best_audio = max(audios, key=lambda a: AUDIO_RANK.get(a.get("id", 0), -1),
                     default=None)

    return best_video, best_audio


def download_playurl_streams(title: str, part: str, playurl: dict, output_dir: Path,
                             quality: str = "1080P",
                             stop_event=None, progress_callback=None,
                             quiet: bool = False) -> bool:
    """保存已获得的播放流，用于普通视频与课程共用下载/合并流程。"""
    qn = QUALITY_MAP.get(quality, 80)
    video_stream, audio_stream = select_best_quality(playurl, target_qn=qn)

    if not video_stream:
        if not quiet:
            print("  ✗ 未找到可用视频流")
        return False

    vid_url = video_stream.get("base_url") or video_stream.get("baseUrl", "")
    aud_url = audio_stream.get("base_url") or audio_stream.get("baseUrl", "") if audio_stream else None

    if not vid_url:
        if not quiet:
            print("  ✗ 视频URL为空")
        return False

    # 需要合并时先确认 ffmpeg 可用，避免下完才发现装不了
    if aud_url and shutil.which("ffmpeg") is None:
        if not quiet:
            print("  ✗ 未检测到 ffmpeg（音视频合并必需），请安装 FFmpeg 后重试")
        return False

    video_dir = output_dir / title
    video_dir.mkdir(parents=True, exist_ok=True)

    ext = "m4s" if "dash" in playurl else "flv"
    vid_path = video_dir / f"{part}_video.{ext}"
    if not quiet:
        print("  📥 下载视频流...")
    if not vid_path.exists():
        if not download_stream(vid_url, vid_path, desc="视频",
                               stop_event=stop_event, progress_callback=progress_callback,
                               quiet=quiet):
            if stop_event and stop_event.is_set():
                return False
            if not quiet:
                print("  ✗ 视频下载失败")
            return False
    else:
        if not quiet:
            print("  ✓ 视频文件已存在, 跳过")

    if aud_url:
        aud_path = video_dir / f"{part}_audio.{ext}"
        if not quiet:
            print("  📥 下载音频流...")
        if not aud_path.exists():
            if not download_stream(aud_url, aud_path, desc="音频",
                                   stop_event=stop_event, progress_callback=progress_callback,
                                   quiet=quiet):
                if stop_event and stop_event.is_set():
                    return False
                if not quiet:
                    print("  ✗ 音频下载失败")
                return False
        else:
            if not quiet:
                print("  ✓ 音频文件已存在, 跳过")

        output_path = video_dir / f"{part}.mp4"
        if not quiet:
            print("  🔗 合并音视频...")
        if merge_video_audio(vid_path, aud_path, output_path):
            if not quiet:
                print(f"  ✓ 下载完成: {output_path}")
            return True
        if not quiet:
            print("  ⚠ 合并失败, 保留单独文件")
        return False

    output_path = video_dir / f"{part}.mp4"
    vid_path.rename(output_path)
    if not quiet:
        print(f"  ✓ 下载完成: {output_path}")
    return True


def download_single(bvid: str, output_dir: Path, quality: str = "1080P",
                    page: int = 0, stop_event=None, progress_callback=None,
                    quiet: bool = False) -> bool:
    """下载单个视频"""
    if not quiet:
        print(f"\n📺 获取视频信息: {bvid}")
    try:
        info = get_video_info(bvid)
    except Exception as e:
        if not quiet:
            print(f"  ✗ {e}")
        return False

    title = safe_filename(info["title"])
    aid = info["aid"]
    pages = info.get("pages", [])

    if pages and page > 1:
        # 下载指定分P
        idx = page - 1
        if idx >= len(pages):
            if not quiet:
                print(f"  ✗ 只有 {len(pages)} 个分P")
            return False
        p = pages[idx]
        cid = p["cid"]
        part = safe_filename(p.get("part", f"P{page}"))
    else:
        cid = info["cid"]
        part = title

    qn = QUALITY_MAP.get(quality, 80)
    if not quiet:
        print(f"  标题: {title}")
        print(f"  分P:  {part}")
        print(f"  画质: {quality} (qn={qn})")

    if not quiet:
        print(f"  获取播放地址...")
    playurl = get_playurl(aid, cid, qn)

    return download_playurl_streams(title, part, playurl, output_dir,
                                    quality=quality,
                                    stop_event=stop_event,
                                    progress_callback=progress_callback,
                                    quiet=quiet)


def download_course_episode(course: dict, episode: dict, output_dir: Path,
                             quality: str = "1080P", stop_event=None,
                             progress_callback=None, part_prefix: str = "",
                             quiet: bool = False) -> bool:
    """下载课程中的单个课时。"""
    ep_id = episode.get("id")
    aid = episode.get("aid")
    cid = episode.get("cid")
    if not all((ep_id, aid, cid)):
        raise Exception("课程课时缺少播放所需的 ep_id/aid/cid")

    title = safe_filename(course.get("title", "课程"))
    ep_title = safe_filename(episode.get("title", f"EP{ep_id}"))
    part = safe_filename(f"{part_prefix}{ep_title}") if part_prefix else ep_title
    qn = QUALITY_MAP.get(quality, 80)
    if not quiet:
        print(f"\n📺 获取课程课时: {part}")
        print(f"  课程: {title}")
        print(f"  画质: {quality} (qn={qn})")
        print("  获取课程播放地址...")
    playurl = get_course_playurl(aid, cid, ep_id, qn)
    return download_playurl_streams(title, part, playurl, output_dir,
                                    quality=quality,
                                    stop_event=stop_event,
                                    progress_callback=progress_callback,
                                    quiet=quiet)


def download_bangumi_episode(collection_title: str, episode: dict, output_dir: Path,
                              quality: str = "1080P", stop_event=None,
                              progress_callback=None, part_prefix: str = "",
                              quiet: bool = False) -> bool:
    """下载番剧中的单集。使用 PGC 播放接口以兼容会员番剧。"""
    ep_id = episode.get("ep_id")
    aid = episode.get("aid")
    cid = episode.get("cid")
    if not all((ep_id, aid, cid)):
        raise Exception("番剧集数缺少播放所需的 ep_id/aid/cid")

    title = safe_filename(collection_title or "番剧")
    ep_title = safe_filename(episode.get("title", f"EP{ep_id}"))
    part = safe_filename(f"{part_prefix}{ep_title}") if part_prefix else ep_title
    qn = QUALITY_MAP.get(quality, 80)
    if not quiet:
        print(f"\n📺 获取番剧: {part}")
        print(f"  系列: {title}")
        print(f"  画质: {quality} (qn={qn})")
        print("  获取番剧播放地址...")
    try:
        playurl = get_bangumi_playurl(aid, cid, ep_id, qn)
    except Exception:
        playurl = get_playurl(aid, cid, qn)
    return download_playurl_streams(title, part, playurl, output_dir,
                                    stop_event=stop_event,
                                    progress_callback=progress_callback,
                                    quiet=quiet)


def download_selected_episodes(collection: dict, selected_ep_ids: list,
                               output_dir: Path, quality: str = "1080P",
                               stop_event=None, progress_callback=None,
                               max_workers: int = 1,
                               episode_status_callback=None) -> dict:
    """下载选中的课程/番剧集数，支持有限并发。

    Args:
        collection: get_media_collection() 返回的系列数据
        selected_ep_ids: 选中的 ep_id 列表
        output_dir: 输出目录
        quality: 画质
        stop_event: 停止事件
        progress_callback: 字节级进度回调（仅单集内部使用）
        max_workers: 并发数，1=串行，最大 4
        episode_status_callback: 集级状态回调

    Returns:
        {
            "total": int,
            "success": int,
            "failed": int,
            "cancelled": bool,
            "cancelled_count": int,
            "failures": [{"ep_id": int, "title": str, "error": str}],
            "max_workers": int,
        }
    """
    max_workers = max(1, min(int(max_workers), 4))

    media_type = collection["media_type"]
    collection_title = collection["title"]
    all_eps = collection["episodes"]

    selected_set = set(selected_ep_ids)
    ordered_selected = [ep for ep in all_eps if ep["ep_id"] in selected_set]

    total = len(ordered_selected)
    if total == 0:
        return {"total": 0, "success": 0, "failed": 0,
                "cancelled": False, "cancelled_count": 0,
                "failures": [], "max_workers": max_workers}

    # 构建每集的 part_prefix 以避免文件名冲突
    def make_part_prefix(ep):
        return f"{ep['index']:03d}_"

    def download_one(ordinal: int, ep: dict) -> dict:
        ep_title = ep.get("title", f"EP{ep['ep_id']}")
        if stop_event and stop_event.is_set():
            return {"status": "cancelled", "ep_id": ep["ep_id"],
                    "title": ep_title, "ordinal": ordinal, "total": total, "error": ""}

        if episode_status_callback:
            episode_status_callback({"status": "started", "ep_id": ep["ep_id"],
                                    "index": ep["index"], "title": ep_title,
                                    "ordinal": ordinal, "total": total, "error": ""})

        try:
            prefix = make_part_prefix(ep)
            if media_type == "course":
                ok = download_course_episode(
                    collection["raw"], ep["raw"], output_dir, quality,
                    stop_event=stop_event, progress_callback=progress_callback,
                    part_prefix=prefix, quiet=True,
                )
            else:
                ok = download_bangumi_episode(
                    collection_title, ep, output_dir, quality,
                    stop_event=stop_event, progress_callback=progress_callback,
                    part_prefix=prefix, quiet=True,
                )

            if ok:
                if episode_status_callback:
                    episode_status_callback({"status": "completed", "ep_id": ep["ep_id"],
                                            "index": ep["index"], "title": ep_title,
                                            "ordinal": ordinal, "total": total, "error": ""})
                return {"status": "completed", "ep_id": ep["ep_id"],
                        "title": ep_title, "ordinal": ordinal, "total": total, "error": ""}
            else:
                if stop_event and stop_event.is_set():
                    return {"status": "cancelled", "ep_id": ep["ep_id"],
                            "title": ep_title, "ordinal": ordinal, "total": total, "error": ""}
                if episode_status_callback:
                    episode_status_callback({"status": "failed", "ep_id": ep["ep_id"],
                                            "index": ep["index"], "title": ep_title,
                                            "ordinal": ordinal, "total": total,
                                            "error": "下载未完成"})
                return {"status": "failed", "ep_id": ep["ep_id"],
                        "title": ep_title, "ordinal": ordinal, "total": total,
                        "error": "下载未完成"}
        except Exception as e:
            err_msg = str(e)
            if episode_status_callback:
                episode_status_callback({"status": "failed", "ep_id": ep["ep_id"],
                                        "index": ep["index"], "title": ep_title,
                                        "ordinal": ordinal, "total": total,
                                        "error": err_msg})
            return {"status": "failed", "ep_id": ep["ep_id"],
                    "title": ep_title, "ordinal": ordinal, "total": total,
                    "error": err_msg}

    results = []
    cancelled = False

    if max_workers <= 1:
        # 串行模式
        for i, ep in enumerate(ordered_selected, 1):
            if stop_event and stop_event.is_set():
                for remaining_ep in ordered_selected[i - 1:]:
                    results.append({"status": "cancelled", "ep_id": remaining_ep["ep_id"],
                                    "title": remaining_ep.get("title", ""),
                                    "ordinal": 0, "total": total, "error": ""})
                cancelled = True
                break
            result = download_one(i, ep)
            results.append(result)
            if result["status"] == "cancelled":
                cancelled = True
                for remaining_ep in ordered_selected[i:]:
                    results.append({"status": "cancelled", "ep_id": remaining_ep["ep_id"],
                                    "title": remaining_ep.get("title", ""),
                                    "ordinal": 0, "total": total, "error": ""})
                break
    else:
        # 并发模式：滑动窗口提交
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        pending_iter = iter(list(enumerate(ordered_selected, 1)))
        running_futures = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            def submit_next():
                try:
                    ordinal, ep = next(pending_iter)
                except StopIteration:
                    return
                if stop_event and stop_event.is_set():
                    results.append({"status": "cancelled", "ep_id": ep["ep_id"],
                                    "title": ep.get("title", ""),
                                    "ordinal": ordinal, "total": total, "error": ""})
                    return
                fut = executor.submit(download_one, ordinal, ep)
                running_futures[fut] = (ordinal, ep)

            # 初始提交 max_workers 个
            for _ in range(max_workers):
                submit_next()

            while running_futures:
                done_set, _ = wait(running_futures, return_when=FIRST_COMPLETED)
                for fut in done_set:
                    del running_futures[fut]
                    result = fut.result()
                    results.append(result)
                    if result["status"] == "cancelled":
                        cancelled = True
                    # 未停止则提交下一个
                    if not (stop_event and stop_event.is_set()):
                        submit_next()

        # 如果停止后仍有未提交的，标记为 cancelled
        if stop_event and stop_event.is_set():
            cancelled = True
            for ordinal, ep in pending_iter:
                results.append({"status": "cancelled", "ep_id": ep["ep_id"],
                                "title": ep.get("title", ""),
                                "ordinal": ordinal, "total": total, "error": ""})

    # 按原始集序排序结果
    ep_order = {ep["ep_id"]: i for i, ep in enumerate(all_eps)}
    results.sort(key=lambda r: ep_order.get(r["ep_id"], 999999))

    success = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] == "failed")
    cancelled_count = sum(1 for r in results if r["status"] == "cancelled")
    failures = [{"ep_id": r["ep_id"], "title": r["title"], "error": r["error"]}
                for r in results if r["status"] == "failed"]

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "cancelled": cancelled,
        "cancelled_count": cancelled_count,
        "failures": failures,
        "max_workers": max_workers,
    }


def download_batch(url_or_bvid: str, output_dir: Path, quality: str = "1080P",
                   stop_event=None, progress_callback=None):
    """自动识别并下载 (支持 BV / ep / ss / 合集 / 课程)"""
    # 解析 URL
    bvid = extract_bvid(url_or_bvid)
    ep_id = extract_epid(url_or_bvid)
    ss_id = extract_ssid(url_or_bvid)
    sid = extract_series_id(url_or_bvid)
    is_cheese = "/cheese/" in url_or_bvid.lower()

    # 处理合集
    if sid:
        print(f"\n📂 检测到合集 series_id={sid}")
        archives = get_series_info(sid)
        print(f"   共 {len(archives)} 个视频")
        for i, arc in enumerate(archives):
            if stop_event and stop_event.is_set():
                print("⏹ 已停止")
                return
            bv = arc["bvid"]
            p_title = safe_filename(arc.get("title", f"video_{i+1}"))
            print(f"\n[{i+1}/{len(archives)}] {p_title} ({bv})")
            download_single(bv, output_dir, quality, stop_event=stop_event, progress_callback=progress_callback)
        return

    # 处理番剧/课程
    if ep_id or ss_id:
        type_name = "课程" if is_cheese else "番剧"
        ident = f"ep_id={ep_id}" if ep_id else f"season_id={ss_id}"
        print(f"\n📺 检测到{type_name} {ident}")
        season = get_season_info(ep_id=ep_id, season_id=ss_id, is_cheese=is_cheese)
        episodes = season.get("episodes", [])
        season_title = season.get("title", season.get("season_title", "未知"))

        # ep 单集链接只下载该集（与 GUI 默认一致），ss 整季链接下载全部。
        # 整季模式加序号前缀，避免不同集同名文件互相覆盖。
        if ep_id:
            selected = next((ep for ep in episodes if ep.get("id") == ep_id), None)
            if selected is None:
                raise Exception(f"{type_name}中未找到 ep{ep_id}")
            batch = [(selected, "")]
        else:
            batch = [(ep, f"{i:03d}_") for i, ep in enumerate(episodes, 1)]
            print(f"   共 {len(batch)} 集")

        for i, (ep, prefix) in enumerate(batch, 1):
            if stop_event and stop_event.is_set():
                print("⏹ 已停止")
                return
            ep_title = safe_filename(
                ep.get("long_title") or ep.get("share_copy")
                or ep.get("title") or f"EP{i}")
            print(f"\n[{i}/{len(batch)}] {ep_title}")
            if is_cheese:
                ok = download_course_episode(season, ep, output_dir, quality,
                                             stop_event=stop_event,
                                             progress_callback=progress_callback,
                                             part_prefix=prefix)
            else:
                episode = {
                    "ep_id": ep.get("id") or ep.get("ep_id"),
                    "aid": ep.get("aid"),
                    "cid": ep.get("cid"),
                    "title": ep_title,
                }
                if not all((episode["ep_id"], episode["aid"], episode["cid"])):
                    # 数据缺 id 时回退到 bvid/aid 走普通视频接口
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
                    download_single(bv, output_dir, quality,
                                    stop_event=stop_event,
                                    progress_callback=progress_callback)
                    continue
                ok = download_bangumi_episode(season_title, episode, output_dir,
                                              quality,
                                              stop_event=stop_event,
                                              progress_callback=progress_callback,
                                              part_prefix=prefix)
            if not ok and not (stop_event and stop_event.is_set()):
                raise Exception(f"{type_name}下载未完成")
        return

    # 处理单个视频 (可能是多P)
    if bvid:
        info = get_video_info(bvid)
        pages = info.get("pages", [])
        if len(pages) > 1:
            print(f"\n📑 检测到多P视频: {len(pages)} 个分P")
            for i, p in enumerate(pages):
                if stop_event and stop_event.is_set():
                    print("⏹ 已停止")
                    return
                print(f"\n[{i+1}/{len(pages)}] {p.get('part', f'P{i+1}')}")
                download_single(bvid, output_dir, quality, page=i+1,
                                stop_event=stop_event, progress_callback=progress_callback)
        else:
            download_single(bvid, output_dir, quality,
                            stop_event=stop_event, progress_callback=progress_callback)
        return

    print("✗ 无法解析链接或BV号")


# ─── URL 解析 ────────────────────────────────────────────────────────────

def extract_bvid(text: str) -> Optional[str]:
    # 标准 BV 号为 "BV" + 10 位；(?![0-9A-Za-z]) 防止匹配到更长的字符串片段
    m = re.search(r'BV[0-9A-Za-z]{10}(?![0-9A-Za-z])', text)
    return m.group(0) if m else None


def extract_epid(text: str) -> Optional[int]:
    m = re.search(r'[?&]ep_id=(\d+)', text)
    if m:
        return int(m.group(1))
    m = re.search(r'/ep(\d+)', text)
    return int(m.group(1)) if m else None


def extract_ssid(text: str) -> Optional[int]:
    m = re.search(r'[?&]season_id=(\d+)', text)
    if m:
        return int(m.group(1))
    m = re.search(r'/ss(\d+)', text)
    return int(m.group(1)) if m else None


def extract_series_id(text: str) -> Optional[int]:
    m = re.search(r'series_id=(\d+)', text)
    return int(m.group(1)) if m else None


def parse_episode_ranges(text: str, max_index: int) -> list:
    """解析集数范围文本，返回去重升序的 1-based 索引列表。

    支持: "1-10,15,20-25"、中文逗号、空格分隔。
    """
    text = text.strip()
    if not text:
        raise ValueError("请输入集数范围，例如：1-10,15,20-25")
    text = text.replace("，", ",").replace("、", ",").replace(" ", ",")
    result = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            segments = part.split("-", 1)
            a_str, b_str = segments[0].strip(), segments[1].strip()
            if not a_str.isdigit() or not b_str.isdigit():
                raise ValueError(f"集数格式错误：'{part}'，请输入如 1-10")
            a, b = int(a_str), int(b_str)
            if a <= 0 or b <= 0:
                raise ValueError(f"集数必须为正整数：'{part}'")
            if a > b:
                raise ValueError(f"范围倒序无效：'{part}'，请写成 {b}-{a}")
            if b > max_index:
                raise ValueError(f"范围超出总集数 {max_index}：'{part}'")
            result.update(range(a, b + 1))
        else:
            if not part.isdigit():
                raise ValueError(f"集数格式错误：'{part}'，请输入正整数")
            n = int(part)
            if n <= 0:
                raise ValueError(f"集数必须为正整数：'{part}'")
            if n > max_index:
                raise ValueError(f"集数 {n} 超出总集数 {max_index}")
            result.add(n)
    if not result:
        raise ValueError("未解析到有效集数")
    return sorted(result)


# ─── 封面下载 ────────────────────────────────────────────────────────────

def download_cover(bvid: str, output_dir: Path):
    """下载视频封面"""
    try:
        info = get_video_info(bvid)
        pic = info.get("pic", "")
        if not pic:
            print("  ✗ 无封面信息")
            return
        title = safe_filename(info["title"])
        ext = Path(urllib.parse.urlparse(pic).path).suffix or ".jpg"
        path = output_dir / title / f"{title}_cover{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(pic, headers=HEADERS, timeout=15)
        path.write_bytes(resp.content)
        print(f"  ✓ 封面已保存: {path}")
    except Exception as e:
        print(f"  ✗ 封面下载失败: {e}")


# ─── 弹幕下载 ────────────────────────────────────────────────────────────

def download_danmaku(bvid: str, output_dir: Path):
    """下载 XML 弹幕"""
    try:
        info = get_video_info(bvid)
        cid = info["cid"]
        title = safe_filename(info["title"])
        url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        path = output_dir / title / f"{title}_danmaku.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        print(f"  ✓ 弹幕已保存: {path}")
    except Exception as e:
        print(f"  ✗ 弹幕下载失败: {e}")


# ─── 字幕下载 ────────────────────────────────────────────────────────────

def download_subtitle(bvid: str, output_dir: Path):
    """下载字幕 (SRT 格式)"""
    try:
        info = get_video_info(bvid)
        cid = info["cid"]
        aid = info["aid"]
        title = safe_filename(info["title"])

        url = f"{BASE_URL}/x/player/v2?aid={aid}&cid={cid}"
        resp = requests.get(url, headers=build_headers(), timeout=15)
        data = resp.json()
        subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        if not subtitles:
            print("  ℹ 无可用字幕")
            return

        out_dir = output_dir / title
        out_dir.mkdir(parents=True, exist_ok=True)

        for sub in subtitles:
            lang = sub.get("lan_doc", "未知")
            sub_url = sub.get("subtitle_url", "")
            if not sub_url:
                continue
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            resp2 = requests.get(sub_url, headers=HEADERS, timeout=15)
            sub_data = resp2.json()
            bodies = sub_data.get("body", [])
            srt_path = out_dir / f"{title}_{lang}.srt"
            with open(srt_path, "w", encoding="utf-8") as f:
                for i, item in enumerate(bodies, 1):
                    start = item["from"]
                    end = item["to"]
                    content = item["content"]
                    f.write(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{content}\n\n")
            print(f"  ✓ 字幕已保存: {srt_path.name}")
    except Exception as e:
        print(f"  ✗ 字幕下载失败: {e}")


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── 登录 ────────────────────────────────────────────────────────────────

def generate_qrcode():
    """生成二维码，返回字典或 None

    返回值: {"url": "qr_content_url", "qr_path": "图片路径", "key": "qrcode_key"}
    """
    import qrcode
    import tempfile

    resp = requests.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        headers=HEADERS, timeout=15
    ).json()
    if resp["code"] != 0:
        return None

    url = resp["data"]["url"]
    key = resp["data"]["qrcode_key"]

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_path = os.path.join(tempfile.gettempdir(), "bili_login_qr.png")
    img.save(img_path)
    return {"url": url, "qr_path": img_path, "key": key}


def poll_login_status(key):
    """轮询登录状态，返回结果字典

    返回值:
        {"status": "success", "sessdata": "..."} - 登录成功
        {"status": "waiting"} - 等待扫码
        {"status": "scanned"} - 已扫码等待确认
        {"status": "expired"} - 二维码已失效
    """
    response = requests.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
        params={"qrcode_key": key}, headers=HEADERS, timeout=15
    )
    poll = response.json()
    code = poll["data"]["code"]

    if code == 0:
        url_info = poll["data"]["url"]
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url_info)
        params = parse_qs(parsed.query)
        auth_keys = ("SESSDATA", "DedeUserID", "DedeUserID__ckMd5", "bili_jct", "sid")
        cookies = {
            key: params[key][0] for key in auth_keys
            if params.get(key)
        }
        cookies.update(response.cookies.get_dict())
        if cookies.get("SESSDATA"):
            cookie = "; ".join(f"{key}={value}" for key, value in cookies.items())
            cfg = save_cookie_string(cookie)
            return {
                "status": "success",
                "sessdata": cfg["sessdata"],
                "cookie_fields": sorted(cfg["cookies"].keys()),
            }
        return {"status": "failed", "message": "未能获取 SESSDATA"}
    elif code == -5:
        return {"status": "scanned"}
    elif code in (-4, 86038):
        return {"status": "expired"}
    else:
        return {"status": "waiting"}


def login_qrcode(gui_mode=False):
    """Bilibili 二维码登录

    Args:
        gui_mode: True 时返回二维码图片路径供 GUI 显示，False 时在终端打印

    Returns:
        gui_mode=True: {"qr_path": "...", "key": "..."} 或 None
        gui_mode=False: True/False
    """
    print("正在获取二维码...")

    result = generate_qrcode()
    if not result:
        print("获取二维码失败")
        return None if gui_mode else False

    if gui_mode:
        return result

    key = result["key"]
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(result["url"])
    qr.print_ascii(invert=True)
    print("\n请使用 Bilibili App 扫描上方二维码登录")
    print("(按 Ctrl+C 取消)\n")

    while True:
        try:
            result = poll_login_status(key)
            if result["status"] == "success":
                print("登录成功! 完整登录 Cookie 已保存到 config.json")
                return True
            elif result["status"] == "scanned":
                print("等待确认...")
            elif result["status"] == "expired":
                print("二维码已失效, 请重新运行 --login")
                return False
            else:
                sys.stdout.write("\r等待扫码...")
                sys.stdout.flush()
            time.sleep(1.5)
        except KeyboardInterrupt:
            print("\n已取消")
            return False


def check_login_status():
    """检查当前登录状态"""
    sess = get_sessdata()
    if not sess:
        print("未登录 (no SESSDATA)")
        return

    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=build_headers(), timeout=15
        ).json()
        if resp["code"] == 0:
            u = resp["data"]
            name = u.get("uname", "?")
            level = u.get("level_info", {}).get("current_level", 0)
            vip = u.get("vipStatus", 0) == 1
            coin = u.get("money", 0)
            print(f"已登录: {name}")
            print(f"  Lv.{level} {'大会员' if vip else '普通用户'}")
            print(f"  B币: {coin}")
        else:
            print(f"登录已失效: {resp.get('message', '未知')}")
    except Exception as e:
        print(f"检查失败: {e}")


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Bilibili 视频下载器 - 支持视频/番剧/课程/合集下载",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python bili_downloader.py --login                         # 二维码登录
  python bili_downloader.py --check-login                   # 检查登录状态
  python bili_downloader.py BV1xx411c7mD                    # 下载视频
  python bili_downloader.py "BV1xx411c7mD" -q 4K           # 指定画质
  python bili_downloader.py https://www.bilibili.com/bangumi/play/ss12345
  python bili_downloader.py BV1xx411c7mD --cover --danmaku  # 下载封面+弹幕
  python bili_downloader.py "BV1xx411c7mD" -p 2             # 下载第2P
        """
    )
    parser.add_argument("url", nargs="?", help="B站视频链接 或 BV号")
    parser.add_argument("-o", "--output", default="./downloads", help="输出目录 (默认: ./downloads)")
    parser.add_argument("-q", "--quality", default="1080P",
                        choices=list(QUALITY_MAP.keys()),
                        help="画质选择 (默认: 1080P)")
    parser.add_argument("-p", "--page", type=int, default=0, help="指定分P序号")
    parser.add_argument("--cover", action="store_true", help="同时下载封面")
    parser.add_argument("--danmaku", action="store_true", help="同时下载弹幕")
    parser.add_argument("--subtitle", action="store_true", help="同时下载字幕")
    parser.add_argument("--sessdata", help="设置 SESSDATA (登录凭证, 用于下载高清视频)")
    parser.add_argument("--cookie", help="导入浏览器完整 Cookie (课程鉴权推荐)")
    parser.add_argument("--login", action="store_true", help="二维码登录")
    parser.add_argument("--check-login", action="store_true", help="检查登录状态")

    args = parser.parse_args()

    # 二维码登录
    if args.login:
        login_qrcode()
        return

    # 检查登录状态
    if args.check_login:
        check_login_status()
        return

    # 设置登录凭证
    if args.sessdata:
        # 显式指定 SESSDATA 即整体替换凭证，避免旧完整 Cookie 抢占优先级
        cfg = {"sessdata": args.sessdata}
        _config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        print("SESSDATA 已保存到 config.json (已替换原有凭证)")
    if args.cookie:
        save_cookie_string(args.cookie)
        print("完整 Cookie 已保存到 config.json")

    if not args.url:
        parser.print_help()
        return

    output_dir = Path(args.output)

    bvid = extract_bvid(args.url)

    # 下载视频
    if args.page > 0 and bvid:
        download_single(bvid, output_dir, args.quality, args.page)
    else:
        download_batch(args.url, output_dir, args.quality)

    # 附加下载
    if bvid:
        if args.cover:
            print(f"\n🖼️ 下载封面...")
            download_cover(bvid, output_dir)
        if args.danmaku:
            print(f"\n💬 下载弹幕...")
            download_danmaku(bvid, output_dir)
        if args.subtitle:
            print(f"\n📝 下载字幕...")
            download_subtitle(bvid, output_dir)


if __name__ == "__main__":
    main()
