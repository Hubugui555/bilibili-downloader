"""Cover, Danmaku XML -> ASS conversion, Subtitle SRT download, and FFmpeg soft-muxing."""

import html
import os
import re
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from .api import fetch_subtitle_list, get_video_info
from .config import build_headers
from .constants import HEADERS
from .media import probe_video_size, run_ffmpeg
from .parser import safe_filename

_DANMAKU_TAG_RE = re.compile(r'<d\s+p="([^"]*)"[^>]*>(.*?)</d>', re.S)
_SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


# ─── Cover ───────────────────────────────────────────────────────────────

def download_cover(
    bvid: Optional[str] = None,
    output_dir: Optional[Path] = None,
    info: Optional[dict] = None,
    *,
    cover_url: Optional[str] = None,
    base_name: Optional[str] = None,
    quiet: bool = False,
) -> Optional[Path]:
    """Download video/collection cover image; return saved Path or None."""
    try:
        if cover_url and base_name and output_dir is not None:
            pic = cover_url
            title = safe_filename(base_name)
            ext = Path(urllib.parse.urlparse(pic).path).suffix or ".jpg"
            path = Path(output_dir) / f"{title}_cover{ext}"
        else:
            info = info or get_video_info(bvid)
            pic = info.get("pic", "")
            if not pic:
                if not quiet:
                    print("  ✗ 无封面信息")
                return None
            title = safe_filename(base_name or info["title"])
            ext = Path(urllib.parse.urlparse(pic).path).suffix or ".jpg"
            path = Path(output_dir) / title / f"{title}_cover{ext}"

        if pic.startswith("//"):
            pic = "https:" + pic
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(pic, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            raise RuntimeError("封面响应为空")
        path.write_bytes(resp.content)
        if not quiet:
            print(f"  ✓ 封面已保存: {path.name}")
        return path
    except Exception as e:
        if not quiet:
            print(f"  ✗ 封面下载失败: {e}")
        return None


# ─── Danmaku XML ─────────────────────────────────────────────────────────

def download_danmaku(
    bvid: Optional[str] = None,
    output_dir: Optional[Path] = None,
    info: Optional[dict] = None,
    *,
    cid: Optional[int] = None,
    base_name: Optional[str] = None,
    quiet: bool = False,
) -> Optional[Path]:
    """Download Bilibili XML danmaku; return saved Path or None."""
    try:
        if info is not None or bvid:
            info = info or get_video_info(bvid)
            cid = cid or info["cid"]
            title = safe_filename(base_name or info["title"])
            target_dir = Path(output_dir) / title if base_name is None else Path(output_dir)
        else:
            if not cid or not base_name:
                raise RuntimeError("缺少 cid/base_name")
            title = safe_filename(base_name)
            target_dir = Path(output_dir)

        url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        if b"<i>" not in resp.content[:1000] and b"<d p=" not in resp.content[:2000]:
            raise RuntimeError("接口未返回有效的弹幕 XML")
        path = target_dir / f"{title}_danmaku.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        if not quiet:
            print(f"  ✓ 弹幕已保存: {path.name}")
        return path
    except Exception as e:
        if not quiet:
            print(f"  ✗ 弹幕下载失败: {e}")
        return None


# ─── Subtitles (SRT) ─────────────────────────────────────────────────────

def subtitle_priority(sub: dict) -> tuple[int, int]:
    """Priority order: manual Chinese > AI Chinese > other Chinese > other languages."""
    lan = (sub.get("lan") or "").lower()
    if lan in ("zh-hans", "zh-cn", "zh"):
        return (0, 0)
    if lan == "ai-zh":
        return (1, 0)
    if lan.startswith("zh"):
        return (2, 0)
    if lan.startswith("ai-zh"):
        return (2, 1)
    return (3, 0)


def srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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
    subtitle_fetcher=None,
    http_get=None,
) -> list[Path]:
    """Download subtitle(s) as SRT. By default saves highest-priority Chinese track."""
    saved: list[Path] = []
    fetcher = subtitle_fetcher or fetch_subtitle_list
    getter = http_get or requests.get
    try:
        if info is not None or bvid:
            info = info or get_video_info(bvid)
            aid = aid or info["aid"]
            cid = cid or info["cid"]
            title = safe_filename(base_name or info["title"])
            out_dir = Path(output_dir) / title if base_name is None else Path(output_dir)
        else:
            if not aid or not cid or not base_name:
                raise RuntimeError("缺少 aid/cid/base_name")
            title = safe_filename(base_name)
            out_dir = Path(output_dir)

        subtitles = [
            s for s in fetcher(aid, cid)
            if s.get("subtitle_url") and not s.get("is_lock")
        ]
        if not subtitles:
            if not quiet:
                print("  ℹ 无可用字幕")
            return saved
        subtitles.sort(key=subtitle_priority)

        out_dir.mkdir(parents=True, exist_ok=True)
        for idx, sub in enumerate(subtitles):
            lan = sub.get("lan") or "unknown"
            label = sub.get("lan_doc") or lan
            if lan.startswith("ai-"):
                label += "（AI 自动生成）"
            sub_url = sub["subtitle_url"]
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            try:
                resp = getter(sub_url, headers=build_headers(), timeout=20)
                resp.raise_for_status()
                bodies = resp.json().get("body") or []
            except Exception as e:
                if not quiet:
                    print(f"  ✗ 字幕 {label} 下载失败: {e}")
                continue
            if not bodies:
                continue

            if idx == 0:
                srt_path = out_dir / f"{title}.srt"
                if srt_path.exists() and srt_path.stat().st_size > 0:
                    srt_path = out_dir / f"{title}_{safe_filename(lan)}.srt"
            else:
                srt_path = out_dir / f"{title}_{safe_filename(lan)}.srt"

            with open(srt_path, "w", encoding="utf-8") as f:
                for i, item in enumerate(bodies, 1):
                    start = item["from"]
                    end = item["to"]
                    content = item["content"]
                    f.write(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n{content}\n\n")
            if not quiet:
                print(f"  ✓ 字幕已保存: {srt_path.name} [{label}]")
            saved.append(srt_path)
            if not all_languages:
                break
    except Exception as e:
        if not quiet:
            print(f"  ✗ 字幕下载失败: {e}")
    return saved


# ─── ASS Conversion & Muxing ─────────────────────────────────────────────

def ass_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    text = text.replace("\r", "").replace("\n", "\\N")
    return text.replace("{", "\\{").replace("}", "\\}")


def ass_colour(rgb: int) -> str:
    """Convert Bilibili decimal RGB (0xRRGGBB) to ASS BGR (&H00BBGGRR&)."""
    rgb &= 0xFFFFFF
    r = (rgb >> 16) & 0xFF
    g = (rgb >> 8) & 0xFF
    b = rgb & 0xFF
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def _ass_alpha(opacity: float) -> str:
    """Convert opacity (0.0 transparent .. 1.0 opaque) to ASS hex alpha (FF..00)."""
    op = max(0.1, min(1.0, float(opacity)))
    alpha = int(round((1.0 - op) * 255))
    return f"{alpha:02X}"


def text_width(text: str, font_size: int) -> int:
    units = sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in text)
    return max(1, int(units * font_size))


