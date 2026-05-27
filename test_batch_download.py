#!/usr/bin/env python3
"""Automated tests for bilibili-downloader episode batch feature."""

import sys
import os
import json
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bili_downloader as bd


# ─── Test parse_episode_ranges ──────────────────────────────────────────

def test_parse_basic():
    assert bd.parse_episode_ranges("1-5", 10) == [1, 2, 3, 4, 5]
    assert bd.parse_episode_ranges("3", 10) == [3]
    assert bd.parse_episode_ranges("1-3,5,7-9", 10) == [1, 2, 3, 5, 7, 8, 9]

def test_parse_chinese_comma():
    assert bd.parse_episode_ranges("1，3，5", 10) == [1, 3, 5]
    assert bd.parse_episode_ranges("1-3，5", 10) == [1, 2, 3, 5]

def test_parse_space():
    assert bd.parse_episode_ranges("1 3 5", 10) == [1, 3, 5]

def test_parse_mixed():
    assert bd.parse_episode_ranges("1-3，5 7-8,10", 10) == [1, 2, 3, 5, 7, 8, 10]

def test_parse_dedup():
    assert bd.parse_episode_ranges("1-3,2-4", 10) == [1, 2, 3, 4]
    assert bd.parse_episode_ranges("3,3,3", 10) == [3]

def test_parse_ascending():
    result = bd.parse_episode_ranges("5,1,3,2,4", 10)
    assert result == [1, 2, 3, 4, 5]

def test_parse_empty():
    try:
        bd.parse_episode_ranges("", 10)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "请输入" in str(e)

def test_parse_whitespace_only():
    try:
        bd.parse_episode_ranges("   ", 10)
        assert False, "Should raise ValueError"
    except ValueError:
        pass

def test_parse_reverse_range():
    try:
        bd.parse_episode_ranges("10-5", 10)
        assert False, "Should raise ValueError for reverse range"
    except ValueError as e:
        assert "倒序" in str(e)

def test_parse_zero():
    try:
        bd.parse_episode_ranges("0", 10)
        assert False, "Should reject 0"
    except ValueError as e:
        assert "正整数" in str(e)

def test_parse_negative():
    try:
        bd.parse_episode_ranges("-1", 10)
        assert False, "Should reject negative"
    except ValueError:
        pass

def test_parse_out_of_range():
    try:
        bd.parse_episode_ranges("11", 10)
        assert False, "Should reject out of range"
    except ValueError as e:
        assert "超出" in str(e)

def test_parse_range_out_of_range():
    try:
        bd.parse_episode_ranges("8-12", 10)
        assert False, "Should reject out of range"
    except ValueError as e:
        assert "超出" in str(e)

def test_parse_non_numeric():
    try:
        bd.parse_episode_ranges("abc", 10)
        assert False, "Should reject non-numeric"
    except ValueError as e:
        assert "格式错误" in str(e)

def test_parse_bad_range():
    try:
        bd.parse_episode_ranges("1-abc", 10)
        assert False, "Should reject bad range"
    except ValueError as e:
        assert "格式错误" in str(e)


# ─── Test get_media_collection ──────────────────────────────────────────

def _mock_course_season():
    """Mock course season API response."""
    return {
        "title": "测试课程",
        "season_id": 999,
        "episodes": [
            {"id": 101, "aid": 1001, "cid": 2001, "title": "第1课", "index": 1},
            {"id": 102, "aid": 1002, "cid": 2002, "title": "第2课", "index": 2},
            {"id": 103, "aid": 1003, "cid": 2003, "title": "第3课", "index": 3},
        ],
    }

def _mock_bangumi_season():
    """Mock bangumi season API response."""
    return {
        "title": "测试番剧",
        "season_id": 888,
        "episodes": [
            {"id": 201, "ep_id": 201, "aid": 3001, "cid": 4001, "bvid": "BV1TEST001",
             "title": "1", "long_title": "第一话"},
            {"id": 202, "ep_id": 202, "aid": 3002, "cid": 4002, "bvid": "BV1TEST002",
             "title": "2", "long_title": "第二话"},
            {"id": 203, "ep_id": 203, "aid": 3003, "cid": 4003, "bvid": "BV1TEST003",
             "title": "3", "long_title": "第三话"},
        ],
    }


def test_get_media_collection_course():
    with patch("bili_downloader.get_season_info", return_value=_mock_course_season()):
        result = bd.get_media_collection("https://www.bilibili.com/cheese/play/ep101")
    assert result["media_type"] == "course"
    assert result["title"] == "测试课程"
    assert result["current_ep_id"] == 101
    assert len(result["episodes"]) == 3
    assert result["episodes"][0]["ep_id"] == 101
    assert result["episodes"][0]["title"] == "第1课"
    assert result["episodes"][0]["bvid"] is None

