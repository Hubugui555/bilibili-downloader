"""Shared constants for BiliDown."""

import os
import subprocess

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
}

BASE_URL = "https://api.bilibili.com"
PASSPORT_URL = "https://passport.bilibili.com"

# 画质映射: 名称 -> qn 值 (从高到低)
QUALITY_MAP = {
    "8K": 127,
    "杜比视界": 126,
    "HDR": 125,
    "4K": 120,
    "1080P60": 116,
    "1080P+": 112,
    "1080P": 80,
    "720P60": 74,
    "720P": 64,
    "480P": 32,
    "360P": 16,
    "240P": 6,
}

QN_TO_NAME = {v: k for k, v in QUALITY_MAP.items()}

# 视频编码映射: Bilibili codecid
# 7 = AVC/H.264, 12 = HEVC/H.265, 13 = AV1
CODEC_MAP = {
    "auto": None,
    "avc": 7,
    "hevc": 12,
    "av1": 13,
}

CODEC_NAMES = {
    7: "H.264 (AVC)",
    12: "H.265 (HEVC)",
    13: "AV1",
}

CODEC_PRIORITY_AUTO = {12: 3, 7: 2, 13: 1}  # HEVC > AVC > AV1 for best balance

# 音质排序: 数值越大音质越好。
# 注意 30250(杜比全景声) / 30251(Hi-Res) 的 id 数值小于 30280(192K)
AUDIO_RANK = {
    30216: 0,  # 64K
    30232: 1,  # 132K
    30280: 2,  # 192K
    30250: 3,  # 杜比全景声
    30251: 4,  # Hi-Res 无损
}

AUDIO_NAMES = {
    30216: "64K",
    30232: "132K",
    30280: "192K",
    30250: "杜比全景声",
    30251: "Hi-Res 无损",
}

AUDIO_FORMATS = ("m4a", "mp3", "flac")

MEDIA_SUFFIXES = {".mp4", ".mkv", ".flv", ".m4a", ".mp3", ".flac"}

COVER_ALLOWED_HOSTS = ("hdslb.com", "bilibili.com", "bilivideo.com")

# Windows 下无控制台 GUI 运行子进程时不弹出黑色窗口
SUBPROCESS_FLAGS = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if os.name == "nt"
    else {}
)

DEFAULT_SETTINGS = {
    "output_dir": "",
    "quality": "1080P",
    "codec": "auto",
    "workers": 2,
    "download_mode": "video",  # video | audio | extras
    "audio_format": "m4a",     # m4a | mp3 | flac
    "cover": True,
    "danmaku": True,
    "subtitle": True,
    "subtitle_mode": "best_zh",  # best_zh | all
    "embed_extras": True,
    "keep_sidecar": True,
    "cdn_probe": True,
    "filename_template": "{title}",
    "danmaku_area": 0.55,      # 0.33 | 0.55 | 0.85
    "danmaku_opacity": 0.85,   # 0.5 ~ 1.0
    "danmaku_font_scale": 1.0, # 0.75 ~ 1.35
    "danmaku_speed": 1.0,      # 0.75 (慢) | 1.0 (标准) | 1.3 (快)
    "cloudreve_enabled": False,
    "cloudreve_url": "",
    "cloudreve_username": "",
    "cloudreve_password": "",
    "cloudreve_remote_dir": "/视频/B站",
    "keep_local": False,
    "theme": "dark",
}
