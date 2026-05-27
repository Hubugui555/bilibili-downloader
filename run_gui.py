#!/usr/bin/env python3
"""Bilibili 视频下载器 - 启动入口"""
import sys, os

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
parser = argparse.ArgumentParser(description="Bilibili 视频下载器")
parser.add_argument("--web", action="store_true", help="启动 Web 界面 (浏览器)")
args, _ = parser.parse_known_args()

if args.web or "--web" in sys.argv:
    from webui import main as web_main
    web_main()
else:
    from gui import main as gui_main
    gui_main()