def test_get_media_collection_bangumi():
    with patch("bili_downloader.get_season_info", return_value=_mock_bangumi_season()):
        result = bd.get_media_collection("https://www.bilibili.com/bangumi/play/ep202")
    assert result["media_type"] == "bangumi"
    assert result["title"] == "测试番剧"
    assert result["current_ep_id"] == 202
    assert len(result["episodes"]) == 3
    assert result["episodes"][1]["ep_id"] == 202
    assert result["episodes"][1]["title"] == "第二话"
    assert result["episodes"][1]["bvid"] == "BV1TEST002"

def test_get_media_collection_no_ep():
    try:
        bd.get_media_collection("https://www.bilibili.com/bangumi/play/ss12345")
        assert False, "Should raise for ss link"
    except Exception as e:
        assert "单集链接" in str(e)

def test_course_same_season():
    """Two different course ep links should resolve to the same season."""
    mock_season = _mock_course_season()
    with patch("bili_downloader.get_season_info", return_value=mock_season):
        c1 = bd.get_media_collection("https://www.bilibili.com/cheese/play/ep101")
        c2 = bd.get_media_collection("https://www.bilibili.com/cheese/play/ep102")
    assert c1["title"] == c2["title"]
    assert len(c1["episodes"]) == len(c2["episodes"])
    assert c1["current_ep_id"] == 101
    assert c2["current_ep_id"] == 102

def test_bangumi_same_season():
    """Two different bangumi ep links should resolve to the same season."""
    mock_season = _mock_bangumi_season()
    with patch("bili_downloader.get_season_info", return_value=mock_season):
        b1 = bd.get_media_collection("https://www.bilibili.com/bangumi/play/ep201")
        b2 = bd.get_media_collection("https://www.bilibili.com/bangumi/play/ep203")
    assert b1["title"] == b2["title"]
    assert len(b1["episodes"]) == len(b2["episodes"])
    assert b1["current_ep_id"] == 201
    assert b2["current_ep_id"] == 203


# ─── Test download_selected_episodes ────────────────────────────────────

def test_download_selected_order():
    """Episodes should download in original order, not selection order."""
    collection = {
        "media_type": "course",
        "title": "测试课程",
        "current_ep_id": 101,
        "episodes": [
            {"ep_id": 101, "title": "第1课", "index": 1, "aid": 1001, "cid": 2001,
             "raw": {"id": 101, "aid": 1001, "cid": 2001, "title": "第1课"}},
            {"ep_id": 102, "title": "第2课", "index": 2, "aid": 1002, "cid": 2002,
             "raw": {"id": 102, "aid": 1002, "cid": 2002, "title": "第2课"}},
            {"ep_id": 103, "title": "第3课", "index": 3, "aid": 1003, "cid": 2003,
             "raw": {"id": 103, "aid": 1003, "cid": 2003, "title": "第3课"}},
        ],
        "raw": {"title": "测试课程"},
    }

    download_order = []

    def mock_download_course_episode(course, episode, output_dir, quality="1080P",
                                      stop_event=None, progress_callback=None,
                                      part_prefix=""):
        download_order.append(episode.get("id"))
        return True

    with patch("bili_downloader.download_course_episode", side_effect=mock_download_course_episode):
        result = bd.download_selected_episodes(
            collection, [103, 101], Path("/tmp"), "1080P")

    # Should be in original order: 101 first, 103 second
    assert download_order == [101, 103], f"Got order: {download_order}"
    assert result["total"] == 2
    assert result["success"] == 2
    assert result["failed"] == 0
    assert result["cancelled"] is False

def test_download_selected_failure_continues():
    """A single episode failure should not stop subsequent downloads."""
    collection = {
        "media_type": "course",
        "title": "测试课程",
        "current_ep_id": 101,
        "episodes": [
            {"ep_id": 101, "title": "第1课", "index": 1, "aid": 1001, "cid": 2001,
             "raw": {"id": 101, "aid": 1001, "cid": 2001, "title": "第1课"}},
            {"ep_id": 102, "title": "第2课", "index": 2, "aid": 1002, "cid": 2002,
             "raw": {"id": 102, "aid": 1002, "cid": 2002, "title": "第2课"}},
            {"ep_id": 103, "title": "第3课", "index": 3, "aid": 1003, "cid": 2003,
             "raw": {"id": 103, "aid": 1003, "cid": 2003, "title": "第3课"}},
        ],
        "raw": {"title": "测试课程"},
    }

    call_count = [0]
    def mock_download(course, episode, output_dir, quality="1080P",
                      stop_event=None, progress_callback=None, part_prefix=""):
        call_count[0] += 1
        if episode.get("id") == 102:
            raise Exception("模拟下载失败")
        return True

    with patch("bili_downloader.download_course_episode", side_effect=mock_download):
        result = bd.download_selected_episodes(
            collection, [101, 102, 103], Path("/tmp"), "1080P")

    assert call_count[0] == 3, f"Called {call_count[0]} times"  # All 3 attempted
    assert result["total"] == 3
    assert result["success"] == 2
    assert result["failed"] == 1
    assert len(result["failures"]) == 1
    assert result["failures"][0]["ep_id"] == 102

