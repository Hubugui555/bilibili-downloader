"""URL, ID, short-link, episode-range, and filename parsing utilities."""

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from .constants import COVER_ALLOWED_HOSTS, HEADERS

_SHORT_LINK_RE = re.compile(r"https?://(?:b23\.tv|bili2233\.cn)/[0-9A-Za-z]+")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"'\]）】》]+")


def safe_filename(name: str, max_len: int = 160) -> str:
    """Sanitize a filename safely for Windows/Linux/macOS."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip().rstrip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "untitled"


def format_filename(
    template: str,
    *,
    title: str,
    index: Optional[int] = None,
    owner: str = "",
    bvid: str = "",
    part: str = "",
    quality: str = "",
) -> str:
    """Render a user-configurable filename template safely."""
    tpl = (template or "{title}").strip() or "{title}"
    idx_str = f"{index:03d}" if index is not None else ""
    mapping = {
        "title": safe_filename(title),
        "part": safe_filename(part or title),
        "index": idx_str,
        "owner": safe_filename(owner) if owner else "",
        "bvid": bvid or "",
        "quality": quality or "",
    }
    try:
        rendered = tpl.format(**mapping)
    except (KeyError, ValueError, IndexError):
        rendered = f"{idx_str}_{title}" if idx_str else title
    rendered = re.sub(r"^[\s_\-]+|[\s_\-]+$", "", rendered)
    rendered = re.sub(r"_{2,}", "_", rendered)
    return safe_filename(rendered or title)


def safe_remote_dir(value: str) -> str:
    """Normalize and validate a WebDAV remote directory path."""
    value = (value or "").strip().replace("\\", "/")
    if not value.startswith("/"):
        value = "/" + value
    parts = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("远程目录不能包含 ..")
        parts.append(part)
    return "/" + "/".join(parts)


def cover_host_ok(host: str) -> bool:
    host = (host or "").lower()
    return any(
        host == domain or host.endswith("." + domain)
        for domain in COVER_ALLOWED_HOSTS
    )


def resolve_short_url(text: str, timeout: int = 10) -> str:
    """Expand b23.tv / bili2233.cn short URLs if found in text."""
    raw = (text or "").strip()
    m = _SHORT_LINK_RE.search(raw)
    if not m:
        return raw
    short_url = m.group(0)
    try:
        resp = requests.get(
            short_url,
            headers=HEADERS,
            allow_redirects=True,
            timeout=timeout,
            stream=True,
        )
        final_url = resp.url
        resp.close()
        return final_url or raw
    except Exception:
        return raw


def extract_bvid(text: str) -> Optional[str]:
    """Extract strict BV ID ('BV' + 10 base58 chars)."""
    m = re.search(r"BV[0-9A-Za-z]{10}(?![0-9A-Za-z])", text or "")
    return m.group(0) if m else None


def extract_aid(text: str) -> Optional[int]:
    """Extract av12345 ID."""
    m = re.search(r"(?:^|[/\s?&])av(\d+)(?![0-9A-Za-z])", text or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def extract_epid(text: str) -> Optional[int]:
    text = text or ""
    m = re.search(r"[?&]ep_id=(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"/ep(\d+)", text)
    return int(m.group(1)) if m else None


def extract_ssid(text: str) -> Optional[int]:
    text = text or ""
    # Do not confuse space collection/season_id with bangumi/cheese ss_id
    if "/space.bilibili.com" in text.lower() or "space.bilibili.com" in text.lower():
        return None
    m = re.search(r"[?&]season_id=(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"/ss(\d+)", text)
    return int(m.group(1)) if m else None


def extract_series_id(text: str) -> Optional[int]:
    text = text or ""
    m = re.search(r"[?&]series_id=(\d+)", text)
    if m:
        return int(m.group(1))
    # space.bilibili.com/{mid}/channel/seriesdetail?sid=123
    if "seriesdetail" in text.lower() or "type=series" in text.lower():
        m = re.search(r"[?&]sid=(\d+)", text)
        if m:
            return int(m.group(1))
    return None


def extract_ugc_season(text: str) -> Optional[tuple[int, int]]:
    """Extract (mid, season_id) from UP space UGC collection URLs.

    Examples:
      - https://space.bilibili.com/12345/channel/collectiondetail?sid=67890
      - https://space.bilibili.com/12345/lists/67890?type=season
    """
    text = text or ""
    if "space.bilibili.com" not in text.lower():
        return None
    mid_m = re.search(r"space\.bilibili\.com/(\d+)", text)
    if not mid_m:
        return None
    mid = int(mid_m.group(1))

    if "collectiondetail" in text.lower():
        sid_m = re.search(r"[?&]sid=(\d+)", text)
        if sid_m:
            return mid, int(sid_m.group(1))

    list_m = re.search(r"/lists/(\d+)", text)
    if list_m:
        if "type=series" in text.lower():
            return None
        return mid, int(list_m.group(1))

    return None


def extract_favlist_id(text: str) -> Optional[int]:
    """Extract favorites media_id from favlist?fid=123 or medialist/detail/ml123."""
    text = text or ""
    m = re.search(r"[?&]fid=(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"/ml(\d+)", text)
    if m:
        return int(m.group(1))
    return None


def is_watch_later(text: str) -> bool:
    low = (text or "").strip().lower()
    return (
        low in ("toview", "watchlater", "稍后再看")
        or "/list/watchlater" in low
        or "/watchlater" in low
    )


def extract_page_param(text: str) -> int:
    """Extract ?p=N from video URL if present (0 if not specified)."""
    try:
        qs = parse_qs(urlparse(text or "").query)
        p_vals = qs.get("p")
        if p_vals and p_vals[0].isdigit():
            return max(1, int(p_vals[0]))
    except Exception:
        pass
    return 0


def parse_target(text: str, resolve_short: bool = True) -> dict:
    """Classify any Bilibili URL, share text, or ID into a structured target dict."""
    raw = (text or "").strip()
    resolved = resolve_short_url(raw) if resolve_short else raw
    low = resolved.lower()

    if is_watch_later(resolved):
        return {"kind": "watchlater", "raw": raw, "resolved": resolved}

    is_cheese = "/cheese/" in low
    ep_id = extract_epid(resolved)
    ss_id = extract_ssid(resolved)
    if ep_id or ss_id:
        return {
            "kind": "course" if is_cheese else "bangumi",
            "ep_id": ep_id,
            "season_id": ss_id,
            "is_cheese": is_cheese,
            "raw": raw,
            "resolved": resolved,
        }

    fav_id = extract_favlist_id(resolved)
    if fav_id:
        return {"kind": "favlist", "media_id": fav_id, "raw": raw, "resolved": resolved}

    ugc_season = extract_ugc_season(resolved)
    if ugc_season:
        mid, sid = ugc_season
        return {
            "kind": "ugc_season",
            "mid": mid,
            "season_id": sid,
            "raw": raw,
            "resolved": resolved,
        }

    series_id = extract_series_id(resolved)
    if series_id:
        mid_m = re.search(r"space\.bilibili\.com/(\d+)", resolved)
        return {
            "kind": "series",
            "series_id": series_id,
            "mid": int(mid_m.group(1)) if mid_m else None,
            "raw": raw,
            "resolved": resolved,
        }

    bvid = extract_bvid(resolved)
    if bvid:
        return {
            "kind": "video",
            "bvid": bvid,
            "page": extract_page_param(resolved),
            "raw": raw,
            "resolved": resolved,
        }

    aid = extract_aid(resolved)
    if aid:
        return {
            "kind": "video",
            "aid": aid,
            "bvid": None,
            "page": extract_page_param(resolved),
            "raw": raw,
            "resolved": resolved,
        }

    return {"kind": "unknown", "raw": raw, "resolved": resolved}


def split_batch_inputs(text: str) -> list[str]:
    """Split multi-line pasted text into distinct candidate URLs/IDs."""
    items: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        urls = _URL_IN_TEXT_RE.findall(line)
        candidates = urls if urls else [line]
        for cand in candidates:
            cand = cand.strip()
            if cand and cand not in seen:
                seen.add(cand)
                items.append(cand)
    return items


def parse_episode_ranges(text: str, max_index: int) -> list[int]:
    """Parse episode range syntax like '1-10,15,20-25' into sorted unique 1-based indices."""
    text = (text or "").strip()
    if not text:
        raise ValueError("请输入集数范围，例如：1-10,15,20-25")
    text = text.replace("，", ",").replace("、", ",").replace(" ", ",")
    result: set[int] = set()
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
