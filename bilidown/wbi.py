"""Bilibili WBI request signing (img_key/sub_key mixin + MD5 w_rid)."""

import hashlib
import threading
import time
import urllib.parse
from typing import Optional

import requests

from .config import build_headers
from .constants import BASE_URL

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_WBI_LOCK = threading.Lock()
_WBI_CACHE: dict = {"img_key": "", "sub_key": "", "fetched_at": 0.0}
_WBI_TTL = 1800.0  # 30 minutes


def get_mixin_key(orig: str) -> str:
    """Shuffle img_key + sub_key via Bilibili's WBI mixin table."""
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


def _extract_key_from_url(url: str) -> str:
    if not url:
        return ""
    stem = url.rsplit("/", 1)[-1]
    return stem.split(".", 1)[0]


def get_wbi_keys(force_refresh: bool = False, headers: Optional[dict] = None) -> tuple[str, str]:
    """Fetch and cache WBI img_key and sub_key from /x/web-interface/nav."""
    now = time.monotonic()
    with _WBI_LOCK:
        if (
            not force_refresh
            and _WBI_CACHE["img_key"]
            and _WBI_CACHE["sub_key"]
            and (now - _WBI_CACHE["fetched_at"]) < _WBI_TTL
        ):
            return _WBI_CACHE["img_key"], _WBI_CACHE["sub_key"]

    resp = requests.get(
        f"{BASE_URL}/x/web-interface/nav",
        headers=headers or build_headers(),
        timeout=12,
    )
    data = resp.json()
    wbi_img = (data.get("data") or {}).get("wbi_img") or {}
    img_key = _extract_key_from_url(wbi_img.get("img_url", ""))
    sub_key = _extract_key_from_url(wbi_img.get("sub_url", ""))

    if img_key and sub_key:
        with _WBI_LOCK:
            _WBI_CACHE["img_key"] = img_key
            _WBI_CACHE["sub_key"] = sub_key
            _WBI_CACHE["fetched_at"] = time.monotonic()
    return img_key, sub_key


def sign_wbi_params(
    params: dict,
    img_key: Optional[str] = None,
    sub_key: Optional[str] = None,
    wts: Optional[int] = None,
) -> dict:
    """Sign query parameters with Bilibili WBI wts + w_rid."""
    if not img_key or not sub_key:
        try:
            img_key, sub_key = get_wbi_keys()
        except Exception:
            return dict(params)
    if not img_key or not sub_key:
        return dict(params)

    mixin_key = get_mixin_key(img_key + sub_key)
    signed = {k: v for k, v in params.items() if v is not None}
    signed["wts"] = int(wts if wts is not None else time.time())
    signed = dict(sorted(signed.items()))

    # Filter characters Bilibili strips before signing: !'()*
    cleaned = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in signed.items()
    }
    query = urllib.parse.urlencode(cleaned)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    cleaned["w_rid"] = w_rid
    return cleaned


def invalidate_wbi_cache() -> None:
    with _WBI_LOCK:
        _WBI_CACHE["img_key"] = ""
        _WBI_CACHE["sub_key"] = ""
        _WBI_CACHE["fetched_at"] = 0.0