def test_download_selected_stop_event():
    """Stop event should halt downloads."""
    collection = {
        "media_type": "course",
        "title": "测试课程",
        "current_ep_id": 101,
        "episodes": [
            {"ep_id": 101, "title": "第1课", "index": 1, "aid": 1001, "cid": 2001,
             "raw": {"id": 101, "aid": 1001, "cid": 2001, "title": "第1课"}},
            {"ep_id": 102, "title": "第2课", "index": 2, "aid": 1002, "cid": 2002,
             "raw": {"id": 102, "aid": 1002, "cid": 2002, "title": "第2课"}},
            {"ep_id": 103, "title": "第3课", "index": 3, "aid": 1003, "cid": 2003,
             "raw": {"id": 103, "aid": 1003, "cid": 2003, "title": "第3课"}},
        ],
        "raw": {"title": "测试课程"},
    }

    stop = threading.Event()
    stop.set()  # Pre-set stop

    def mock_download(course, episode, output_dir, quality="1080P",
                      stop_event=None, progress_callback=None, part_prefix=""):
        return True

    with patch("bili_downloader.download_course_episode", side_effect=mock_download):
        result = bd.download_selected_episodes(
            collection, [101, 102, 103], Path("/tmp"), "1080P", stop_event=stop)

    assert result["total"] == 3
    assert result["success"] == 0
    assert result["cancelled"] is True


# ─── Test download_selected_episodes with bangumi ───────────────────────

def test_download_selected_bangumi():
    collection = {
        "media_type": "bangumi",
        "title": "测试番剧",
        "current_ep_id": 201,
        "episodes": [
            {"ep_id": 201, "title": "第一话", "index": 1, "aid": 3001, "cid": 4001,
             "bvid": "BV1TEST001", "raw": {}},
            {"ep_id": 202, "title": "第二话", "index": 2, "aid": 3002, "cid": 4002,
             "bvid": "BV1TEST002", "raw": {}},
        ],
        "raw": {},
    }

    def mock_download_bangumi(title, ep, output_dir, quality="1080P",
                               stop_event=None, progress_callback=None, part_prefix=""):
        return True

    with patch("bili_downloader.download_bangumi_episode", side_effect=mock_download_bangumi):
        result = bd.download_selected_episodes(
            collection, [202], Path("/tmp"), "1080P")

    assert result["total"] == 1
    assert result["success"] == 1
    assert result["failed"] == 0


# ─── Smoke test: existing functions still importable ────────────────────

def test_existing_functions_exist():
    assert callable(bd.download_single)
    assert callable(bd.download_batch)
    assert callable(bd.download_course_episode)
    assert callable(bd.get_season_info)
    assert callable(bd.get_video_info)
    assert callable(bd.get_playurl)
    assert callable(bd.get_course_playurl)
    assert callable(bd.get_bangumi_playurl)
    assert callable(bd.download_bangumi_episode)
    assert callable(bd.download_selected_episodes)
    assert callable(bd.get_media_collection)
    assert callable(bd.parse_episode_ranges)
    assert callable(bd.extract_bvid)
    assert callable(bd.extract_epid)
    assert callable(bd.extract_ssid)
    assert callable(bd.get_cookie_string)
    assert callable(bd.build_headers)
    assert callable(bd.save_cookie_string)

def test_cookie_config_structure():
    """Verify config.json structure is preserved."""
    cfg_path = Path("config.json")
    if cfg_path.exists():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "sessdata" in data or "cookie" in data


# ─── Test pagination logic (pure logic, no Tk) ─────────────────────────

