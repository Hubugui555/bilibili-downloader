#!/usr/bin/env python3
"""Bilibili 视频下载器 - 启动入口"""
import sys

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

from gui import main as gui_main

if __name__ == "__main__":
    gui_main()
