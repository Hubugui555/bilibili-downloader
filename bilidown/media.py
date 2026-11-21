"""Stream selection, FFmpeg muxing, and audio extraction utilities."""

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .constants import (
    AUDIO_RANK,
    CODEC_MAP,
    CODEC_PRIORITY_AUTO,
    SUBPROCESS_FLAGS,
)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def ffmpeg_version() -> str:
    if not ffmpeg_available():
        return ""
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            **SUBPROCESS_FLAGS,
        ).stdout
        first = out.splitlines()[0] if out else ""
        return first.strip()
    except Exception:
        return ""


def select_best_quality(
    playurl_data: dict,
    target_qn: Optional[int] = None,
    codec: str = "auto",
) -> tuple[Optional[dict], Optional[dict]]:
    """Select best video and audio streams from playurl DASH or FLV payload.

    Args:
        playurl_data: Bilibili playurl response `data` dict.
        target_qn: Desired quality qn cap; None picks maximum available.
        codec: Preferred video codec: 'auto', 'avc', 'hevc', or 'av1'.
    """
    dash = playurl_data.get("dash")
    if not dash:
        video_list = playurl_data.get("video", [])
        if video_list:
            return video_list[0], None
        durl = playurl_data.get("durl", [])
        if durl:
            first = dict(durl[0])
            if "url" in first and "base_url" not in first:
                first["base_url"] = first["url"]
            return first, None
        return None, None

    videos = dash.get("video") or []
    if not videos:
        best_video = None
    else:
        if target_qn is None:
            best_qn = max(v.get("id", 0) for v in videos)
        else:
            eligible = [v for v in videos if v.get("id", 0) <= target_qn] or videos
            best_qn = max(v.get("id", 0) for v in eligible)

        same_qn = [v for v in videos if v.get("id", 0) == best_qn]
        preferred_cid = CODEC_MAP.get((codec or "auto").lower())
        if preferred_cid is not None:
            matched = [v for v in same_qn if v.get("codecid") == preferred_cid]
            if matched:
                same_qn = matched

        best_video = max(
            same_qn,
            key=lambda v: (
                CODEC_PRIORITY_AUTO.get(v.get("codecid", 0), 0),
                v.get("bandwidth", 0),
            ),
        )

    # Merge audio pools (standard + Hi-Res FLAC + Dolby Atmos); guard null fields
    audios = list(dash.get("audio") or [])
    audios += list((dash.get("flac") or {}).get("audio") or [])
    audios += list((dash.get("dolby") or {}).get("audio") or [])
    best_audio = max(
        audios,
        key=lambda a: (AUDIO_RANK.get(a.get("id", 0), -1), a.get("bandwidth", 0)),
        default=None,
    )

    return best_video, best_audio


def run_ffmpeg(cmd: list[str], quiet: bool = False) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, **SUBPROCESS_FLAGS)
    except FileNotFoundError:
        msg = "未找到 ffmpeg，请安装 FFmpeg 并确认其在 PATH 中"
        if not quiet:
            print(f"  ✗ {msg}")
        return False, msg
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        short = err[-300:] if len(err) > 300 else err
        if not quiet:
            print(f"  ✗ ffmpeg 处理失败: {short}")
        return False, short
    return True, ""


def merge_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    metadata: Optional[dict] = None,
    quiet: bool = False,
) -> bool:
    """Losslessly remux video and audio streams into output_path using FFmpeg."""
    if output_path.exists():
        if not quiet:
            print(f"  ✓ {output_path.name} 已存在, 跳过合并")
        for leftover in (video_path, audio_path):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "copy",
    ]
    if metadata:
        for key in ("title", "artist", "comment"):
            val = metadata.get(key)
            if val:
                cmd += ["-metadata", f"{key}={val}"]
    cmd += ["-movflags", "+faststart", str(output_path)]

    ok, _ = run_ffmpeg(cmd, quiet=quiet)
    if ok:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
    return ok


def extract_audio(
    audio_path: Path,
    output_path: Path,
    audio_format: str = "m4a",
    *,
    cover_path: Optional[Path] = None,
    metadata: Optional[dict] = None,
    quiet: bool = False,
) -> bool:
    """Convert or remux a Bilibili DASH audio stream to M4A, MP3, or FLAC with tags & cover."""
    fmt = (audio_format or "m4a").lower()
    if fmt not in ("m4a", "mp3", "flac"):
        fmt = "m4a"

    if output_path.exists():
        if not quiet:
            print(f"  ✓ {output_path.name} 已存在, 跳过提取")
        audio_path.unlink(missing_ok=True)
        return True

    has_cover = bool(cover_path and Path(cover_path).exists())
    cmd = ["ffmpeg", "-y", "-i", str(audio_path)]
    if has_cover:
        cmd += ["-i", str(cover_path)]

    if fmt == "mp3":
        cmd += ["-map", "0:a:0"]
        if has_cover:
            cmd += ["-map", "1:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
        cmd += ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"]
    elif fmt == "flac":
        cmd += ["-map", "0:a:0"]
        if has_cover:
            cmd += ["-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        cmd += ["-c:a", "flac"]
    else:  # m4a
        cmd += ["-map", "0:a:0"]
        if has_cover:
            cmd += ["-map", "1:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
        cmd += ["-c:a", "copy", "-movflags", "+faststart"]

    if metadata:
        for key in ("title", "artist", "album", "comment"):
            val = metadata.get(key)
            if val:
                cmd += ["-metadata", f"{key}={val}"]

    cmd.append(str(output_path))
    ok, _ = run_ffmpeg(cmd, quiet=quiet)

    # Fallback: if m4a stream copy fails (e.g. FLAC/Dolby inside m4s), re-encode to AAC
    if not ok and fmt == "m4a":
        cmd_fb = ["ffmpeg", "-y", "-i", str(audio_path), "-map", "0:a:0", "-c:a", "aac", "-b:a", "256k", str(output_path)]
        ok, _ = run_ffmpeg(cmd_fb, quiet=quiet)

    if ok:
        audio_path.unlink(missing_ok=True)
    return ok


def probe_video_size(video_path: Path) -> tuple[int, int]:
    """Probe video resolution via ffprobe; defaults to 1920x1080 on error."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            **SUBPROCESS_FLAGS,
        ).stdout.strip()
        width, height = out.split("x")[:2]
        if int(width) > 0 and int(height) > 0:
            return int(width), int(height)
    except Exception:
        pass
    return 1920, 1080
