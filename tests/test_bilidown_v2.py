"""Comprehensive test suite for BiliDown Studio 2.0 refactored architecture & new features."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bilidown import api, danmaku, engine, media, parser, wbi
from bilidown.manager import TaskManager
from bilidown.store import HistoryStore
from bilidown.web.app import create_app


# ─── WBI Signing ─────────────────────────────────────────────────────────

def test_wbi_mixin_and_sign_deterministic():
    img = "7cd084941338484aae1ad9425b84077c"
    sub = "4932caff0ff746eab6f01bf08b70ac45"
    mk = wbi.get_mixin_key(img + sub)
    assert len(mk) == 32

    signed = wbi.sign_wbi_params(
        {"aid": 12345, "cid": 67890, "note": "a!b'c(d)e*f"},
        img_key=img,
        sub_key=sub,
        wts=1700000000,
    )
    assert signed["wts"] == "1700000000"
    assert signed["note"] == "abcdef"  # !'()* stripped
    assert len(signed["w_rid"]) == 32
    # Deterministic
    signed2 = wbi.sign_wbi_params(
        {"cid": 67890, "aid": 12345, "note": "a!b'c(d)e*f"},
        img_key=img,
        sub_key=sub,
        wts=1700000000,
    )
    assert signed["w_rid"] == signed2["w_rid"]


# ─── Universal URL Parser & Templates ────────────────────────────────────

def test_parse_all_target_types():
    assert parser.parse_target("BV1xx411c7mD", resolve_short=False)["kind"] == "video"
    t_p = parser.parse_target("https://www.bilibili.com/video/BV1xx411c7mD?p=3", resolve_short=False)
    assert t_p["kind"] == "video" and t_p["page"] == 3

    t_av = parser.parse_target("https://www.bilibili.com/video/av170001", resolve_short=False)
    assert t_av["kind"] == "video" and t_av["aid"] == 170001

    t_bg = parser.parse_target("https://www.bilibili.com/bangumi/play/ss12345", resolve_short=False)
    assert t_bg["kind"] == "bangumi" and t_bg["season_id"] == 12345

    t_ch = parser.parse_target("https://www.bilibili.com/cheese/play/ep9988", resolve_short=False)
    assert t_ch["kind"] == "course" and t_ch["ep_id"] == 9988

    t_ugc1 = parser.parse_target(
        "https://space.bilibili.com/42/channel/collectiondetail?sid=777",
        resolve_short=False,
    )
    assert t_ugc1["kind"] == "ugc_season" and t_ugc1["mid"] == 42 and t_ugc1["season_id"] == 777

    t_ugc2 = parser.parse_target(
        "https://space.bilibili.com/42/lists/888?type=season",
        resolve_short=False,
    )
    assert t_ugc2["kind"] == "ugc_season" and t_ugc2["season_id"] == 888

    t_ser = parser.parse_target(
        "https://space.bilibili.com/42/channel/seriesdetail?sid=555",
        resolve_short=False,
    )
    assert t_ser["kind"] == "series" and t_ser["series_id"] == 555 and t_ser["mid"] == 42

    t_fav1 = parser.parse_target("https://space.bilibili.com/42/favlist?fid=333444", resolve_short=False)
    assert t_fav1["kind"] == "favlist" and t_fav1["media_id"] == 333444

    t_fav2 = parser.parse_target("https://www.bilibili.com/medialist/detail/ml98765", resolve_short=False)
    assert t_fav2["kind"] == "favlist" and t_fav2["media_id"] == 98765

    assert parser.parse_target("toview", resolve_short=False)["kind"] == "watchlater"
    assert parser.parse_target("https://www.bilibili.com/list/watchlater", resolve_short=False)["kind"] == "watchlater"
    assert parser.parse_target("not a valid link", resolve_short=False)["kind"] == "unknown"


def test_short_url_resolution_from_share_text():
    fake_resp = MagicMock()
    fake_resp.url = "https://www.bilibili.com/video/BV1AbCdEfGhI?p=2"
    with patch("bilidown.parser.requests.get", return_value=fake_resp):
        t = parser.parse_target("【超赞视频】 https://b23.tv/abc1234 快来看看", resolve_short=True)
    assert t["kind"] == "video"
    assert t["bvid"] == "BV1AbCdEfGhI"
    assert t["page"] == 2


def test_split_batch_inputs():
    text = """
    【视频1】 https://www.bilibili.com/video/BV1xx411c7mD
    BV1AbCdEfGhI
    https://www.bilibili.com/video/BV1xx411c7mD
    """
    urls = parser.split_batch_inputs(text)
    assert urls == [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "BV1AbCdEfGhI",
    ]


def test_format_filename_and_safe_remote_dir():
    name = parser.format_filename(
        "{index}_{owner} - {title} [{quality}]",
        title="测试:视频/名称",
        index=3,
        owner="UP主",
        quality="4K",
    )
    assert name == "003_UP主 - 测试_视频_名称 [4K]"

    assert parser.safe_remote_dir("视频\\B站/") == "/视频/B站"
    with pytest.raises(ValueError):
        parser.safe_remote_dir("/视频/../secret")


# ─── Stream Codec Preference & Bug Fixes ─────────────────────────────────

def test_select_quality_codec_preference():
    playurl = {
        "dash": {
            "video": [
                {"id": 80, "codecid": 7, "bandwidth": 2000},   # AVC
                {"id": 80, "codecid": 12, "bandwidth": 1500},  # HEVC
                {"id": 80, "codecid": 13, "bandwidth": 1200},  # AV1
            ],
            "audio": [{"id": 30280, "bandwidth": 192000}],
        }
    }
    v_auto, _ = media.select_best_quality(playurl, target_qn=80, codec="auto")
    assert v_auto["codecid"] == 12  # HEVC preferred in auto

    v_avc, _ = media.select_best_quality(playurl, target_qn=80, codec="avc")
    assert v_avc["codecid"] == 7

    v_av1, _ = media.select_best_quality(playurl, target_qn=80, codec="av1")
    assert v_av1["codecid"] == 13


def test_ass_colour_rgb_to_bgr_fix():
    # Red (0xFF0000) in RGB MUST become &H000000FF& in ASS BGR
    assert danmaku.ass_colour(0xFF0000) == "&H000000FF&"
    # Blue (0x0000FF) in RGB MUST become &H00FF0000& in ASS BGR
    assert danmaku.ass_colour(0x0000FF) == "&H00FF0000&"
    # Green (0x00FF00) stays &H0000FF00&
    assert danmaku.ass_colour(0x00FF00) == "&H0000FF00&"
    # White stays &H00FFFFFF&
    assert danmaku.ass_colour(0xFFFFFF) == "&H00FFFFFF&"


def test_build_combined_ass_custom_style(tmp_path):
    xml = tmp_path / "dm.xml"
    xml.write_text('<i><d p="1.0,1,25,16711680,0,0,1,1,10">红色弹幕</d></i>', encoding="utf-8")
    out = danmaku.build_combined_ass(
        xml, [], tmp_path / "out.ass", 1920, 1080, area=0.4, opacity=0.5, font_scale=1.2, speed=1.25
    )
    content = out.read_text(encoding="utf-8-sig")
    assert "&H000000FF&" in content  # true red in BGR
    assert "&H80FFFFFF" in content   # ~50% opacity alpha=0x80


def test_bangumi_episode_forwards_quality_fix(tmp_path):
    ep = {"ep_id": 201, "aid": 3001, "cid": 4001, "title": "第一话"}
    with patch("bilidown.engine.get_bangumi_playurl", return_value={"dash": {}}) as m_pu, \
            patch("bilidown.engine.download_playurl_streams", return_value=True) as m_dl:
        ok = engine.download_bangumi_episode("测试番剧", ep, tmp_path, quality="4K", codec="hevc")
    assert ok is True
    assert m_pu.call_args.args[3] == 120
    assert m_dl.call_args.kwargs["quality"] == "4K"
    assert m_dl.call_args.kwargs["codec"] == "hevc"


def test_qr_poll_recognizes_86090_scanned():
    resp = MagicMock()
    resp.json.return_value = {"code": 0, "data": {"code": 86090}}
    with patch("bilidown.api.requests.get", return_value=resp):
        assert api.poll_login_status("dummy_key")["status"] == "scanned"


# ─── SQLite HistoryStore & TaskManager ───────────────────────────────────

def test_history_store_crud(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    store.save_record(
        {
            "id": "task001",
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "Python 重构教程",
            "media_type": "video",
            "quality": "4K",
            "status": "done",
            "total_bytes": 104857600,
            "files": ["/downloads/test.mp4"],
        }
    )
    rows = store.list_records(query="重构")
    assert len(rows) == 1
    assert rows[0]["title"] == "Python 重构教程"
    assert rows[0]["files"] == ["/downloads/test.mp4"]

    stats = store.stats()
    assert stats["total_tasks"] == 1
    assert stats["done_tasks"] == 1
    assert stats["total_bytes"] == 104857600

    assert store.delete_record("task001") is True
    assert store.list_records() == []


def test_task_manager_queue_and_retry(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    mgr = TaskManager(store=store, work_dir=tmp_path / "work")
    # Pause dispatcher so we can inspect queued state deterministically
    mgr._shutdown.set()

    with patch("bilidown.manager.effective_cloudreve_config", return_value=None):
        t = mgr.enqueue(
            {
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": "测试任务",
                "quality": "1080P",
                "output_dir": str(tmp_path / "out"),
                "upload_cloudreve": False,
            }
        )
    assert t["status"] == "queued"
    assert mgr.legacy_status()["id"] == t["id"]

    # Simulate partial failure and retry only failed ep_ids
    with mgr._lock:
        mgr._tasks[t["id"]]["status"] = "partial"
        mgr._tasks[t["id"]]["failed"] = [{"ep_id": 102, "title": "第2集", "error": "timeout"}]
    with patch("bilidown.manager.effective_cloudreve_config", return_value=None):
        retry_t = mgr.retry_failed(t["id"])
    assert retry_t["id"] != t["id"]
    assert "(重试)" in retry_t["title"]
    assert mgr.cancel_task(retry_t["id"]) is True
    assert mgr.get_task(retry_t["id"])["status"] == "stopped"


# ─── Flask Web Studio API Tests ──────────────────────────────────────────

def test_web_studio_routes_and_security(tmp_path):
    store = HistoryStore(tmp_path / "web_h.db")
    mgr = TaskManager(store=store, work_dir=tmp_path / "work")
    mgr._shutdown.set()

    # 1) Server mode blocks desktop OS folder picker & open-path
    server_app = create_app(desktop_mode=False, manager=mgr, store=store)
    client = server_app.test_client()

    idx = client.get("/")
    assert idx.status_code == 200
    html = idx.get_data(as_text=True)
    # Relative asset links required for /api/bili/ reverse proxy compatibility!
    assert 'href="static/css/studio.css' in html
    assert 'src="static/js/studio.js' in html
    assert 'href="/static/' not in html

    sys_info = client.get("/api/system/info").get_json()
    assert sys_info["desktop_mode"] is False
    assert "qualities" in sys_info

    assert client.post("/api/system/pick-folder", json={}).status_code == 403
    assert client.post("/api/system/open-path", json={"path": "."}).status_code == 403

    # Cover proxy rejects non-bilibili domains
    assert client.get("/api/cover?url=https://evil.example.com/a.jpg").status_code == 400

    # Legacy status & v2 tasks
    assert client.get("/api/status").get_json()["status"] == "idle"
    assert client.get("/api/tasks").get_json()["tasks"] == []

    # 2) Inspect route mocked
    mock_preview = {"kind": "video", "media_type": "video", "title": "演示", "items": [{"id": 1}]}
    with patch("bilidown.api.inspect_url", return_value=mock_preview):
        r = client.get("/api/info?url=BV1xx411c7mD")
        assert r.status_code == 200
        assert r.get_json()["title"] == "演示"

    # 3) Batch parse route
    br = client.post("/api/parse-batch", json={"text": "BV1xx411c7mD\nhttps://b23.tv/abc"})
    assert br.status_code == 200
    assert br.get_json()["count"] == 2