def parse_danmaku_xml(xml_path: Path) -> list[tuple[float, int, int, int, str]]:
    try:
        raw = Path(xml_path).read_bytes().decode("utf-8", errors="replace")
    except Exception:
        return []

    items = []
    for match in _DANMAKU_TAG_RE.finditer(raw):
        fields = match.group(1).split(",")
        if len(fields) < 4:
            continue
        try:
            start = float(fields[0])
            mode = int(fields[1])
            size = int(float(fields[2]))
            colour = int(fields[3])
        except ValueError:
            continue
        text = html.unescape(match.group(2)).strip()
        if not text or mode in (7, 8):
            continue
        items.append((start, mode, size, colour, text))
    items.sort(key=lambda x: x[0])
    return items


def parse_srt(srt_path: Path) -> list[tuple[float, float, str]]:
    try:
        content = Path(srt_path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return []

    events = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        time_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_idx is None:
            continue
        m = _SRT_TIME_RE.search(lines[time_idx])
        if not m:
            continue
        start = (
            int(m.group(1)) * 3600
            + int(m.group(2)) * 60
            + int(m.group(3))
            + int(m.group(4).ljust(3, "0")) / 1000
        )
        end = (
            int(m.group(5)) * 3600
            + int(m.group(6)) * 60
            + int(m.group(7))
            + int(m.group(8).ljust(3, "0")) / 1000
        )
        text = html.unescape("\n".join(lines[time_idx + 1:])).strip()
        if text and end > start:
            events.append((start, end, text))
    return events


def build_combined_ass(
    danmaku_xml: Optional[Path],
    subtitle_files: list[Path],
    ass_path: Path,
    width: int = 1920,
    height: int = 1080,
    *,
    area: float = 0.85,
    opacity: float = 0.85,
    font_scale: float = 1.0,
    speed: float = 1.0,
    font_name: str = "Microsoft YaHei",
) -> Path:
    """Merge Bilibili danmaku XML + SRT subtitles into a single styled ASS track."""
    scale = (height / 540.0) * max(0.5, min(2.0, float(font_scale)))
    base_size = max(24, min(76, int(25 * scale)))
    line_height = max(28, int(base_size * 1.35))
    top_margin = max(8, int(height * 0.02))
    subtitle_reserve = max(80, int(height * 0.14))
    sub_size = max(20, int(height * 0.05))
    sub_margin_v = max(12, int(height * 0.04))

    max_usable = max(line_height, height - subtitle_reserve - top_margin)
    area_ratio = max(0.25, min(1.0, float(area)))
    usable_height = max(line_height, int(max_usable * area_ratio))
    lane_count = max(1, usable_height // line_height)
    dm_alpha = _ass_alpha(opacity)
    speed_factor = max(0.5, min(2.0, float(speed)))

    header = f"""[Script Info]
; BiliDown Studio 合并字幕轨（弹幕 + 字幕）
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Danmaku,{font_name},{base_size},&H{dm_alpha}FFFFFF,&H{dm_alpha}FFFFFF,&H{dm_alpha}000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,7,0,0,0,1
Style: Subtitle,{font_name},{sub_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,{max(20, int(width * 0.04))},{max(20, int(width * 0.04))},{sub_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    scroll_free = [0.0] * lane_count
    top_free = [0.0] * lane_count
    bottom_free = [0.0] * lane_count

    def take_lane(free_times: list[float], start_t: float) -> int:
        for i, free in enumerate(free_times):
            if free <= start_t:
                return i
        return min(range(len(free_times)), key=lambda i: free_times[i])

    for start, mode, size, colour, text in (
        parse_danmaku_xml(danmaku_xml) if danmaku_xml else []
    ):
        font_size = max(16, min(96, int(size * scale)))
        t_width = text_width(text, font_size)
        style_tag = f"\\c{ass_colour(colour)}\\fs{font_size}"

        if mode in (1, 2, 3, 6):
            base_dur = (width + t_width) / ((width / 8.0) * speed_factor)
            duration = min(14.0, max(4.0, base_dur))
            lane = take_lane(scroll_free, start)
            scroll_free[lane] = start + duration * 0.75
            y = top_margin + lane * line_height
            if mode == 6:
                move = f"\\move({-t_width},{y},{width},{y})"
            else:
                move = f"\\move({width},{y},{-t_width},{y})"
            tag = f"{{\\an7{style_tag}{move}}}"
        elif mode == 5:
            duration = 5.0
            lane = take_lane(top_free, start)
            top_free[lane] = start + duration
            y = top_margin + lane * line_height
            tag = f"{{\\an8\\pos({width // 2},{y}){style_tag}}}"
        elif mode == 4:
            duration = 5.0
            lane = take_lane(bottom_free, start)
            bottom_free[lane] = start + duration
            y = height - subtitle_reserve - lane * line_height
            tag = f"{{\\an2\\pos({width // 2},{y}){style_tag}}}"
        else:
            continue

        events.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(start + duration)},"
            f"Danmaku,,0,0,0,,{tag}{ass_escape(text)}\n"
        )

    for srt_path in subtitle_files or []:
        for start, end, text in parse_srt(srt_path):
            events.append(
                f"Dialogue: 1,{ass_time(start)},{ass_time(end)},"
                f"Subtitle,,0,0,0,,{ass_escape(text)}\n"
            )

    ass_path = Path(ass_path)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(header + "".join(events), encoding="utf-8-sig")
    return ass_path


def mux_combined_subtitle(video_path: Path, ass_path: Path, output_path: Path) -> bool:
    ok, _ = run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(ass_path),
            "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
            "-c", "copy", "-c:s", "ass",
            "-metadata:s:s:0", "language=chi",
            "-metadata:s:s:0", "title=弹幕/字幕",
            "-disposition:s:0", "default",
            str(output_path),
        ]
    )
    return ok


def mux_subtitle_mp4(
    video_path: Path, subtitle_files: list[Path], output_path: Path
) -> bool:
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    for srt_path in subtitle_files:
        cmd += ["-i", str(srt_path)]
    cmd += ["-map", "0"]
    for i in range(len(subtitle_files)):
        cmd += ["-map", f"{i + 1}:0"]
    cmd += ["-c", "copy", "-c:s", "mov_text"]
    for i, srt_path in enumerate(subtitle_files):
        cmd += [
            f"-metadata:s:s:{i}", "language=chi",
            f"-metadata:s:s:{i}", f"title={srt_path.stem}",
        ]
    cmd += ["-movflags", "+faststart", str(output_path)]
    ok, _ = run_ffmpeg(cmd)
    return ok


def merge_extras_into_video(
    video_dir: Path,
    title: str,
    danmaku_xml: Optional[Path] = None,
    subtitle_files: Optional[list[Path]] = None,
    quiet: bool = False,
    danmaku_style: Optional[dict] = None,
) -> Optional[Path]:
    """Soft-mux danmaku/subtitles into {title}.mp4 (MKV if danmaku present, else MP4)."""
    video_dir = Path(video_dir)
    subtitle_files = [Path(p) for p in (subtitle_files or []) if p][:1]
    mp4_path = video_dir / f"{title}.mp4"
    mkv_path = video_dir / f"{title}.mkv"

    if mkv_path.exists():
        if not quiet:
            print(f"  ✓ 合并文件已存在: {mkv_path.name}")
        return mkv_path
    if not mp4_path.exists():
        if not quiet:
            print("  ℹ 未找到待合并的视频文件，跳过合并")
        return None

    style = danmaku_style or {}
    if danmaku_xml:
        width, height = probe_video_size(mp4_path)
        ass_path = video_dir / f"{title}_弹幕字幕.ass"
        build_combined_ass(
            danmaku_xml,
            subtitle_files,
            ass_path,
            width,
            height,
            area=style.get("danmaku_area", 0.55),
            opacity=style.get("danmaku_opacity", 0.85),
            font_scale=style.get("danmaku_font_scale", 1.0),
            speed=style.get("danmaku_speed", 1.0),
        )
        if mux_combined_subtitle(mp4_path, ass_path, mkv_path):
            try:
                mp4_path.unlink(missing_ok=True)
            except OSError as e:
                if not quiet:
                    print(f"  ⚠ 合并完成，但原 MP4 无法删除: {e}")
            if not quiet:
                print(f"  ✓ 弹幕/字幕已合并: {mkv_path.name}")
            return mkv_path
        try:
            mkv_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    if subtitle_files:
        tmp_path = video_dir / f"{title}.merge_tmp.mp4"
        if mux_subtitle_mp4(mp4_path, subtitle_files, tmp_path):
            try:
                os.replace(tmp_path, mp4_path)
            except OSError as e:
                if not quiet:
                    print(f"  ✗ 替换视频文件失败（可能被播放器占用）: {e}")
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
            if not quiet:
                print(f"  ✓ 字幕已合并: {mp4_path.name}")
            return mp4_path
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    return None


def download_extras_and_merge(
    bvid: str,
    output_dir: Path,
    *,
    cover: bool = False,
    danmaku: bool = False,
    subtitle: bool = False,
    merge: bool = True,
    quiet: bool = False,
    danmaku_style: Optional[dict] = None,
    all_subtitles: bool = False,
    info: Optional[dict] = None,
    base_name: Optional[str] = None,
    cid: Optional[int] = None,
    video_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Download cover/danmaku/subtitles for a video and optionally soft-mux them."""
    output_dir = Path(output_dir)
    try:
        info = info or get_video_info(bvid)
    except Exception as e:
        if not quiet:
            print(f"  ✗ 获取视频信息失败: {e}")
        return None

    title = safe_filename(base_name or info["title"])
    vdir = Path(video_dir) if video_dir is not None else (output_dir / safe_filename(info["title"]))
    target_cid = cid or info["cid"]

    if cover:
        download_cover(
            cover_url=info.get("pic", ""),
            output_dir=vdir,
            base_name=title,
            quiet=quiet,
        )
    danmaku_xml = (
        download_danmaku(cid=target_cid, output_dir=vdir, base_name=title, quiet=quiet)
        if danmaku
        else None
    )
    subtitle_files = (
        download_subtitle(
            aid=info["aid"],
            cid=target_cid,
            output_dir=vdir,
            base_name=title,
            quiet=quiet,
            all_languages=all_subtitles,
        )
        if subtitle
        else []
    )

    if not merge or not (danmaku_xml or subtitle_files):
        return None
    return merge_extras_into_video(
        vdir, title, danmaku_xml, subtitle_files, quiet=quiet, danmaku_style=danmaku_style
    )


def download_episode_extras(
    video_dir: Path,
    base_name: str,
    aid: int,
    cid: int,
    *,
    danmaku: bool = False,
    subtitle: bool = False,
    merge: bool = True,
    quiet: bool = False,
    danmaku_style: Optional[dict] = None,
    all_subtitles: bool = False,
) -> Optional[Path]:
    """Download danmaku/subtitles for a course/bangumi/multi-P episode and soft-mux."""
    video_dir = Path(video_dir)
    base_name = safe_filename(base_name)
    danmaku_xml = (
        download_danmaku(cid=cid, output_dir=video_dir, base_name=base_name, quiet=quiet)
        if danmaku
        else None
    )
    subtitle_files = (
        download_subtitle(
            aid=aid,
            cid=cid,
            output_dir=video_dir,
            base_name=base_name,
            quiet=quiet,
            all_languages=all_subtitles,
        )
        if subtitle
        else []
    )
    if not merge or not (danmaku_xml or subtitle_files):
        return None
    return merge_extras_into_video(
        video_dir, base_name, danmaku_xml, subtitle_files, quiet=quiet, danmaku_style=danmaku_style
    )
