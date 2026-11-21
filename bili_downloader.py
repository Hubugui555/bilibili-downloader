#!/usr/bin/env python3
"""
BiliDown 2.0 — Bilibili Video Downloader (CLI & Backward-Compatible Core Facade)
Core implementation lives in the modular `bilidown` package.
"""

import io as _io
import sys
from pathlib import Path
from typing import Optional

import requests

# Windows GBK terminal compatibility
if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from bilidown import api as _api
from bilidown import cli as _cli
from bilidown import config as _config
from bilidown import constants as _constants
from bilidown import danmaku as _danmaku
from bilidown import engine as _engine
from bilidown import media as _media
from bilidown import parser as _parser

# ─── Public Constants ────────────────────────────────────────────────────
HEADERS = _constants.HEADERS
BASE_URL = _constants.BASE_URL
QUALITY_MAP = _constants.QUALITY_MAP
AUDIO_RANK = _constants.AUDIO_RANK
_SUBPROCESS_FLAGS = _constants.SUBPROCESS_FLAGS

# ─── URL / Filename Helpers ──────────────────────────────────────────────
safe_filename = _parser.safe_filename
extract_bvid = _parser.extract_bvid
extract_aid = _parser.extract_aid
extract_epid = _parser.extract_epid
extract_ssid = _parser.extract_ssid
extract_series_id = _parser.extract_series_id
parse_episode_ranges = _parser.parse_episode_ranges
resolve_short_url = _parser.resolve_short_url
parse_target = _parser.parse_target

# ─── Config & Credentials (patch-compatible with _config_path) ───────────
_config_path = _config.config_path


def _load_config() -> dict:
    return _config.load_config(path=_config_path())


def _save_config(cfg: dict) -> None:
    _config.save_config(cfg, path=_config_path())


def get_sessdata() -> str:
    return _config.get_sessdata(path=_config_path())


def get_cookie_string() -> str:
    return _config.get_cookie_string(path=_config_path())


def save_cookie_string(cookie: str) -> dict:
    return _config.save_cookie_string(cookie, path=_config_path())


def build_headers() -> dict:
    return _config.build_headers(path=_config_path())


# ─── API Functions ───────────────────────────────────────────────────────
parse_api_response = _api.parse_api_response
get_video_info = _api.get_video_info
get_video_info_by_aid = _api.get_video_info_by_aid
get_playurl = _api.get_playurl
get_season_info = _api.get_season_info
get_course_playurl = _api.get_course_playurl
get_bangumi_playurl = _api.get_bangumi_playurl
get_series_info = _api.get_series_info
generate_qrcode = _api.generate_qrcode
poll_login_status = _api.poll_login_status
inspect_url = _api.inspect_url


def get_media_collection(url: str) -> dict:
    return _api.get_media_collection(url, allow_season=False, season_fetcher=get_season_info)


# ─── Stream Selection & Media ────────────────────────────────────────────
select_best_quality = _media.select_best_quality
merge_video_audio = _media.merge_video_audio
_probe_video_size = _media.probe_video_size

# ─── Download Engine (patch-compatible hooks) ────────────────────────────
_stream_urls = _engine.stream_urls
_rank_stream_urls = _engine.rank_stream_urls
download_stream = _engine.download_stream
download_streams = _engine.download_streams
merge_downloaded_streams = _engine.merge_downloaded_streams
download_single = _engine.download_single
download_course_episode = _engine.download_course_episode
download_bangumi_episode = _engine.download_bangumi_episode
download_batch = _engine.download_batch


def download_playurl_streams(
    title: str,
    part: str,
    playurl: dict,
    output_dir: Path,
    quality: str = "1080P",
    stop_event=None,
    progress_callback=None,
    quiet: bool = False,
    defer_merge: bool = False,
    **kwargs,
):
    return _engine.download_playurl_streams(
        title,
        part,
        playurl,
        output_dir,
        quality=quality,
        stop_event=stop_event,
        progress_callback=progress_callback,
        quiet=quiet,
        defer_merge=defer_merge,
        stream_downloader=download_stream if download_stream is not _engine.download_stream else None,
        video_audio_merger=merge_video_audio if merge_video_audio is not _media.merge_video_audio else None,
        **kwargs,
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
    **kwargs,
) -> dict:
    return _engine.download_selected_episodes(
        collection,
        selected_ep_ids,
        output_dir,
        quality=quality,
        stop_event=stop_event,
        progress_callback=progress_callback,
        max_workers=max_workers,
        episode_status_callback=episode_status_callback,
        danmaku=danmaku,
        subtitle=subtitle,
        course_downloader=(
            download_course_episode
            if download_course_episode is not _engine.download_course_episode
            else None
        ),
        bangumi_downloader=(
            download_bangumi_episode
            if download_bangumi_episode is not _engine.download_bangumi_episode
            else None
        ),
        stream_merger=(
            merge_downloaded_streams
            if merge_downloaded_streams is not _engine.merge_downloaded_streams
            else None
        ),
        **kwargs,
    )


# ─── Cover, Danmaku & Subtitle ───────────────────────────────────────────
download_cover = _danmaku.download_cover
download_danmaku = _danmaku.download_danmaku
_subtitle_priority = _danmaku.subtitle_priority
_fetch_subtitle_list = _api.fetch_subtitle_list
_srt_time = _danmaku.srt_time
_ass_time = _danmaku.ass_time
_ass_escape = _danmaku.ass_escape
_ass_colour = _danmaku.ass_colour
_text_width = _danmaku.text_width
_parse_danmaku_xml = _danmaku.parse_danmaku_xml
_parse_srt = _danmaku.parse_srt
build_combined_ass = _danmaku.build_combined_ass
mux_combined_subtitle = _danmaku.mux_combined_subtitle
mux_subtitle_mp4 = _danmaku.mux_subtitle_mp4
merge_extras_into_video = _danmaku.merge_extras_into_video
download_extras_and_merge = _danmaku.download_extras_and_merge
download_episode_extras = _danmaku.download_episode_extras


def download_subtitle(
    bvid: Optional[str] = None,
    output_dir: Optional[Path] = None,
    info: Optional[dict] = None,
    *,
    aid: Optional[int] = None,
    cid: Optional[int] = None,
    base_name: Optional[str] = None,
    quiet: bool = False,
    all_languages: bool = False,
) -> list:
    return _danmaku.download_subtitle(
        bvid=bvid,
        output_dir=output_dir,
        info=info,
        aid=aid,
        cid=cid,
        base_name=base_name,
        quiet=quiet,
        all_languages=all_languages,
        subtitle_fetcher=_fetch_subtitle_list,
        http_get=requests.get,
    )


# ─── CLI & Auth ──────────────────────────────────────────────────────────
login_qrcode = _cli.login_qrcode
check_login_status = _cli.check_login_status
main = _cli.main

if __name__ == "__main__":
    main()
