"""Flask application factory for BiliDown Studio (Desktop & Cloudreve Server modes)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from flask import Flask, Response, abort, jsonify, render_template, request

from .. import __version__, api
from ..cloudreve import CloudreveWebDAV
from ..config import (
    clear_credentials,
    default_output_dir,
    effective_cloudreve_config,
    load_cloudreve_config,
    load_settings,
    save_cookie_string,
    save_settings,
)
from ..constants import AUDIO_FORMATS, HEADERS, QUALITY_MAP, SUBPROCESS_FLAGS
from ..manager import TaskManager
from ..media import ffmpeg_available, ffmpeg_version
from ..parser import cover_host_ok, split_batch_inputs
from ..store import HistoryStore
from ..wbi import invalidate_wbi_cache


def _is_localhost() -> bool:
    return (request.remote_addr or "") in ("127.0.0.1", "::1", "localhost")


def _open_native_folder_picker(initial_dir: str = "") -> str:
    """Open a native OS folder picker dialog safely (works in both Python & frozen PyInstaller .exe)."""
    if sys.platform == "win32":
        escaped = (initial_dir or "").replace("'", "''")
        ps_script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dlg.Description = '选择 BiliDown 保存目录'; "
            "$dlg.UseDescriptionForTitle = $true; "
            "$dlg.ShowNewFolderButton = $true; "
            + (f"if (Test-Path -LiteralPath '{escaped}') {{ $dlg.SelectedPath = '{escaped}' }}; " if escaped else "")
            + "$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true; "
            "if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dlg.SelectedPath }; "
            "$owner.Dispose(); $dlg.Dispose()"
        )
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            **SUBPROCESS_FLAGS,
        )
        return (proc.stdout or "").strip()

    if not getattr(sys, "frozen", False):
        code = (
            "import tkinter as tk; from tkinter import filedialog; "
            "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
            "d = filedialog.askdirectory(title='选择保存目录', initialdir=r'''"
            + initial_dir.replace("'''", "")
            + "'''); print(d or ''); root.destroy()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return (proc.stdout or "").strip()

    return ""


def _open_in_file_manager(target_path: Path) -> None:
    target = Path(target_path).resolve()
    if not target.exists():
        # Fall back to parent if exact file missing
        if target.parent.exists():
            target = target.parent
        else:
            raise FileNotFoundError(f"路径不存在: {target}")

    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer", f"/select,{target}"])
        else:
            os.startfile(str(target))
    elif sys.platform == "darwin":
        if target.is_file():
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["open", str(target)])
    else:
        folder = target.parent if target.is_file() else target
        subprocess.Popen(["xdg-open", str(folder)])


def _play_local_file(target_path: Path) -> None:
    target = Path(target_path).resolve()
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"文件不存在: {target}")
    if sys.platform == "win32":
        os.startfile(str(target))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def create_app(
    *,
    desktop_mode: Optional[bool] = None,
    manager: Optional[TaskManager] = None,
    store: Optional[HistoryStore] = None,
) -> Flask:
    """Create and configure the BiliDown Studio Flask app."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        web_dir = Path(sys._MEIPASS) / "bilidown" / "web"
    else:
        web_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(web_dir / "templates"),
        static_folder=str(web_dir / "static"),
        static_url_path="/static",
    )

    if desktop_mode is None:
        # Auto-detect: if cloudreve_config.json exists, default to server mode; otherwise desktop mode
        desktop_mode = load_cloudreve_config() is None

    app.config["DESKTOP_MODE"] = bool(desktop_mode)
    history_store = store or HistoryStore()
    task_manager = manager or TaskManager(store=history_store)
    app.extensions["task_manager"] = task_manager
    app.extensions["history_store"] = history_store

    def _guard_desktop():
        if not app.config["DESKTOP_MODE"] or not _is_localhost():
            abort(403, description="此操作仅在本地桌面模式可用")

    # ─── Frontend Entry ──────────────────────────────────────────────────

    @app.get("/")
    def index():
        html = render_template("index.html", version=__version__)
        resp = Response(html, mimetype="text/html; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store"
        if request.cookies.get("bili_token"):
            resp.delete_cookie("bili_token")
        return resp

    # ─── System & Settings ───────────────────────────────────────────────

    @app.get("/api/system/info")
    def system_info():
        settings = load_settings()
        cloud_cfg = effective_cloudreve_config(settings)
        disk_target = Path(settings.get("output_dir") or default_output_dir())
        if not disk_target.exists():
            disk_target = Path(__file__).resolve().parent
        try:
            usage = shutil.disk_usage(disk_target)
            disk_free_gb = round(usage.free / (1024 ** 3), 2)
            disk_total_gb = round(usage.total / (1024 ** 3), 2)
        except OSError:
            disk_free_gb, disk_total_gb = 0.0, 0.0

        return jsonify(
            {
                "version": __version__,
                "desktop_mode": app.config["DESKTOP_MODE"],
                "cloudreve_configured": cloud_cfg is not None,
                "cloudreve_remote_dir": cloud_cfg["remote_dir"] if cloud_cfg else settings.get("cloudreve_remote_dir", "/视频/B站"),
                "ffmpeg_ok": ffmpeg_available(),
                "ffmpeg_version": ffmpeg_version(),
                "disk_free_gb": disk_free_gb,
                "disk_total_gb": disk_total_gb,
                "default_output_dir": str(default_output_dir()),
                "qualities": list(QUALITY_MAP.keys()),
                "audio_formats": list(AUDIO_FORMATS),
                "history_stats": history_store.stats(),
            }
        )

    @app.post("/api/system/pick-folder")
    def system_pick_folder():
        _guard_desktop()
        data = request.get_json(silent=True) or {}
        initial = data.get("initial_dir") or load_settings().get("output_dir") or str(default_output_dir())
        try:
            chosen = _open_native_folder_picker(initial)
            return jsonify({"path": chosen})
        except Exception as exc:
            return jsonify({"error": f"打开文件夹对话框失败: {exc}"}), 500

    @app.post("/api/system/open-path")
    def system_open_path():
        _guard_desktop()
        data = request.get_json(silent=True) or {}
        raw_path = (data.get("path") or "").strip()
        if not raw_path:
            return jsonify({"error": "缺少路径参数"}), 400
        try:
            _open_in_file_manager(Path(raw_path))
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/system/play-file")
    def system_play_file():
        _guard_desktop()
        data = request.get_json(silent=True) or {}
        raw_path = (data.get("path") or "").strip()
        if not raw_path:
            return jsonify({"error": "缺少文件路径"}), 400
        try:
            _play_local_file(Path(raw_path))
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/settings")
    def get_settings():
        s = load_settings()
        # Mask password when returning to browser
        safe = dict(s)
        safe["has_cloudreve_password"] = bool(s.get("cloudreve_password"))
        safe["cloudreve_password"] = ""
        return jsonify(safe)

    @app.post("/api/settings")
    def update_settings():
        data = request.get_json(silent=True) or {}
        updates = dict(data)
        # Keep existing password if left blank in UI
        if "cloudreve_password" in updates and updates["cloudreve_password"] == "":
            updates.pop("cloudreve_password")
        saved = save_settings(updates)
        safe = dict(saved)
        safe["has_cloudreve_password"] = bool(saved.get("cloudreve_password"))
        safe["cloudreve_password"] = ""
        return jsonify(safe)

    @app.post("/api/settings/test-cloudreve")
    def test_cloudreve():
        data = request.get_json(silent=True) or {}
        current = load_settings()
        base_url = (data.get("cloudreve_url") or current.get("cloudreve_url") or "").strip()
        username = (data.get("cloudreve_username") or current.get("cloudreve_username") or "").strip()
        password = data.get("cloudreve_password") or current.get("cloudreve_password") or ""
        if not base_url or not username:
            return jsonify({"ok": False, "message": "请填写 WebDAV 地址与用户名"}), 400
        dav = CloudreveWebDAV(base_url, username, password)
        ok, msg = dav.test_connection()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    # ─── Media Inspection & Cover Proxy ──────────────────────────────────

    @app.get("/api/info")
    def inspect_info():
        url = (request.args.get("url") or "").strip()
        if not url:
            return jsonify({"error": "请输入 B站链接、BV号或分享文本"}), 400
        try:
            return jsonify(api.inspect_url(url))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.post("/api/parse-batch")
    def parse_batch():
        data = request.get_json(silent=True) or {}
        text = data.get("text") or ""
        items = split_batch_inputs(text)
        return jsonify({"urls": items, "count": len(items)})

    @app.get("/api/cover")
    def proxy_cover():
        url = request.args.get("url", "")
        if url.startswith("//"):
            url = "https:" + url
        host = (urlparse(url).hostname or "").lower()
        if not url.startswith(("http://", "https://")) or not cover_host_ok(host):
            abort(400)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            response = Response(
                resp.content,
                mimetype=resp.headers.get("Content-Type", "image/jpeg"),
            )
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response
        except Exception:
            abort(404)

    # ─── Task Queue API (v2) ─────────────────────────────────────────────

    @app.get("/api/tasks")
    def list_tasks():
        resp = jsonify(
            {
                "tasks": task_manager.list_tasks(),
                "counts": task_manager.summary_counts(),
            }
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/tasks")
    def create_tasks():
        data = request.get_json(silent=True) or {}
        # Support single task or batch array under `tasks`
        if isinstance(data.get("tasks"), list):
            created = []
            errors = []
            for item in data["tasks"]:
                try:
                    created.append(task_manager.enqueue(item))
                except ValueError as exc:
                    errors.append(str(exc))
            if not created and errors:
                return jsonify({"error": errors[0], "errors": errors}), 400
            return jsonify({"tasks": created, "errors": errors}), 202

        try:
            task = task_manager.enqueue(data)
            return jsonify(task), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/tasks/<task_id>")
    def get_task(task_id: str):
        task = task_manager.get_task(task_id)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        resp = jsonify(task)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/tasks/<task_id>/stop")
    def stop_single_task(task_id: str):
        if task_manager.cancel_task(task_id):
            return jsonify({"ok": True})
        return jsonify({"error": "任务未在运行或排队中"}), 409

    @app.post("/api/tasks/<task_id>/retry")
    def retry_single_task(task_id: str):
        try:
            new_task = task_manager.retry_failed(task_id)
            return jsonify(new_task), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.delete("/api/tasks/<task_id>")
    def delete_single_task(task_id: str):
        if task_manager.remove_task(task_id):
            return jsonify({"ok": True})
        return jsonify({"error": "无法删除正在运行的任务"}), 409

    @app.post("/api/tasks/clear")
    def clear_finished_tasks():
        removed = task_manager.clear_finished()
        return jsonify({"ok": True, "removed": removed})

    # ─── Legacy v1 Compatibility Routes ──────────────────────────────────

    @app.get("/api/status")
    def legacy_status():
        resp = jsonify(task_manager.legacy_status())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/jobs")
    def legacy_create_job():
        data = request.get_json(silent=True) or {}
        try:
            task = task_manager.enqueue(data)
            return jsonify({"id": task["id"], "status": task["status"]}), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/stop")
    def legacy_stop():
        if task_manager.stop_active():
            return jsonify({"ok": True})
        return jsonify({"error": "没有正在运行的任务"}), 409

    # ─── History API ─────────────────────────────────────────────────────

    @app.get("/api/history")
    def list_history():
        q = request.args.get("q", "")
        try:
            limit = min(500, max(1, int(request.args.get("limit", 100))))
        except ValueError:
            limit = 100
        return jsonify(
            {
                "records": history_store.list_records(query=q, limit=limit),
                "stats": history_store.stats(),
            }
        )

    @app.delete("/api/history/<record_id>")
    def delete_history_item(record_id: str):
        ok = history_store.delete_record(record_id)
        return jsonify({"ok": ok})

    @app.delete("/api/history")
    def clear_history():
        history_store.clear()
        return jsonify({"ok": True})

    # ─── Account, QR Login, Watch Later, Favorites ───────────────────────

    @app.get("/api/login/status")
    def login_status():
        resp = jsonify(api.get_login_profile())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/login/qr")
    def login_qr():
        try:
            result = api.generate_qrcode()
            if not result:
                return jsonify({"error": "获取二维码失败"}), 502
            return jsonify({"key": result["key"], "image": result["image_data_url"]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.get("/api/login/poll")
    def login_poll():
        key = request.args.get("key", "")
        if not key:
            return jsonify({"error": "缺少 qrcode_key"}), 400
        try:
            return jsonify(api.poll_login_status(key))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.post("/api/login/cookie")
    def login_cookie():
        data = request.get_json(silent=True) or {}
        cookie = (data.get("cookie") or "").strip()
        if not cookie:
            return jsonify({"error": "请输入 Cookie"}), 400
        try:
            profile = api.get_login_profile(cookie=cookie)
            if not profile.get("logged_in"):
                raise ValueError("Cookie 登录校验失败，请确认已登录 bilibili.com")
            save_cookie_string(cookie)
            invalidate_wbi_cache()
            return jsonify({"ok": True, **profile})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/login/logout")
    def login_logout():
        clear_credentials()
        invalidate_wbi_cache()
        return jsonify({"ok": True})

    @app.get("/api/account/watchlater")
    def account_watchlater():
        try:
            raw_list = api.get_watch_later()
            items = [
                {
                    "bvid": it.get("bvid", ""),
                    "title": it.get("title", ""),
                    "cover": it.get("pic", ""),
                    "owner": (it.get("owner") or {}).get("name", ""),
                    "duration": it.get("duration") or 0,
                    "view": (it.get("stat") or {}).get("view", 0),
                    "pages": it.get("videos") or 1,
                }
                for it in raw_list
                if it.get("bvid")
            ]
            return jsonify({"items": items, "count": len(items)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/account/fav-folders")
    def account_fav_folders():
        profile = api.get_login_profile()
        if not profile.get("logged_in") or not profile.get("mid"):
            return jsonify({"error": "请先登录 B站账号"}), 401
        try:
            folders = api.get_user_fav_folders(int(profile["mid"]))
            return jsonify(
                {
                    "folders": [
                        {
                            "id": f.get("id"),
                            "title": f.get("title", ""),
                            "count": f.get("media_count", 0),
                        }
                        for f in folders
                    ]
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.get("/api/account/fav-items")
    def account_fav_items():
        media_id = request.args.get("media_id", "")
        if not media_id.isdigit():
            return jsonify({"error": "无效的收藏夹 ID"}), 400
        try:
            fav = api.get_favlist_info(int(media_id))
            items = [
                {
                    "bvid": m.get("bvid", ""),
                    "title": m.get("title", ""),
                    "cover": m.get("cover", ""),
                    "owner": (m.get("upper") or {}).get("name", ""),
                    "duration": m.get("duration") or 0,
                    "view": (m.get("cnt_info") or {}).get("play", 0),
                    "pages": m.get("page") or 1,
                }
                for m in fav["medias"]
                if m.get("bvid")
            ]
            return jsonify(
                {
                    "title": fav["title"],
                    "media_id": fav["media_id"],
                    "items": items,
                    "count": len(items),
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    return app
