#!/usr/bin/env python3
"""Inject the BiliDown button into Cloudreve's sidebar_bottom_html setting."""

import json
import os
import sqlite3
import time

DB = "/home/ubuntu/cloudreve/cloudreve/cloudreve.db"
BACKUP_DIR = "/home/ubuntu/bili-downloader/backups"
OLD_VALUE_FILE = "/home/ubuntu/bili-downloader/cloudreve_sidebar_backup.txt"

os.makedirs(BACKUP_DIR, exist_ok=True)
backup = os.path.join(BACKUP_DIR, "cloudreve.db." + time.strftime("%Y%m%d%H%M%S"))
src = sqlite3.connect(DB)
dst = sqlite3.connect(backup)
src.backup(dst)
dst.close()
src.close()
print("backup", backup, os.path.getsize(backup))

link = "/api/bili/"
block = (
    '<div style="padding:10px 8px 12px;">'
    '<a href="' + link + '" target="_blank" rel="noopener noreferrer" '
    'style="display:flex;align-items:center;justify-content:center;gap:8px;'
    'padding:11px 12px;border-radius:10px;text-decoration:none;color:#fff;'
    'font-weight:700;font-size:13px;'
    'background:linear-gradient(90deg,#fb7299,#8b5cf6);'
    'box-shadow:0 4px 14px rgba(251,114,153,.28);">'
    '<span style="font-size:16px;line-height:1;">⬇</span> B站视频下载'
    '</a></div>'
)

con = sqlite3.connect(DB, timeout=30)
row = con.execute(
    "select value from settings where name='sidebar_bottom_html'"
).fetchone()
open(OLD_VALUE_FILE, "w", encoding="utf-8").write(row[0] if row else "")
con.execute(
    "update settings set value=? where name='sidebar_bottom_html'", (block,)
)
con.commit()
print("updated_rows", con.total_changes)
print("new_len", len(con.execute(
    "select value from settings where name='sidebar_bottom_html'"
).fetchone()[0]))
con.close()
