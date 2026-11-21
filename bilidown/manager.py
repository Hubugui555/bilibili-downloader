"""Multi-task download queue manager with live telemetry, Cloudreve upload, and history."""

import collections
import contextlib
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from . import api, engine
from .cloudreve import CloudreveWebDAV
from .config import (
    app_root,
    default_output_dir,
    effective_cloudreve_config,
    load_settings,
)
from .constants import MEDIA_SUFFIXES, QUALITY_MAP
from .danmaku import (
    download_cover,
    download_danmaku,
    download_episode_extras,
    download_extras_and_merge,
    download_subtitle,
)
from .parser import (
    format_filename,
    parse_target,
    safe_filename,
    safe_remote_dir,
)
from .store import HistoryStore


class _TaskStdoutWriter:
    """Capture print() lines from core functions into a task's structured log buffer."""

    def __init__(self, manager: "TaskManager", task_id: str):
        self.manager = manager
        self.task_id = task_id

    def write(self, text: str) -> int:
        if not text:
            return 0
        if "\r" in text:
            text = text.rsplit("\r", 1)[-1]
        for line in text.splitlines():
            clean = line.strip()
            if clean:
                self.manager.add_log(self.task_id, clean)
        return len(text)

    def flush(self) -> None:
        pass


def find_upload_files(root: Path) -> list[Path]:
    """Select media files and matching .srt sidecars for Cloudreve upload."""
    media: list[Path] = []
    subtitles: list[Path] = []
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in MEDIA_SUFFIXES:
            media.append(path)
        elif suffix == ".srt":
            subtitles.append(path)
    media.sort()
    stems = {p.stem for p in media}
    sidecars = sorted(p for p in subtitles if p.stem in stems)
    return media + sidecars


def find_output_files(root: Path, include_extras: bool = False) -> list[Path]:
    """Discover downloaded result files inside root."""
    allowed = set(MEDIA_SUFFIXES)
    if include_extras:
        allowed |= {".srt", ".ass", ".xml", ".jpg", ".jpeg", ".png", ".webp"}
    files = [
        p for p in Path(root).rglob("*")
        if p.is_file() and p.suffix.lower() in allowed and not p.name.endswith(".tmp")
    ]
    files.sort(key=lambda p: p.name)
    return files


