#!/usr/bin/env python3
"""BiliDown Studio 2.0 — Desktop Application Entry Point."""

import argparse
import sys

if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from bilidown.launcher import launch_desktop


def main(argv=None):
    parser = argparse.ArgumentParser(description="BiliDown Studio 桌面应用")
    parser.add_argument("--port", type=int, default=0, help="指定本地服务端口 (默认自动选择)")
    parser.add_argument("--browser", action="store_true", help="在默认浏览器标签页打开而非独立窗口")
    parser.add_argument("--no-open", action="store_true", help="仅启动本地服务，不自动打开窗口")
    args = parser.parse_args(argv)
    launch_desktop(
        port=args.port or None,
        use_browser_tab=args.browser,
        no_open=args.no_open,
    )


if __name__ == "__main__":
    main()