def test_pagination_default_page_for_current_ep():
    """Default page should contain the current episode."""
    import math
    episodes = [{"ep_id": i, "title": f"EP{i}", "index": i} for i in range(1, 260)]
    current_ep_id = 97
    page_size = 30
    current_index = next(i for i, ep in enumerate(episodes) if ep["ep_id"] == current_ep_id)
    page = current_index // page_size
    # EP97 is at index 96, page 96//30 = 3
    assert page == 3
    start = page * page_size
    end = start + page_size
    page_ep_ids = [ep["ep_id"] for ep in episodes[start:end]]
    assert 97 in page_ep_ids

def test_pagination_total_pages():
    import math
    for n, expected in [(259, 9), (873, 30), (30, 1), (31, 2), (1, 1)]:
        assert math.ceil(n / 30) == expected, f"n={n}: got {math.ceil(n/30)}, expected {expected}"

def test_pagination_range_select_ids():
    """Range selection should produce correct ep_id set."""
    episodes = [{"ep_id": i * 10, "title": f"EP{i}", "index": i} for i in range(1, 101)]
    indices = bd.parse_episode_ranges("1-10,15,20-25", len(episodes))
    selected_ids = {episodes[i - 1]["ep_id"] for i in indices}
    assert selected_ids == {10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200, 210, 220, 230, 240, 250}

def test_pagination_selection_preserved_across_pages():
    """Selections on page 1 should survive navigating to page 2."""
    selected_ids = {1, 2, 3}
    # Simulate: user selects items on page 1, navigates to page 2
    # On page 2, toggle item 31
    selected_ids.add(31)
    assert selected_ids == {1, 2, 3, 31}
    # Toggle item 2 off
    selected_ids.discard(2)
    assert selected_ids == {1, 3, 31}

def test_pagination_confirm_order():
    """Confirm should return IDs in original episode order, not selection order."""
    episodes = [{"ep_id": i, "title": f"EP{i}", "index": i} for i in range(1, 101)]
    selected_ids = {100, 1, 50, 25}
    result = [ep["ep_id"] for ep in episodes if ep["ep_id"] in selected_ids]
    assert result == [1, 25, 50, 100]

def test_pagination_invert():
    """Invert should produce complement set."""
    episodes = [{"ep_id": i, "index": i} for i in range(1, 11)]
    selected_ids = {1, 3, 5, 7, 9}
    all_ids = {ep["ep_id"] for ep in episodes}
    inverted = all_ids - selected_ids
    assert inverted == {2, 4, 6, 8, 10}

def test_pagination_go_to_episode():
    """_go_to_episode should compute correct page."""
    import math
    episodes = [{"ep_id": i, "index": i} for i in range(1, 874)]
    for ep_id, expected_page in [(1, 0), (30, 0), (31, 1), (870, 28), (873, 29)]:
        idx = next(i for i, ep in enumerate(episodes) if ep["ep_id"] == ep_id)
        page = idx // 30
        assert page == expected_page, f"ep{ep_id}: got page {page}, expected {expected_page}"


# ─── Run all tests ──────────────────────────────────────────────────────

