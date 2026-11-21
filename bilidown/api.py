"""Bilibili HTTP API client (video, bangumi, course, collections, account, QR login)."""

import base64
import io
import os
import tempfile
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from .config import build_headers, get_sessdata, save_cookie_string
from .constants import (
    AUDIO_NAMES,
    AUDIO_RANK,
    BASE_URL,
    CODEC_NAMES,
    HEADERS,
    PASSPORT_URL,
    QN_TO_NAME,
)
from .parser import (
    extract_epid,
    extract_ssid,
    parse_target,
)
from .wbi import invalidate_wbi_cache, sign_wbi_params


def parse_api_response(resp: requests.Response, action: str) -> dict:
    """Decode JSON API response with clear error message if HTML/WAF returned."""
    try:
        return resp.json()
    except ValueError as exc:
        content_type = resp.headers.get("Content-Type", "未知类型").split(";")[0]
        raise RuntimeError(
            f"{action}失败: 接口返回 HTTP {resp.status_code} ({content_type})，不是有效 JSON"
        ) from exc


# ─── Video & PlayURL ─────────────────────────────────────────────────────

def get_video_info(bvid: str) -> dict:
    url = f"{BASE_URL}/x/web-interface/view"
    resp = requests.get(url, params={"bvid": bvid}, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取视频信息")
    if data.get("code") != 0:
        raise RuntimeError(f"API 错误: {data.get('message', '未知错误')}")
    return data["data"]


def get_video_info_by_aid(aid: int) -> dict:
    url = f"{BASE_URL}/x/web-interface/view"
    resp = requests.get(url, params={"aid": aid}, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取视频信息")
    if data.get("code") != 0:
        raise RuntimeError(f"API 错误: {data.get('message', '未知错误')}")
    return data["data"]


def get_playurl(aid: int, cid: int, qn: int = 80, fourk: bool = True) -> dict:
    """Fetch DASH/FLV playurl, preferring WBI-signed endpoint with fallback."""
    base_params = {
        "avid": aid,
        "cid": cid,
        "qn": qn,
        "fnval": 4048,
        "fnver": 0,
        "fourk": 1 if fourk else 0,
    }
    headers = build_headers()

    # Try WBI endpoint first for reliable 4K/HDR/Dolby/high-bitrate streams
    try:
        signed = sign_wbi_params(base_params)
        resp = requests.get(
            f"{BASE_URL}/x/player/wbi/playurl",
            params=signed,
            headers=headers,
            timeout=15,
        )
        data = parse_api_response(resp, "获取播放地址")
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
    except Exception:
        pass

    # Fallback to classic endpoint
    resp = requests.get(
        f"{BASE_URL}/x/player/playurl",
        params=base_params,
        headers=headers,
        timeout=15,
    )
    data = parse_api_response(resp, "获取播放地址")
    if data.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {data.get('message', '未知错误')}")
    return data["data"]


# ─── Bangumi & Cheese (Course) ───────────────────────────────────────────

def get_season_info(
    ep_id: Optional[int] = None,
    season_id: Optional[int] = None,
    is_cheese: bool = False,
) -> dict:
    if ep_id is None and season_id is None:
        raise RuntimeError("获取系列信息需要 ep_id 或 season_id")
    params = {"ep_id": ep_id} if ep_id is not None else {"season_id": season_id}
    url = (
        f"{BASE_URL}/pugv/view/web/season"
        if is_cheese
        else f"{BASE_URL}/pgc/view/web/season"
    )
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取课程信息" if is_cheese else "获取番剧信息")
    if data.get("code") != 0:
        type_name = "课程" if is_cheese else "番剧"
        raise RuntimeError(f"获取{type_name}信息失败: {data.get('message', '未知错误')}")
    return data.get("data", data.get("result", {}))


def _normalize_season_episodes(season: dict, is_cheese: bool) -> list[dict]:
    episodes = []
    for idx, ep in enumerate(season.get("episodes") or []):
        if is_cheese:
            ep_id = ep.get("id")
            title = ep.get("title") or f"EP{idx + 1}"
            bvid = None
            duration = ep.get("duration") or 0
            badge = ep.get("status")
        else:
            ep_id = ep.get("id") or ep.get("ep_id")
            title = (
                ep.get("long_title")
                or ep.get("share_copy")
                or ep.get("title")
                or f"EP{idx + 1}"
            )
            bvid = ep.get("bvid")
            dur = ep.get("duration") or 0
            # PGC duration is often in milliseconds
            duration = dur // 1000 if dur > 100_000 else dur
            badge = ep.get("badge") or ""

        episodes.append(
            {
                "ep_id": ep_id,
                "title": title,
                "index": idx + 1,
                "aid": ep.get("aid"),
                "cid": ep.get("cid"),
                "bvid": bvid,
                "duration": duration,
                "badge": badge,
                "cover": ep.get("cover") or "",
                "raw": ep,
            }
        )
    return episodes


def get_media_collection(
    url: str,
    *,
    allow_season: bool = False,
    season_fetcher=None,
) -> dict:
    """Resolve a course or bangumi link into a normalized collection dict.

    By default matches legacy behavior (ep links only when allow_season=False).
    Pass allow_season=True to also support ss/season_id links seamlessly.
    """
    ep_id = extract_epid(url)
    ss_id = extract_ssid(url) if allow_season else None
    if not ep_id and not ss_id:
        raise RuntimeError("请使用课程或番剧的单集链接（包含 /ep 的链接），而非整季链接")

    is_cheese = "/cheese/" in (url or "").lower()
    fetcher = season_fetcher or get_season_info
    if ep_id:
        season = fetcher(ep_id, is_cheese=is_cheese)
    else:
        season = fetcher(season_id=ss_id, is_cheese=is_cheese)

    media_type = "course" if is_cheese else "bangumi"
    title = season.get("title") or season.get("season_title") or "未知"
    episodes = _normalize_season_episodes(season, is_cheese)
    current_ep_id = ep_id or (episodes[0]["ep_id"] if episodes else None)

    return {
        "media_type": media_type,
        "title": title,
        "cover": season.get("cover") or season.get("season_cover") or "",
        "current_ep_id": current_ep_id,
        "episodes": episodes,
        "raw": season,
    }


def get_course_playurl(aid: int, cid: int, ep_id: int, qn: int = 80) -> dict:
    params = {
        "avid": aid,
        "cid": cid,
        "ep_id": ep_id,
        "qn": qn,
        "fnval": 4048,
        "fnver": 0,
        "fourk": 1,
    }
    url = f"{BASE_URL}/pugv/player/web/playurl"
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取课程播放地址")
    if data.get("code") != 0:
        if data.get("code") == -403:
            raise RuntimeError(
                "获取课程播放地址失败: B站未向当前 Cookie 会话返回该课时的播放权限；"
                "请重新扫码登录，或导入网页已能播放此课时的完整 Cookie"
            )
        raise RuntimeError(f"获取课程播放地址失败: {data.get('message', '未知错误')}")
    return data["data"]


def get_bangumi_playurl(aid: int, cid: int, ep_id: int, qn: int = 80) -> dict:
    params = {
        "avid": aid,
        "cid": cid,
        "ep_id": ep_id,
        "qn": qn,
        "fnval": 4048,
        "fnver": 0,
        "fourk": 1,
    }
    url = f"{BASE_URL}/pgc/player/web/playurl"
    resp = requests.get(url, params=params, headers=build_headers(), timeout=15)
    data = parse_api_response(resp, "获取番剧播放地址")
    if data.get("code") != 0:
        if data.get("code") == -403:
            raise RuntimeError(
                "获取番剧播放地址失败: B站未向当前 Cookie 会话返回该集的播放权限；"
                "请确认已登录大会员账号，或导入已能播放此番剧的完整 Cookie"
            )
        raise RuntimeError(f"获取番剧播放地址失败: {data.get('message', '未知错误')}")
    return data.get("result", data.get("data", {}))


# ─── Series, UGC Season, Favorites, Watch Later ──────────────────────────

def get_series_info(series_id: int, mid: Optional[int] = None) -> list[dict]:
    """Fetch video list from a series (supports pagination)."""
    headers = build_headers()
    if mid:
        archives: list[dict] = []
        pn = 1
        while True:
            resp = requests.get(
                f"{BASE_URL}/x/series/archives",
                params={"mid": mid, "series_id": series_id, "pn": pn, "ps": 100},
                headers=headers,
                timeout=15,
            )
            data = parse_api_response(resp, "获取合集信息")
            if data.get("code") != 0:
                break
            page_items = (data.get("data") or {}).get("archives") or []
            if not page_items:
                break
            archives.extend(page_items)
            total = ((data.get("data") or {}).get("page") or {}).get("total", len(archives))
            if len(archives) >= total or len(page_items) < 100:
                break
            pn += 1
        if archives:
            return archives

    url = f"{BASE_URL}/x/web-interface/arc/search?series_id={series_id}&type=series"
    resp = requests.get(url, headers=headers, timeout=15)
    data = parse_api_response(resp, "获取合集信息")
    if data.get("code") != 0:
        raise RuntimeError(f"获取合集信息失败: {data.get('message', '未知错误')}")
    return (data.get("data") or {}).get("archives") or []


def get_ugc_season_info(mid: int, season_id: int) -> dict:
    """Fetch UP's UGC collection/season (合集列表) with pagination."""
    headers = build_headers()
    archives: list[dict] = []
    meta: dict = {}
    page_num = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/x/polymer/web-space/seasons_archives_list",
            params={
                "mid": mid,
                "season_id": season_id,
                "sort_reverse": "false",
                "page_num": page_num,
                "page_size": 100,
            },
            headers=headers,
            timeout=15,
        )
        data = parse_api_response(resp, "获取UP合集")
        if data.get("code") != 0:
            raise RuntimeError(f"获取UP合集失败: {data.get('message', '未知错误')}")
        payload = data.get("data") or {}
        if not meta:
            meta = payload.get("meta") or {}
        page_items = payload.get("archives") or []
        if not page_items:
            break
        archives.extend(page_items)
        total = (payload.get("page") or {}).get("total", len(archives))
        if len(archives) >= total or len(page_items) < 100:
            break
        page_num += 1
    return {
        "title": meta.get("name") or f"合集_{season_id}",
        "cover": meta.get("cover") or (archives[0].get("pic", "") if archives else ""),
        "description": meta.get("description") or "",
        "archives": archives,
    }


def get_user_fav_folders(mid: int) -> list[dict]:
    """List a user's favorite folders."""
    resp = requests.get(
        f"{BASE_URL}/x/v3/fav/folder/created/list-all",
        params={"up_mid": mid},
        headers=build_headers(),
        timeout=15,
    )
    data = parse_api_response(resp, "获取收藏夹列表")
    if data.get("code") != 0:
        raise RuntimeError(f"获取收藏夹列表失败: {data.get('message', '未知错误')}")
    return (data.get("data") or {}).get("list") or []


def get_favlist_info(media_id: int, max_pages: int = 20) -> dict:
    """Fetch videos inside a favorite folder."""
    headers = build_headers()
    medias: list[dict] = []
    info_meta: dict = {}
    for pn in range(1, max_pages + 1):
        resp = requests.get(
            f"{BASE_URL}/x/v3/fav/resource/list",
            params={"media_id": media_id, "pn": pn, "ps": 20, "platform": "web"},
            headers=headers,
            timeout=15,
        )
        data = parse_api_response(resp, "获取收藏夹内容")
        if data.get("code") != 0:
            raise RuntimeError(f"获取收藏夹内容失败: {data.get('message', '未知错误')}")
        payload = data.get("data") or {}
        if not info_meta:
            info_meta = payload.get("info") or {}
        page_items = payload.get("medias") or []
        for item in page_items:
            # Skip deleted/invalid items (attr == 9 or empty bvid)
            if item.get("bvid") and item.get("title") != "已失效视频":
                medias.append(item)
        if not payload.get("has_more"):
            break
    return {
        "media_id": media_id,
        "title": info_meta.get("title") or f"收藏夹_{media_id}",
        "cover": info_meta.get("cover") or (medias[0].get("cover", "") if medias else ""),
        "owner": (info_meta.get("upper") or {}).get("name", ""),
        "medias": medias,
    }


def get_watch_later() -> list[dict]:
    """Fetch the logged-in user's Watch Later ('稍后再看') list."""
    resp = requests.get(
        f"{BASE_URL}/x/v2/history/toview/web",
        headers=build_headers(),
        timeout=15,
    )
    data = parse_api_response(resp, "获取稍后再看")
    if data.get("code") != 0:
        raise RuntimeError(f"获取稍后再看失败: {data.get('message', '请先登录 B站账号')}")
    return (data.get("data") or {}).get("list") or []


# ─── Account & QR Login ──────────────────────────────────────────────────

def get_nav_info(cookie: Optional[str] = None) -> dict:
    resp = requests.get(
        f"{BASE_URL}/x/web-interface/nav",
        headers=build_headers(cookie_override=cookie),
        timeout=12,
    )
    return parse_api_response(resp, "检查登录状态")


def get_login_profile(cookie: Optional[str] = None) -> dict:
    """Return normalized login profile dict."""
    if cookie is None and not get_sessdata():
        return {"logged_in": False}
    try:
        data = get_nav_info(cookie=cookie)
        user = data.get("data") or {}
        if data.get("code") == 0 and user.get("isLogin"):
            vip_label = ((user.get("vip") or {}).get("label") or {}).get("text", "")
            return {
                "logged_in": True,
                "mid": user.get("mid"),
                "name": user.get("uname", ""),
                "face": user.get("face", ""),
                "level": (user.get("level_info") or {}).get("current_level", 0),
                "vip": user.get("vipStatus", 0) == 1,
                "vip_label": vip_label or ("大会员" if user.get("vipStatus", 0) == 1 else "普通用户"),
                "coin": user.get("money", 0),
            }
    except Exception:
        pass
    return {"logged_in": False}


def generate_qrcode() -> Optional[dict]:
    """Generate a Bilibili QR login session + PNG image path & base64 data URI."""
    import qrcode

    resp = requests.get(
        f"{PASSPORT_URL}/x/passport-login/web/qrcode/generate",
        headers=HEADERS,
        timeout=15,
    ).json()
    if resp.get("code") != 0:
        return None

    url = resp["data"]["url"]
    key = resp["data"]["qrcode_key"]

    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    img_path = os.path.join(tempfile.gettempdir(), "bili_login_qr.png")
    try:
        with open(img_path, "wb") as f:
            f.write(png_bytes)
    except OSError:
        img_path = ""

    return {
        "url": url,
        "qr_path": img_path,
        "key": key,
        "image_data_url": "data:image/png;base64," + base64.b64encode(png_bytes).decode(),
    }


def poll_login_status(key: str) -> dict:
    """Poll Bilibili QR login status (supports both v2 86090 and legacy -5 scanned codes)."""
    response = requests.get(
        f"{PASSPORT_URL}/x/passport-login/web/qrcode/poll",
        params={"qrcode_key": key},
        headers=HEADERS,
        timeout=15,
    )
    poll = response.json()
    code = (poll.get("data") or {}).get("code")

    if code == 0:
        url_info = poll["data"].get("url", "")
        parsed = urlparse(url_info)
        params = parse_qs(parsed.query)
        auth_keys = ("SESSDATA", "DedeUserID", "DedeUserID__ckMd5", "bili_jct", "sid")
        cookies = {k: params[k][0] for k in auth_keys if params.get(k)}
        cookies.update(response.cookies.get_dict())
        if cookies.get("SESSDATA"):
            cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
            cfg = save_cookie_string(cookie)
            invalidate_wbi_cache()
            return {
                "status": "success",
                "sessdata": cfg["sessdata"],
                "cookie_fields": sorted(cfg["cookies"].keys()),
            }
        return {"status": "failed", "message": "未能获取 SESSDATA"}
    if code in (-5, 86090):
        return {"status": "scanned"}
    if code in (-4, 86038):
        return {"status": "expired"}
    return {"status": "waiting"}


# ─── Stream Inspection Summary ───────────────────────────────────────────

def describe_playurl_streams(playurl: dict) -> dict:
    """Extract available qualities, codecs, and audio tiers from a playurl response."""
    if not playurl:
        return {"qualities": [], "codecs": [], "audios": []}

    accept_quality = playurl.get("accept_quality") or []
    accept_desc = playurl.get("accept_description") or []
    dash = playurl.get("dash") or {}

    stream_qns = {v.get("id") for v in (dash.get("video") or []) if v.get("id")}
    codec_ids = sorted(
        {v.get("codecid") for v in (dash.get("video") or []) if v.get("codecid") in CODEC_NAMES},
        key=lambda c: (0 if c == 12 else (1 if c == 7 else 2)),
    )

    qualities = []
    seen_qns = set()
    for qn, desc in zip(accept_quality, accept_desc):
        name = QN_TO_NAME.get(qn)
        if not name or qn in seen_qns:
            continue
        seen_qns.add(qn)
        qualities.append(
            {
                "qn": qn,
                "name": name,
                "label": desc,
                "available": (qn in stream_qns) if stream_qns else True,
            }
        )

    audios_raw = list(dash.get("audio") or [])
    audios_raw += list((dash.get("flac") or {}).get("audio") or [])
    audios_raw += list((dash.get("dolby") or {}).get("audio") or [])
    audio_ids = sorted(
        {a.get("id") for a in audios_raw if a.get("id")},
        key=lambda x: AUDIO_RANK.get(x, -1),
        reverse=True,
    )
    audios = [{"id": aid, "label": AUDIO_NAMES.get(aid, str(aid))} for aid in audio_ids]

    return {
        "qualities": qualities,
        "codecs": [{"id": cid, "label": CODEC_NAMES[cid]} for cid in codec_ids],
        "audios": audios,
    }


def fetch_subtitle_list(aid: int, cid: int, attempts: int = 3) -> list[dict]:
    """Fetch subtitle tracks via WBI-signed endpoint first, with classic fallback + backoff."""
    last: list[dict] = []
    headers = build_headers()
    for attempt in range(attempts):
        # 1) Signed WBI endpoint
        try:
            signed = sign_wbi_params({"aid": aid, "cid": cid})
            resp = requests.get(
                f"{BASE_URL}/x/player/wbi/v2",
                params=signed,
                headers=headers,
                timeout=15,
            )
            data = parse_api_response(resp, "获取字幕信息")
            subtitles = (((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
            last = subtitles or last
            if any(s.get("subtitle_url") for s in subtitles):
                return subtitles
        except Exception:
            pass

        # 2) Fallback classic endpoint
        try:
            resp = requests.get(
                f"{BASE_URL}/x/player/v2",
                params={"aid": aid, "cid": cid},
                headers=headers,
                timeout=15,
            )
            data = parse_api_response(resp, "获取字幕信息")
            subtitles = (((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
            last = subtitles or last
            if any(s.get("subtitle_url") for s in subtitles):
                return subtitles
        except Exception:
            pass

        if attempt + 1 < attempts:
            time.sleep(1.5 + attempt * 1.5)
    return last


def inspect_url(url: str) -> dict:
    """Inspect any supported Bilibili link and return a unified preview payload for UI/CLI."""
    target = parse_target(url, resolve_short=True)
    kind = target["kind"]

    if kind == "unknown":
        raise ValueError("无法识别该链接，支持 BV/av 视频、b23.tv 短链、番剧/课程 ep/ss、UP 合集、收藏夹与稍后再看")

    if kind == "video":
        info = (
            get_video_info(target["bvid"])
            if target.get("bvid")
            else get_video_info_by_aid(target["aid"])
        )
        bvid = info.get("bvid") or target.get("bvid")
        aid = info["aid"]
        pages_raw = info.get("pages") or []
        pages = [
            {
                "id": p.get("page") or (i + 1),
                "index": i + 1,
                "page": p.get("page") or (i + 1),
                "cid": p.get("cid"),
                "title": p.get("part") or info.get("title") or f"P{i + 1}",
                "duration": p.get("duration") or 0,
            }
            for i, p in enumerate(pages_raw)
        ]
        # Probe available qualities/codecs on first (or requested) page
        req_page = target.get("page") or 0
        probe_cid = (
            pages[req_page - 1]["cid"]
            if 1 <= req_page <= len(pages)
            else info.get("cid")
        )
        streams = {"qualities": [], "codecs": [], "audios": []}
        subtitles_preview = []
        try:
            playurl = get_playurl(aid, probe_cid, qn=127, fourk=True)
            streams = describe_playurl_streams(playurl)
        except Exception:
            pass
        try:
            subs = fetch_subtitle_list(aid, probe_cid, attempts=1)
            subtitles_preview = [
                {
                    "lan": s.get("lan", ""),
                    "label": s.get("lan_doc") or s.get("lan", ""),
                    "is_ai": (s.get("lan") or "").startswith("ai-"),
                }
                for s in subs
                if s.get("subtitle_url") and not s.get("is_lock")
            ]
        except Exception:
            pass

        default_selected = (
            [req_page]
            if (len(pages) > 1 and 1 <= req_page <= len(pages))
            else [p["id"] for p in pages]
        )

        return {
            "kind": "video",
            "media_type": "video",
            "url": target["resolved"],
            "bvid": bvid,
            "aid": aid,
            "cid": info.get("cid"),
            "title": info.get("title", ""),
            "desc": (info.get("desc") or "")[:300],
            "cover": info.get("pic", ""),
            "owner": (info.get("owner") or {}).get("name", ""),
            "owner_face": (info.get("owner") or {}).get("face", ""),
            "duration": info.get("duration", 0),
            "stat": info.get("stat") or {},
            "items": pages,
            "pages": pages,
            "default_selected_ids": default_selected,
            "current_item_id": req_page if 1 <= req_page <= len(pages) else (pages[0]["id"] if pages else 1),
            "streams": streams,
            "subtitles": subtitles_preview,
        }

    if kind in ("bangumi", "course"):
        collection = get_media_collection(target["resolved"], allow_season=True)
        raw = collection.get("raw") or {}
        episodes = collection["episodes"]
        items = [
            {
                "id": ep["ep_id"],
                "ep_id": ep["ep_id"],
                "index": ep["index"],
                "title": ep["title"],
                "duration": ep.get("duration") or 0,
                "badge": ep.get("badge") or "",
                "aid": ep.get("aid"),
                "cid": ep.get("cid"),
                "bvid": ep.get("bvid"),
            }
            for ep in episodes
        ]
        default_ids = (
            [collection["current_ep_id"]]
            if target.get("ep_id")
            else [ep["ep_id"] for ep in episodes]
        )
        # Try stream inspection on current episode
        streams = {"qualities": [], "codecs": [], "audios": []}
        cur_ep = next((e for e in episodes if e["ep_id"] == collection["current_ep_id"]), episodes[0] if episodes else None)
        if cur_ep and cur_ep.get("aid") and cur_ep.get("cid"):
            try:
                if kind == "course":
                    pu = get_course_playurl(cur_ep["aid"], cur_ep["cid"], cur_ep["ep_id"], qn=127)
                else:
                    pu = get_bangumi_playurl(cur_ep["aid"], cur_ep["cid"], cur_ep["ep_id"], qn=127)
                streams = describe_playurl_streams(pu)
            except Exception:
                pass

        return {
            "kind": "collection",
            "media_type": collection["media_type"],
            "url": target["resolved"],
            "title": collection["title"],
            "desc": (raw.get("evaluate") or raw.get("subtitle") or "")[:300],
            "cover": collection.get("cover") or "",
            "owner": (raw.get("up_info") or {}).get("uname", ""),
            "current_ep_id": collection["current_ep_id"],
            "current_item_id": collection["current_ep_id"],
            "episodes": items,
            "items": items,
            "default_ep_ids": default_ids,
            "default_selected_ids": default_ids,
            "streams": streams,
        }

    if kind == "ugc_season":
        ugc = get_ugc_season_info(target["mid"], target["season_id"])
        items = [
            {
                "id": a.get("bvid"),
                "bvid": a.get("bvid"),
                "index": i + 1,
                "title": a.get("title", f"视频 {i + 1}"),
                "duration": a.get("duration") or 0,
                "cover": a.get("pic", ""),
            }
            for i, a in enumerate(ugc["archives"])
            if a.get("bvid")
        ]
        return {
            "kind": "playlist",
            "media_type": "ugc_season",
            "url": target["resolved"],
            "title": ugc["title"],
            "desc": ugc.get("description", "")[:300],
            "cover": ugc["cover"],
            "count": len(items),
            "items": items,
            "default_selected_ids": [it["id"] for it in items],
            "streams": {"qualities": [], "codecs": [], "audios": []},
        }

    if kind == "series":
        archives = get_series_info(target["series_id"], mid=target.get("mid"))
        items = [
            {
                "id": a.get("bvid"),
                "bvid": a.get("bvid"),
                "index": i + 1,
                "title": a.get("title", f"视频 {i + 1}"),
                "duration": a.get("duration") or 0,
                "cover": a.get("pic", ""),
            }
            for i, a in enumerate(archives)
            if a.get("bvid")
        ]
        cover = archives[0].get("pic", "") if archives else ""
        return {
            "kind": "playlist",
            "media_type": "series",
            "url": target["resolved"],
            "title": f"合集 #{target['series_id']}",
            "cover": cover,
            "count": len(items),
            "items": items,
            "default_selected_ids": [it["id"] for it in items],
            "streams": {"qualities": [], "codecs": [], "audios": []},
        }

    if kind == "favlist":
        fav = get_favlist_info(target["media_id"])
        items = [
            {
                "id": m.get("bvid"),
                "bvid": m.get("bvid"),
                "index": i + 1,
                "title": m.get("title", f"视频 {i + 1}"),
                "duration": m.get("duration") or 0,
                "owner": (m.get("upper") or {}).get("name", ""),
                "cover": m.get("cover", ""),
            }
            for i, m in enumerate(fav["medias"])
            if m.get("bvid")
        ]
        return {
            "kind": "playlist",
            "media_type": "favlist",
            "url": target["resolved"],
            "title": fav["title"],
            "owner": fav.get("owner", ""),
            "cover": fav["cover"],
            "count": len(items),
            "items": items,
            "default_selected_ids": [it["id"] for it in items],
            "streams": {"qualities": [], "codecs": [], "audios": []},
        }

    if kind == "watchlater":
        wl = get_watch_later()
        items = [
            {
                "id": item.get("bvid"),
                "bvid": item.get("bvid"),
                "index": i + 1,
                "title": item.get("title", f"视频 {i + 1}"),
                "duration": item.get("duration") or 0,
                "owner": (item.get("owner") or {}).get("name", ""),
                "cover": item.get("pic", ""),
            }
            for i, item in enumerate(wl)
            if item.get("bvid")
        ]
        return {
            "kind": "playlist",
            "media_type": "watchlater",
            "url": "https://www.bilibili.com/list/watchlater",
            "title": "稍后再看",
            "cover": items[0]["cover"] if items else "",
            "count": len(items),
            "items": items,
            "default_selected_ids": [it["id"] for it in items],
            "streams": {"qualities": [], "codecs": [], "audios": []},
        }

    raise ValueError("暂不支持的链接类型")
