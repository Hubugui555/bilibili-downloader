"""SQLite-backed persistent download history store."""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .config import data_dir


class HistoryStore:
    """Thread-safe SQLite store for completed/failed download tasks."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else (data_dir() / "history.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_history (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    cover TEXT DEFAULT '',
                    quality TEXT DEFAULT '',
                    mode TEXT DEFAULT 'video',
                    status TEXT NOT NULL,
                    output_dir TEXT DEFAULT '',
                    remote_dir TEXT DEFAULT '',
                    total_items INTEGER DEFAULT 1,
                    success_items INTEGER DEFAULT 0,
                    failed_items INTEGER DEFAULT 0,
                    total_bytes INTEGER DEFAULT 0,
                    files_json TEXT DEFAULT '[]',
                    failures_json TEXT DEFAULT '[]',
                    error TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    finished_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_finished ON task_history(finished_at DESC)"
            )

    def save_record(self, task: dict) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO task_history (
                    id, url, title, media_type, cover, quality, mode, status,
                    output_dir, remote_dir, total_items, success_items, failed_items,
                    total_bytes, files_json, failures_json, error, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task.get("url", ""),
                    task.get("title") or task.get("url", ""),
                    task.get("media_type", "video"),
                    task.get("cover", ""),
                    task.get("quality", "1080P"),
                    task.get("download_mode", "video"),
                    task.get("status", "done"),
                    str(task.get("output_dir", "")),
                    str(task.get("remote_dir", "")),
                    int(task.get("total_items", 1)),
                    int(task.get("success_items", 0)),
                    int(task.get("failed_items", 0)),
                    int(task.get("total_bytes", 0)),
                    json.dumps(task.get("files", []), ensure_ascii=False),
                    json.dumps(task.get("failed", []), ensure_ascii=False),
                    task.get("error", ""),
                    float(task.get("created_at", now)),
                    float(task.get("finished_at", now)),
                ),
            )

    def list_records(self, query: str = "", limit: int = 100) -> list[dict]:
        q = (query or "").strip().lower()
        with self._lock, self._connect() as conn:
            if q:
                like = f"%{q}%"
                rows = conn.execute(
                    """
                    SELECT * FROM task_history
                    WHERE lower(title) LIKE ? OR lower(url) LIKE ? OR lower(media_type) LIKE ?
                    ORDER BY finished_at DESC LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM task_history ORDER BY finished_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            try:
                item["files"] = json.loads(item.pop("files_json", "[]") or "[]")
            except ValueError:
                item["files"] = []
            try:
                item["failed"] = json.loads(item.pop("failures_json", "[]") or "[]")
            except ValueError:
                item["failed"] = []
            results.append(item)
        return results

    def delete_record(self, task_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM task_history WHERE id = ?", (task_id,))
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM task_history")

    def stats(self) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_tasks,
                    SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) as done_tasks,
                    COALESCE(SUM(total_bytes), 0) as total_bytes
                FROM task_history
                """
            ).fetchone()
            return {
                "total_tasks": row["total_tasks"] or 0,
                "done_tasks": row["done_tasks"] or 0,
                "total_bytes": row["total_bytes"] or 0,
            }
