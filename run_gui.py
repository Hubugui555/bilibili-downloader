#!/usr/bin/env python3
"""BiliDown Studio — 快速启动入口"""
import sys

if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from gui import main as gui_main

if __name__ == "__main__":
    gui_main()