class TaskManager:
    """Thread-safe multi-task queue manager for Desktop and Server modes."""

    def __init__(
        self,
        store: Optional[HistoryStore] = None,
        work_dir: Optional[Path] = None,
    ):
        self.store = store or HistoryStore()
        self.work_dir = Path(work_dir) if work_dir else (app_root() / "work")
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = {}
        self._order: list[str] = []  # newest first or chronological
        self._queue: collections.deque[str] = collections.deque()
        self._active_task_id: Optional[str] = None
        self._wakeup = threading.Event()
        self._shutdown = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._dispatcher_loop, name="bilidown-queue", daemon=True
        )
        self._worker_thread.start()

    # ─── Queue & Task Creation ───────────────────────────────────────────

    def enqueue(self, payload: dict) -> dict:
        """Validate payload, create a queued task, and wake the dispatcher."""
        settings = load_settings()
        url = (payload.get("url") or "").strip()
        if not url:
            raise ValueError("请输入视频链接或 BV 号")

        quality = payload.get("quality") or settings.get("quality", "1080P")
        if quality not in QUALITY_MAP:
            raise ValueError(f"不支持的画质: {quality}")

        download_mode = (payload.get("download_mode") or settings.get("download_mode", "video")).lower()
        if download_mode not in ("video", "audio", "extras"):
            download_mode = "video"

        audio_format = (payload.get("audio_format") or settings.get("audio_format", "m4a")).lower()
        if audio_format not in ("m4a", "mp3", "flac"):
            audio_format = "m4a"

        codec = (payload.get("codec") or settings.get("codec", "auto")).lower()
        if codec not in ("auto", "avc", "hevc", "av1"):
            codec = "auto"

        try:
            workers = max(1, min(32, int(payload.get("workers", settings.get("workers", 2)))))
        except (TypeError, ValueError) as exc:
            raise ValueError("并发数必须是正整数") from exc

        cloud_cfg = effective_cloudreve_config(settings)
        use_cloudreve = bool(
            payload.get("upload_cloudreve")
            if "upload_cloudreve" in payload
            else (cloud_cfg is not None)
        )
        if use_cloudreve and cloud_cfg is None:
            raise ValueError("尚未配置 Cloudreve WebDAV，请先在设置中配置")

        remote_dir = ""
        if use_cloudreve:
            raw_remote = payload.get("remote_dir") or (cloud_cfg["remote_dir"] if cloud_cfg else "/视频/B站")
            remote_dir = safe_remote_dir(raw_remote)

        output_dir = (payload.get("output_dir") or settings.get("output_dir") or str(default_output_dir())).strip()

        selected_ids = payload.get("selected_ids")
        if selected_ids is None and payload.get("ep_ids"):
            selected_ids = payload.get("ep_ids")
        if selected_ids is None:
            selected_ids = []

        danmaku_style = {
            "danmaku_area": float(payload.get("danmaku_area", settings.get("danmaku_area", 0.55))),
            "danmaku_opacity": float(payload.get("danmaku_opacity", settings.get("danmaku_opacity", 0.85))),
            "danmaku_font_scale": float(payload.get("danmaku_font_scale", settings.get("danmaku_font_scale", 1.0))),
            "danmaku_speed": float(payload.get("danmaku_speed", settings.get("danmaku_speed", 1.0))),
        }

        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        task = {
            "id": task_id,
            "url": url,
            "title": (payload.get("title") or "").strip() or url,
            "cover": payload.get("cover_url") or "",
            "media_type": payload.get("media_type") or "video",
            "quality": quality,
            "codec": codec,
            "download_mode": download_mode,
            "audio_format": audio_format,
            "workers": workers,
            "output_dir": output_dir,
            "upload_cloudreve": use_cloudreve,
            "remote_dir": remote_dir,
            "keep_local": bool(payload.get("keep_local", settings.get("keep_local", False))),
            "cover_opt": bool(payload.get("cover", settings.get("cover", True))),
            "danmaku": bool(payload.get("danmaku", settings.get("danmaku", True))),
            "subtitle": bool(payload.get("subtitle", settings.get("subtitle", True))),
            "embed_extras": bool(payload.get("embed_extras", settings.get("embed_extras", True))),
            "all_subtitles": (payload.get("subtitle_mode") or settings.get("subtitle_mode", "best_zh")) == "all",
            "cdn_probe": bool(payload.get("cdn_probe", settings.get("cdn_probe", True))),
            "filename_template": payload.get("filename_template") or settings.get("filename_template", "{title}"),
            "danmaku_style": danmaku_style,
            "selected_ids": list(selected_ids),
            "status": "queued",
            "phase": "queued",
            "progress": 0.0,
            "speed_bps": 0.0,
            "speed_history": [],
            "eta_sec": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "cdn_host": "",
            "total_items": max(1, len(selected_ids)) if selected_ids else 1,
            "success_items": 0,
            "failed_items": 0,
            "items": [],
            "log": [],
            "result": [],
            "files": [],
            "failed": [],
            "error": "",
            "resolved_dir": "",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "_stop_event": threading.Event(),
            "_last_speed_sample": 0.0,
        }

        with self._lock:
            self._tasks[task_id] = task
            self._order.insert(0, task_id)
            # Keep memory bounded (last 60 session tasks)
            if len(self._order) > 60:
                old_id = self._order.pop()
                if old_id != self._active_task_id:
                    self._tasks.pop(old_id, None)
            self._queue.append(task_id)
            self._wakeup.set()
            return self._public_view(task)

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task["status"] == "queued":
                task["status"] = "stopped"
                task["phase"] = "stopped"
                task["finished_at"] = time.time()
                task["_stop_event"].set()
                try:
                    self._queue.remove(task_id)
                except ValueError:
                    pass
                self._append_log_locked(task, "任务已从队列中取消")
                return True
            if task["status"] == "running":
                task["_stop_event"].set()
                self._append_log_locked(task, "正在停止任务...")
                return True
            return False

    def stop_active(self) -> bool:
        with self._lock:
            if not self._active_task_id:
                return False
            return self.cancel_task(self._active_task_id)

    def remove_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task["status"] == "running":
                return False
            if task["status"] == "queued":
                try:
                    self._queue.remove(task_id)
                except ValueError:
                    pass
            self._tasks.pop(task_id, None)
            if task_id in self._order:
                self._order.remove(task_id)
            return True

    def clear_finished(self) -> int:
        with self._lock:
            removable = [
                tid for tid in self._order
                if tid in self._tasks and self._tasks[tid]["status"] not in ("queued", "running")
            ]
            for tid in removable:
                self._tasks.pop(tid, None)
                self._order.remove(tid)
            return len(removable)

    def retry_failed(self, task_id: str) -> dict:
        """Create a focused retry task for only the failed items of an earlier task."""
        with self._lock:
            src = self._tasks.get(task_id)
            if not src:
                raise ValueError("未找到原任务")
            failed_ids = []
            for f in src.get("failed") or []:
                fid = f.get("id") or f.get("ep_id") or f.get("bvid") or f.get("page")
                if fid is not None:
                    failed_ids.append(fid)

            payload = {
                "url": src["url"],
                "title": f"{src['title']} (重试)",
                "cover_url": src.get("cover", ""),
                "media_type": src.get("media_type", "video"),
                "quality": src["quality"],
                "codec": src["codec"],
                "download_mode": src["download_mode"],
                "audio_format": src["audio_format"],
                "workers": max(1, min(src["workers"], 2)),
                "output_dir": src["output_dir"],
                "upload_cloudreve": src["upload_cloudreve"],
                "remote_dir": src["remote_dir"],
                "keep_local": src["keep_local"],
                "cover": src["cover_opt"],
                "danmaku": src["danmaku"],
                "subtitle": src["subtitle"],
                "embed_extras": src["embed_extras"],
                "cdn_probe": src["cdn_probe"],
                "filename_template": src["filename_template"],
                "selected_ids": failed_ids if failed_ids else src.get("selected_ids", []),
            }
        return self.enqueue(payload)

    # ─── Inspection / Snapshots ──────────────────────────────────────────

    def _public_view(self, task: dict, log_limit: int = 200) -> dict:
        return {
            "id": task["id"],
            "url": task["url"],
            "title": task["title"],
            "cover": task["cover"],
            "media_type": task["media_type"],
            "quality": task["quality"],
            "codec": task["codec"],
            "download_mode": task["download_mode"],
            "audio_format": task["audio_format"],
            "workers": task["workers"],
            "output_dir": task["output_dir"],
            "resolved_dir": task["resolved_dir"],
            "upload_cloudreve": task["upload_cloudreve"],
            "remote_dir": task["remote_dir"],
            "status": task["status"],
            "phase": task["phase"],
            "progress": round(float(task["progress"]), 2),
            "speed_bps": round(float(task["speed_bps"]), 1),
            "speed_mbps": round(float(task["speed_bps"]) / (1024 * 1024), 2),
            "speed_history": list(task["speed_history"]),
            "eta_sec": round(float(task["eta_sec"]), 1),
            "downloaded_bytes": int(task["downloaded_bytes"]),
            "total_bytes": int(task["total_bytes"]),
            "cdn_host": task["cdn_host"],
            "total_items": task["total_items"],
            "success_items": task["success_items"],
            "failed_items": task["failed_items"],
            "items": [dict(it) for it in task["items"]],
            "log": task["log"][-log_limit:],
            "log_total": len(task["log"]),
            "result": list(task["result"]),
            "files": list(task["files"]),
            "failed": list(task["failed"]),
            "error": task["error"],
            "created_at": task["created_at"],
            "started_at": task["started_at"],
            "finished_at": task["finished_at"],
        }

    def list_tasks(self, log_limit: int = 120) -> list[dict]:
        with self._lock:
            return [
                self._public_view(self._tasks[tid], log_limit=log_limit)
                for tid in self._order
                if tid in self._tasks
            ]

    def get_task(self, task_id: str, log_limit: int = 300) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return self._public_view(task, log_limit=log_limit) if task else None

    def summary_counts(self) -> dict:
        with self._lock:
            active = sum(1 for t in self._tasks.values() if t["status"] in ("queued", "running"))
            running = sum(1 for t in self._tasks.values() if t["status"] == "running")
            queued = sum(1 for t in self._tasks.values() if t["status"] == "queued")
            return {
                "active": active,
                "running": running,
                "queued": queued,
                "total": len(self._tasks),
                "active_task_id": self._active_task_id,
            }

    def legacy_status(self) -> dict:
        """Backward-compatible /api/status projection matching v1 bili_web.py."""
        with self._lock:
            tid = self._active_task_id or (self._order[0] if self._order else None)
            if not tid or tid not in self._tasks:
                return {"status": "idle", "progress": 0, "log": [], "result": []}
            t = self._tasks[tid]
            return {
                "id": t["id"],
                "status": t["status"],
                "progress": round(float(t["progress"]), 2),
                "log": t["log"][-200:],
                "result": list(t["result"]),
                "failed": list(t["failed"]),
                "error": t["error"],
                "workers": t["workers"],
            }

    # ─── Internal State Helpers ──────────────────────────────────────────

    def _append_log_locked(self, task: dict, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        task["log"].append(f"[{stamp}] {message}")
        if len(task["log"]) > 600:
            task["log"] = task["log"][-600:]

    def add_log(self, task_id: str, message: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                self._append_log_locked(task, message)

    def _update(self, task_id: str, **fields) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            for k, v in fields.items():
                if k == "progress":
                    task["progress"] = max(task.get("progress", 0.0), min(float(v), 100.0))
                else:
                    task[k] = v

    def _record_telemetry(self, task_id: str, info: dict) -> None:
        now = time.monotonic()
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            speed = float(info.get("speed_bps") or 0.0)
            task["speed_bps"] = speed
            task["eta_sec"] = float(info.get("eta") or 0.0)
            if info.get("host"):
                task["cdn_host"] = info["host"]
            if info.get("downloaded"):
                task["downloaded_bytes"] = int(info["downloaded"])
            if info.get("total"):
                task["total_bytes"] = max(task["total_bytes"], int(info["total"]))
            if now - task["_last_speed_sample"] >= 0.7:
                task["_last_speed_sample"] = now
                mbps = round(speed / (1024 * 1024), 2)
                task["speed_history"].append(mbps)
                if len(task["speed_history"]) > 40:
                    task["speed_history"] = task["speed_history"][-40:]

    def _update_item(self, task_id: str, item_id, **fields) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            for it in task["items"]:
                if str(it["id"]) == str(item_id):
                    it.update(fields)
                    return

    # ─── Background Dispatcher & Runner ──────────────────────────────────

    def _dispatcher_loop(self) -> None:
        while not self._shutdown.is_set():
            task_id = None
            with self._lock:
                while self._queue:
                    cand = self._queue.popleft()
                    t = self._tasks.get(cand)
                    if t and t["status"] == "queued":
                        task_id = cand
                        self._active_task_id = cand
                        break
            if task_id is None:
                self._wakeup.wait(timeout=0.5)
                self._wakeup.clear()
                continue
            try:
                self._run_task(task_id)
            finally:
                with self._lock:
                    if self._active_task_id == task_id:
                        self._active_task_id = None

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            stop_event = task["_stop_event"]
            task["status"] = "running"
            task["phase"] = "resolving"
            task["started_at"] = time.time()
            use_cloudreve = task["upload_cloudreve"]
            quality = task["quality"]
            codec = task["codec"]
            download_mode = task["download_mode"]
            audio_format = task["audio_format"]
            workers = task["workers"]
            cdn_probe = task["cdn_probe"]
            cover_opt = task["cover_opt"]
            danmaku = task["danmaku"]
            subtitle = task["subtitle"]
            embed_extras = task["embed_extras"]
            all_subtitles = task["all_subtitles"]
            danmaku_style = dict(task["danmaku_style"])
            filename_template = task["filename_template"]
            selected_ids = list(task["selected_ids"])
            remote_dir_root = task["remote_dir"]
            keep_local = task["keep_local"]
            raw_url = task["url"]

        if use_cloudreve:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            out_dir = self.work_dir / task_id
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = Path(task["output_dir"] or default_output_dir())
            out_dir.mkdir(parents=True, exist_ok=True)

        self._update(task_id, resolved_dir=str(out_dir))
        self.add_log(task_id, f"开始任务：{raw_url}")
        mode_desc = {
            "video": f"视频 ({quality} · {codec.upper()})",
            "audio": f"仅音频 ({audio_format.upper()})",
            "extras": "仅封面/弹幕/字幕",
        }[download_mode]
        self.add_log(
            task_id,
            f"模式：{mode_desc} | 并发：{workers}"
            + (f" | 网盘目录：{remote_dir_root}" if use_cloudreve else f" | 保存至：{out_dir}"),
        )

        # Progress range allocation: download takes 5% -> 60% if uploading, else 5% -> 98%
        dl_start = 5.0
        dl_span = 55.0 if use_cloudreve else 93.0
        stopped = False

        try:
            if use_cloudreve:
                free = shutil.disk_usage(self.work_dir).free
                if free < 2 * 1024 ** 3:
                    raise RuntimeError(
                        f"服务器磁盘剩余空间不足 2GB（当前 {free / 1024 ** 3:.1f}GB）"
                    )

            target = parse_target(raw_url, resolve_short=True)
            url = target["resolved"]
            kind = target["kind"]
            if kind == "unknown":
                raise RuntimeError("无法识别链接格式，请检查后重试")

            audio_only = download_mode == "audio"
            extras_only = download_mode == "extras"

            def single_progress(done: int, total: int):
                if total > 0:
                    self._update(task_id, progress=dl_start + min(done / total, 1.0) * dl_span)

            def on_telemetry(info: dict):
                self._record_telemetry(task_id, info)

            with contextlib.redirect_stdout(_TaskStdoutWriter(self, task_id)):
                self._update(task_id, phase="downloading", progress=dl_start)

                # ── 1) Regular Video (Single or Multi-P) ──
                if kind == "video":
                    info = (
                        api.get_video_info(target["bvid"])
                        if target.get("bvid")
                        else api.get_video_info_by_aid(target["aid"])
                    )
                    bvid = info["bvid"]
                    vtitle = info["title"]
                    pages = info.get("pages") or [
                        {"page": 1, "cid": info["cid"], "part": vtitle}
                    ]
                    self._update(
                        task_id,
                        title=vtitle,
                        cover=info.get("pic", ""),
                        media_type="video",
                    )

                    if selected_ids:
                        sel_set = {int(x) for x in selected_ids if str(x).isdigit()}
                        chosen_pages = [
                            (i + 1, p) for i, p in enumerate(pages)
                            if (p.get("page") or (i + 1)) in sel_set
                        ]
                        if not chosen_pages:
                            chosen_pages = [(1, pages[0])]
                    elif target.get("page") and 1 <= target["page"] <= len(pages):
                        idx = target["page"]
                        chosen_pages = [(idx, pages[idx - 1])]
                    else:
                        chosen_pages = [(i + 1, p) for i, p in enumerate(pages)]

                    item_rows = [
                        {
                            "id": p.get("page") or idx,
                            "index": idx,
                            "title": p.get("part") or vtitle,
                            "status": "queued",
                            "progress": 0.0,
                            "error": "",
                        }
                        for idx, p in chosen_pages
                    ]
                    self._update(task_id, items=item_rows, total_items=len(chosen_pages))

                    folder_path = out_dir / safe_filename(vtitle)
                    self._update(task_id, resolved_dir=str(folder_path))

                    if len(chosen_pages) == 1:
                        p_idx, p_obj = chosen_pages[0]
                        pid = p_obj.get("page") or p_idx
                        self._update_item(task_id, pid, status="downloading", progress=5.0)
                        if extras_only:
                            base = safe_filename(vtitle) if len(pages) == 1 else format_filename(
                                "{index}_{part}", title=vtitle, part=p_obj.get("part", f"P{p_idx}"), index=p_idx
                            )
                            download_extras_and_merge(
                                bvid,
                                out_dir,
                                cover=cover_opt,
                                danmaku=danmaku,
                                subtitle=subtitle,
                                merge=False,
                                info=info,
                                base_name=base,
                                cid=p_obj.get("cid"),
                                video_dir=folder_path,
                                all_subtitles=all_subtitles,
                            )
                            ok = True
                        else:
                            ok = bool(
                                engine.download_single(
                                    bvid,
                                    out_dir,
                                    quality,
                                    page=p_idx if len(pages) > 1 else 0,
                                    stop_event=stop_event,
                                    progress_callback=single_progress,
                                    telemetry_callback=on_telemetry,
                                    quiet=True,
                                    codec=codec,
                                    audio_only=audio_only,
                                    audio_format=audio_format,
                                    cdn_probe=cdn_probe,
                                    filename_template=filename_template,
                                    info=info,
                                )
                            )
                            if ok and not stop_event.is_set() and not audio_only and (cover_opt or danmaku or subtitle):
                                self._update(task_id, phase="merging")
                                self._update_item(task_id, pid, status="merging", progress=92.0)
                                part_name = (
                                    format_filename(
                                        filename_template if filename_template != "{title}" else "{index}_{part}",
                                        title=vtitle,
                                        part=p_obj.get("part", f"P{p_idx}"),
                                        index=p_idx,
                                        owner=(info.get("owner") or {}).get("name", ""),
                                        bvid=bvid,
                                        quality=quality,
                                    )
                                    if len(pages) > 1
                                    else format_filename(
                                        filename_template,
                                        title=vtitle,
                                        owner=(info.get("owner") or {}).get("name", ""),
                                        bvid=bvid,
                                        quality=quality,
                                    )
                                )
                                download_extras_and_merge(
                                    bvid,
                                    out_dir,
                                    cover=cover_opt,
                                    danmaku=danmaku,
                                    subtitle=subtitle,
                                    merge=embed_extras,
                                    danmaku_style=danmaku_style,
                                    all_subtitles=all_subtitles,
                                    info=info,
                                    base_name=part_name,
                                    cid=p_obj.get("cid"),
                                    video_dir=folder_path,
                                )
                        if stop_event.is_set():
                            stopped = True
                            self._update_item(task_id, pid, status="cancelled")
                        elif ok:
                            self._update_item(task_id, pid, status="completed", progress=100.0)
                            self._update(task_id, success_items=1)
                        else:
                            self._update_item(task_id, pid, status="failed", error="下载未完成")
                            raise RuntimeError("视频下载未完成")
                    else:
                        # Concurrent Multi-P download
                        done_lock = threading.Lock()
                        done_n = 0
                        ok_n = 0
                        failures = []

                        def run_page(p_idx: int, p_obj: dict):
                            nonlocal done_n, ok_n
                            pid = p_obj.get("page") or p_idx
                            if stop_event.is_set():
                                self._update_item(task_id, pid, status="cancelled")
                                return
                            self._update_item(task_id, pid, status="downloading", progress=2.0)
                            self.add_log(task_id, f"[P{p_idx}] 开始：{p_obj.get('part', '')}")

                            def p_cb(d, t):
                                if t > 0:
                                    self._update_item(task_id, pid, progress=min(d / t * 92.0, 92.0))

                            try:
                                part_name = format_filename(
                                    filename_template if filename_template != "{title}" else "{index}_{part}",
                                    title=vtitle,
                                    part=p_obj.get("part", f"P{p_idx}"),
                                    index=p_idx,
                                    owner=(info.get("owner") or {}).get("name", ""),
                                    bvid=bvid,
                                    quality=quality,
                                )
                                if extras_only:
                                    download_episode_extras(
                                        folder_path,
                                        part_name,
                                        info["aid"],
                                        p_obj.get("cid"),
                                        danmaku=danmaku,
                                        subtitle=subtitle,
                                        merge=False,
                                        all_subtitles=all_subtitles,
                                    )
                                    ok = True
                                else:
                                    ok = bool(
                                        engine.download_single(
                                            bvid,
                                            out_dir,
                                            quality,
                                            page=p_idx,
                                            stop_event=stop_event,
                                            progress_callback=p_cb,
                                            telemetry_callback=on_telemetry,
                                            quiet=True,
                                            codec=codec,
                                            audio_only=audio_only,
                                            audio_format=audio_format,
                                            cdn_probe=cdn_probe,
                                            filename_template=filename_template,
                                            info=info,
                                        )
                                    )
                                    if ok and not stop_event.is_set() and not audio_only and (danmaku or subtitle):
                                        self._update_item(task_id, pid, status="merging", progress=94.0)
                                        download_episode_extras(
                                            folder_path,
                                            part_name,
                                            info["aid"],
                                            p_obj.get("cid"),
                                            danmaku=danmaku,
                                            subtitle=subtitle,
                                            merge=embed_extras,
                                            danmaku_style=danmaku_style,
                                            all_subtitles=all_subtitles,
                                        )
                                if stop_event.is_set():
                                    self._update_item(task_id, pid, status="cancelled")
                                    return
                                if not ok:
                                    raise RuntimeError("分P下载未完成")
                                self._update_item(task_id, pid, status="completed", progress=100.0)
                                self.add_log(task_id, f"[P{p_idx}] 完成：{p_obj.get('part', '')}")
                                with done_lock:
                                    ok_n += 1
                            except Exception as exc:
                                err = str(exc)
                                self._update_item(task_id, pid, status="failed", error=err)
                                self.add_log(task_id, f"[P{p_idx}] 失败：{err}")
                                with done_lock:
                                    failures.append({"id": pid, "page": pid, "index": p_idx, "title": p_obj.get("part", ""), "error": err})
                            finally:
                                with done_lock:
                                    done_n += 1
                                    cur = done_n
                                self._update(
                                    task_id,
                                    progress=dl_start + (cur / max(len(chosen_pages), 1)) * dl_span,
                                    success_items=ok_n,
                                    failed_items=len(failures),
                                )

                        if cover_opt and info.get("pic"):
                            download_cover(cover_url=info["pic"], output_dir=folder_path, base_name=safe_filename(vtitle), quiet=True)

                        with ThreadPoolExecutor(max_workers=workers) as pool:
                            futs = [pool.submit(run_page, idx, p) for idx, p in chosen_pages]
                            for f in as_completed(futs):
                                f.result()

                        if stop_event.is_set():
                            stopped = True
                        if failures:
                            self._update(task_id, failed=failures)
                        if ok_n == 0 and not stopped:
                            raise RuntimeError("没有成功下载任何分P")

                # ── 2) Bangumi / Course Collection ──
                elif kind in ("bangumi", "course"):
                    collection = api.get_media_collection(url, allow_season=True)
                    ctitle = collection["title"]
                    self._update(
                        task_id,
                        title=ctitle,
                        cover=collection.get("cover", ""),
                        media_type=collection["media_type"],
                        resolved_dir=str(out_dir / safe_filename(ctitle)),
                    )
                    all_eps = collection["episodes"]
                    if selected_ids:
                        sel_ep_ids = [int(x) for x in selected_ids]
                    elif target.get("ep_id"):
                        sel_ep_ids = [collection["current_ep_id"]]
                    else:
                        sel_ep_ids = [ep["ep_id"] for ep in all_eps]
                    if not sel_ep_ids:
                        raise RuntimeError("未选择任何集数")

                    sel_set = set(sel_ep_ids)
                    ordered = [ep for ep in all_eps if ep["ep_id"] in sel_set]
                    self._update(
                        task_id,
                        items=[
                            {
                                "id": ep["ep_id"],
                                "index": ep["index"],
                                "title": ep["title"],
                                "status": "queued",
                                "progress": 0.0,
                                "error": "",
                            }
                            for ep in ordered
                        ],
                        total_items=len(ordered),
                    )
                    self.add_log(task_id, f"共 {len(all_eps)} 集，已选择 {len(ordered)} 集")

                    if cover_opt and collection.get("cover"):
                        download_cover(
                            cover_url=collection["cover"],
                            output_dir=out_dir / safe_filename(ctitle),
                            base_name=safe_filename(ctitle),
                            quiet=True,
                        )

                    if extras_only:
                        folder_path = out_dir / safe_filename(ctitle)
                        ok_n = 0
                        failures = []
                        for i, ep in enumerate(ordered, 1):
                            if stop_event.is_set():
                                stopped = True
                                self._update_item(task_id, ep["ep_id"], status="cancelled")
                                continue
                            self._update_item(task_id, ep["ep_id"], status="downloading", progress=20.0)
                            try:
                                base = safe_filename(f"{ep['index']:03d}_{ep['title']}")
                                download_episode_extras(
                                    folder_path,
                                    base,
                                    ep["aid"],
                                    ep["cid"],
                                    danmaku=danmaku,
                                    subtitle=subtitle,
                                    merge=False,
                                    all_subtitles=all_subtitles,
                                )
                                ok_n += 1
                                self._update_item(task_id, ep["ep_id"], status="completed", progress=100.0)
                            except Exception as exc:
                                failures.append({"id": ep["ep_id"], "ep_id": ep["ep_id"], "index": ep["index"], "title": ep["title"], "error": str(exc)})
                                self._update_item(task_id, ep["ep_id"], status="failed", error=str(exc))
                            self._update(
                                task_id,
                                progress=dl_start + i / max(len(ordered), 1) * dl_span,
                                success_items=ok_n,
                                failed_items=len(failures),
                            )
                        if failures:
                            self._update(task_id, failed=failures)
                    else:
                        progress_lock = threading.Lock()
                        done_count = 0
                        ok_count = 0
                        fail_count = 0

                        def ep_cb(ev: dict):
                            nonlocal done_count, ok_count, fail_count
                            st = ev["status"]
                            eid = ev["ep_id"]
                            ordinal = ev.get("ordinal", 0)
                            total_n = ev.get("total", len(ordered))
                            idx = ev.get("index", "?")
                            etitle = ev.get("title", "")
                            if st == "started":
                                self._update_item(task_id, eid, status="downloading", progress=1.0)
                                self.add_log(task_id, f"[{ordinal}/{total_n}] 开始下载：第{idx}集 {etitle}")
                            elif st == "downloading":
                                self._update_item(task_id, eid, status="downloading", progress=round(ev.get("progress", 0.0), 1))
                            elif st == "merging":
                                self._update_item(task_id, eid, status="merging", progress=94.0)
                                self.add_log(task_id, f"[{ordinal}/{total_n}] 合并处理中：第{idx}集 {etitle}")
                            elif st in ("completed", "failed"):
                                with progress_lock:
                                    done_count += 1
                                    if st == "completed":
                                        ok_count += 1
                                    else:
                                        fail_count += 1
                                    cur_done, cur_ok, cur_fail = done_count, ok_count, fail_count
                                self._update_item(
                                    task_id,
                                    eid,
                                    status=st,
                                    progress=100.0 if st == "completed" else 0.0,
                                    error=ev.get("error", ""),
                                )
                                self._update(
                                    task_id,
                                    progress=dl_start + cur_done / max(total_n, 1) * dl_span,
                                    success_items=cur_ok,
                                    failed_items=cur_fail,
                                )
                                if st == "completed":
                                    self.add_log(task_id, f"[{ordinal}/{total_n}] 完成：第{idx}集 {etitle}")
                                else:
                                    self.add_log(task_id, f"[{ordinal}/{total_n}] 失败：第{idx}集 {etitle}：{ev.get('error', '')}")

                        summary = engine.download_selected_episodes(
                            collection,
                            sel_ep_ids,
                            out_dir,
                            quality,
                            stop_event=stop_event,
                            max_workers=workers,
                            episode_status_callback=ep_cb,
                            danmaku=danmaku,
                            subtitle=subtitle,
                            codec=codec,
                            audio_only=audio_only,
                            audio_format=audio_format,
                            cdn_probe=cdn_probe,
                            embed_extras=embed_extras,
                            danmaku_style=danmaku_style,
                            all_subtitles=all_subtitles,
                            telemetry_callback=on_telemetry,
                        )
                        if summary["cancelled"]:
                            stopped = True
                        if summary["failed"]:
                            self._update(task_id, failed=summary["failures"])
                        if summary["success"] == 0 and not stopped:
                            raise RuntimeError("没有成功下载任何集数")

                # ── 3) Playlist (UGC Season / Series / Favlist / Watch Later) ──
                elif kind in ("ugc_season", "series", "favlist", "watchlater"):
                    preview = api.inspect_url(url)
                    ptitle = preview["title"]
                    all_items = preview["items"]
                    folder_path = out_dir / safe_filename(ptitle)
                    self._update(
                        task_id,
                        title=ptitle,
                        cover=preview.get("cover", ""),
                        media_type=preview["media_type"],
                        resolved_dir=str(folder_path),
                    )
                    if selected_ids:
                        sel_set = {str(x) for x in selected_ids}
                        chosen = [it for it in all_items if str(it["id"]) in sel_set]
                    else:
                        chosen = list(all_items)
                    if not chosen:
                        raise RuntimeError("未选择任何视频")

                    self._update(
                        task_id,
                        items=[
                            {
                                "id": it["bvid"],
                                "index": it["index"],
                                "title": it["title"],
                                "status": "queued",
                                "progress": 0.0,
                                "error": "",
                            }
                            for it in chosen
                        ],
                        total_items=len(chosen),
                    )
                    self.add_log(task_id, f"列表「{ptitle}」共 {len(chosen)} 个视频，并发 {workers}")

                    done_lock = threading.Lock()
                    done_n = 0
                    ok_n = 0
                    failures = []

                    def run_bvid_item(item: dict):
                        nonlocal done_n, ok_n
                        bv = item["bvid"]
                        idx = item["index"]
                        ititle = item["title"]
                        if stop_event.is_set():
                            self._update_item(task_id, bv, status="cancelled")
                            return
                        self._update_item(task_id, bv, status="downloading", progress=2.0)
                        self.add_log(task_id, f"[{idx}] 开始：{ititle} ({bv})")

                        def item_cb(d, t):
                            if t > 0:
                                self._update_item(task_id, bv, progress=min(d / t * 92.0, 92.0))

                        try:
                            vinfo = api.get_video_info(bv)
                            part_name = format_filename(
                                filename_template if filename_template != "{title}" else "{index}_{title}",
                                title=vinfo["title"],
                                index=idx,
                                owner=(vinfo.get("owner") or {}).get("name", ""),
                                bvid=bv,
                                quality=quality,
                            )
                            if extras_only:
                                download_extras_and_merge(
                                    bv,
                                    out_dir,
                                    cover=cover_opt,
                                    danmaku=danmaku,
                                    subtitle=subtitle,
                                    merge=False,
                                    info=vinfo,
                                    base_name=part_name,
                                    video_dir=folder_path,
                                    all_subtitles=all_subtitles,
                                )
                                ok = True
                            else:
                                ok = bool(
                                    engine.download_single(
                                        bv,
                                        out_dir,
                                        quality,
                                        stop_event=stop_event,
                                        progress_callback=item_cb,
                                        telemetry_callback=on_telemetry,
                                        quiet=True,
                                        codec=codec,
                                        audio_only=audio_only,
                                        audio_format=audio_format,
                                        cdn_probe=cdn_probe,
                                        filename_template=filename_template if filename_template != "{title}" else "{index}_{title}",
                                        folder_override=ptitle,
                                        index=idx,
                                        info=vinfo,
                                    )
                                )
                                if ok and not stop_event.is_set() and not audio_only and (cover_opt or danmaku or subtitle):
                                    self._update_item(task_id, bv, status="merging", progress=94.0)
                                    download_extras_and_merge(
                                        bv,
                                        out_dir,
                                        cover=cover_opt,
                                        danmaku=danmaku,
                                        subtitle=subtitle,
                                        merge=embed_extras,
                                        danmaku_style=danmaku_style,
                                        all_subtitles=all_subtitles,
                                        info=vinfo,
                                        base_name=part_name,
                                        video_dir=folder_path,
                                    )
                            if stop_event.is_set():
                                self._update_item(task_id, bv, status="cancelled")
                                return
                            if not ok:
                                raise RuntimeError("视频下载未完成")
                            self._update_item(task_id, bv, status="completed", progress=100.0)
                            self.add_log(task_id, f"[{idx}] 完成：{ititle}")
                            with done_lock:
                                ok_n += 1
                        except Exception as exc:
                            err = str(exc)
                            self._update_item(task_id, bv, status="failed", error=err)
                            self.add_log(task_id, f"[{idx}] 失败：{ititle}：{err}")
                            with done_lock:
                                failures.append({"id": bv, "bvid": bv, "index": idx, "title": ititle, "error": err})
                        finally:
                            with done_lock:
                                done_n += 1
                                cur = done_n
                            self._update(
                                task_id,
                                progress=dl_start + (cur / max(len(chosen), 1)) * dl_span,
                                success_items=ok_n,
                                failed_items=len(failures),
                            )

                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futs = [pool.submit(run_bvid_item, it) for it in chosen]
                        for f in as_completed(futs):
                            f.result()

                    if stop_event.is_set():
                        stopped = True
                    if failures:
                        self._update(task_id, failed=failures)
                    if ok_n == 0 and not stopped:
                        raise RuntimeError("没有成功下载任何视频")

            # ── 4) Finalize / Cloudreve Upload ──
            if use_cloudreve:
                self._update(task_id, phase="uploading", progress=60.0, speed_bps=0.0, eta_sec=0.0)
                files = find_upload_files(out_dir) if not extras_only else find_output_files(out_dir, include_extras=True)
                if not files:
                    if stopped:
                        self._finish_task(task_id, "stopped", "任务已停止，没有可上传的文件。")
                        if not keep_local:
                            shutil.rmtree(out_dir, ignore_errors=True)
                        return
                    raise RuntimeError("没有找到可上传的文件")

                cloud_cfg = effective_cloudreve_config()
                dav = CloudreveWebDAV(
                    cloud_cfg["base_url"], cloud_cfg["username"], cloud_cfg["password"]
                )
                total_bytes = sum(p.stat().st_size for p in files)
                self._update(task_id, total_bytes=total_bytes)
                sent_base = 0
                self.add_log(
                    task_id,
                    f"准备上传 {len(files)} 个文件至 Cloudreve（共 {total_bytes / 1024 ** 2:.1f} MB）",
                )

                uploaded_paths: list[str] = []
                for path in files:
                    rel = path.relative_to(out_dir)
                    rel_parent = rel.parent.as_posix()
                    rdir = remote_dir_root
                    if rel_parent not in ("", "."):
                        rdir = rdir.rstrip("/") + "/" + rel_parent
                    base = sent_base
                    size = path.stat().st_size
                    t0 = time.monotonic()

                    def upload_progress(done: int, tot: int, base=base, t0=t0):
                        dt = max(time.monotonic() - t0, 0.01)
                        self._record_telemetry(
                            task_id,
                            {
                                "downloaded": base + done,
                                "total": total_bytes,
                                "speed_bps": done / dt,
                                "eta": (total_bytes - (base + done)) / max(done / dt, 1.0),
                                "host": "Cloudreve WebDAV",
                            },
                        )
                        self._update(task_id, progress=60.0 + (base + done) / max(total_bytes, 1) * 40.0)

                    self.add_log(task_id, f"上传中：{rel.as_posix()} ({size / 1024 ** 2:.1f} MB)")
                    remote_path = dav.upload(path, rdir, upload_progress)
                    sent_base += size
                    uploaded_paths.append(remote_path)
                    with self._lock:
                        self._tasks[task_id]["result"].append(remote_path)
                        self._tasks[task_id]["files"].append(remote_path)
                    self.add_log(task_id, f"已上传：{remote_path}")

                if not keep_local:
                    shutil.rmtree(out_dir, ignore_errors=True)
            else:
                scan_root = Path(self._tasks[task_id]["resolved_dir"] or out_dir)
                if not scan_root.exists():
                    scan_root = out_dir
                out_files = find_output_files(scan_root, include_extras=extras_only)
                total_bytes = sum(p.stat().st_size for p in out_files)
                str_files = [str(p) for p in out_files]
                self._update(
                    task_id,
                    total_bytes=total_bytes,
                    files=str_files,
                    result=str_files,
                )

            with self._lock:
                has_failed = bool(self._tasks[task_id].get("failed"))
            self._update(task_id, progress=100.0, speed_bps=0.0, eta_sec=0.0)
            if has_failed:
                self._finish_task(task_id, "partial", "任务部分完成，可点击「重试失败项」继续。")
            elif stopped:
                self._finish_task(task_id, "stopped", "任务已停止。")
            else:
                self._finish_task(
                    task_id,
                    "done",
                    "任务完成，文件已保存到 Cloudreve。" if use_cloudreve else "任务全部完成！",
                )

        except Exception as exc:
            if stop_event.is_set():
                self._finish_task(task_id, "stopped", "任务已停止。")
            else:
                self._update(task_id, error=str(exc), speed_bps=0.0, eta_sec=0.0)
                self.add_log(task_id, f"任务失败：{exc}")
                self.add_log(task_id, traceback.format_exc(limit=3))
                self._finish_task(task_id, "error", "")

    def _finish_task(self, task_id: str, status: str, log_msg: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task["status"] = status
            task["phase"] = status
            task["finished_at"] = time.time()
            task["speed_bps"] = 0.0
            task["eta_sec"] = 0.0
            if log_msg:
                self._append_log_locked(task, log_msg)
            snapshot = dict(task)
        try:
            self.store.save_record(snapshot)
        except Exception:
            pass