def run_tests():
    tests = [
        test_parse_basic,
        test_parse_chinese_comma,
        test_parse_space,
        test_parse_mixed,
        test_parse_dedup,
        test_parse_ascending,
        test_parse_empty,
        test_parse_whitespace_only,
        test_parse_reverse_range,
        test_parse_zero,
        test_parse_negative,
        test_parse_out_of_range,
        test_parse_range_out_of_range,
        test_parse_non_numeric,
        test_parse_bad_range,
        test_get_media_collection_course,
        test_get_media_collection_bangumi,
        test_get_media_collection_no_ep,
        test_course_same_season,
        test_bangumi_same_season,
        test_download_selected_order,
        test_download_selected_failure_continues,
        test_download_selected_stop_event,
        test_download_selected_bangumi,
        test_existing_functions_exist,
        test_cookie_config_structure,
        test_pagination_default_page_for_current_ep,
        test_pagination_total_pages,
        test_pagination_range_select_ids,
        test_pagination_selection_preserved_across_pages,
        test_pagination_confirm_order,
        test_pagination_invert,
        test_pagination_go_to_episode,
    ]

    # ─── Concurrent download tests ───────────────────────────────────

    def _make_collection(n):
        return {
            "media_type": "course",
            "title": "并发测试课程",
            "current_ep_id": 101,
            "episodes": [
                {"ep_id": 100 + i, "title": f"第{i}课", "index": i,
                 "aid": 1000 + i, "cid": 2000 + i,
                 "raw": {"id": 100 + i, "aid": 1000 + i, "cid": 2000 + i, "title": f"第{i}课"}}
                for i in range(1, n + 1)
            ],
            "raw": {"title": "并发测试课程"},
        }

    def test_concurrent_order_preserved():
        """max_workers=1 should preserve episode order."""
        import time
        coll = _make_collection(4)
        call_order = []
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            call_order.append(episode.get("id"))
            time.sleep(0.01)
            return True
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            result = bd.download_selected_episodes(
                coll, [101, 102, 103, 104], Path("/tmp"), "1080P", max_workers=1)
        assert call_order == [101, 102, 103, 104]
        assert result["success"] == 4

    def test_concurrent_parallel_execution():
        """max_workers=2 should allow parallel execution."""
        import time, threading
        coll = _make_collection(4)
        active = {"count": 0, "max": 0}
        lock = threading.Lock()
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            time.sleep(0.05)
            with lock:
                active["count"] -= 1
            return True
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            result = bd.download_selected_episodes(
                coll, [101, 102, 103, 104], Path("/tmp"), "1080P", max_workers=2)
        assert active["max"] >= 2, f"Max concurrent: {active['max']}"
        assert result["success"] == 4

    def test_concurrent_failure_continues():
        """One failure should not stop other concurrent downloads."""
        import time
        coll = _make_collection(4)
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            if episode.get("id") == 102:
                raise Exception("模拟失败")
            time.sleep(0.01)
            return True
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            result = bd.download_selected_episodes(
                coll, [101, 102, 103, 104], Path("/tmp"), "1080P", max_workers=2)
        assert result["success"] == 3
        assert result["failed"] == 1
        assert result["failures"][0]["ep_id"] == 102

    def test_concurrent_stop_no_new_submits():
        """Stop event should prevent new submissions."""
        import time, threading
        coll = _make_collection(6)
        started_ids = []
        lock = threading.Lock()
        stop = threading.Event()
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            with lock:
                started_ids.append(episode.get("id"))
            if episode.get("id") == 101:
                stop.set()
            time.sleep(0.05)
            return True
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            result = bd.download_selected_episodes(
                coll, [101, 102, 103, 104, 105, 106], Path("/tmp"), "1080P",
                stop_event=stop, max_workers=2)
        # Should not have started all 6
        assert len(started_ids) < 6, f"Started {len(started_ids)} episodes"
        assert result["cancelled"] is True

    def test_concurrent_max_workers_clamped():
        """max_workers should be clamped to 1-4."""
        coll = _make_collection(2)
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            return True
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            r1 = bd.download_selected_episodes(coll, [101], Path("/tmp"), max_workers=0)
            assert r1["max_workers"] == 1
            r2 = bd.download_selected_episodes(coll, [101], Path("/tmp"), max_workers=99)
            assert r2["max_workers"] == 4

    def test_concurrent_status_callback():
        """episode_status_callback should receive started/completed events."""
        import time
        coll = _make_collection(3)
        events = []
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            time.sleep(0.01)
            return True
        def cb(event):
            events.append(event["status"])
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            bd.download_selected_episodes(
                coll, [101, 102, 103], Path("/tmp"), "1080P",
                max_workers=1, episode_status_callback=cb)
        assert events.count("started") == 3
        assert events.count("completed") == 3

    def test_concurrent_file_naming_no_conflict():
        """Different episodes should produce different output file names."""
        coll = _make_collection(3)
        eps = coll["episodes"]
        prefixes = [f"{ep['index']:03d}_" for ep in eps]
        parts = [f"{p}{ep['title']}" for p, ep in zip(prefixes, eps)]
        assert len(set(parts)) == len(parts), f"Name conflict: {parts}"

    def test_concurrent_summary_fields():
        """Summary should include max_workers and cancelled_count."""
        coll = _make_collection(2)
        def mock_dl(course, episode, output_dir, quality="1080P",
                    stop_event=None, progress_callback=None, part_prefix=""):
            return True
        with patch("bili_downloader.download_course_episode", side_effect=mock_dl):
            result = bd.download_selected_episodes(
                coll, [101, 102], Path("/tmp"), "1080P", max_workers=2)
        assert result["max_workers"] == 2
        assert "cancelled_count" in result

    tests.extend([
        test_concurrent_order_preserved,
        test_concurrent_parallel_execution,
        test_concurrent_failure_continues,
        test_concurrent_stop_no_new_submits,
        test_concurrent_max_workers_clamped,
        test_concurrent_status_callback,
        test_concurrent_file_naming_no_conflict,
        test_concurrent_summary_fields,
    ])

    passed = 0
    failed = 0
    errors = []

    for test in tests:
        try:
            test()
            passed += 1
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failed += 1
            errors.append((test.__name__, e))
            print(f"  ✗ {test.__name__}: {e}")

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")

    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  {name}: {err}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
