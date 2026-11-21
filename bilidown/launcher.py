"""Desktop App Launcher: hosts BiliDown Studio locally and opens a native app window."""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from werkzeug.serving import make_server

from .config import data_dir
from .web.app import create_app


def _pick_free_port(preferred: int = 18787) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return 18787


def _find_chromium_app_browser() -> Optional[str]:
    """Locate Microsoft Edge or Google Chrome for native --app window mode."""
    candidates: list[str] = []
    if sys.platform == "win32":
        program_files = [
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for base in program_files:
            if not base:
                continue
            candidates.extend(
                [
                    os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                    os.path.join(base, r"Google\Chrome\Application\chrome.exe"),
                ]
            )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def launch_desktop(
    *,
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    use_browser_tab: bool = False,
    no_open: bool = False,
) -> None:
    """Start BiliDown Studio local server and open desktop app window (or browser tab)."""
    actual_port = port if port else _pick_free_port(18787)
    app = create_app(desktop_mode=True)
    server = make_server(host, actual_port, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://{host}:{actual_port}/"
    if sys.stdout is not None:
        print(f"BiliDown Studio 已启动: {url}")

    if no_open:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
        return

    browser_exe = None if use_browser_tab else _find_chromium_app_browser()
    if browser_exe:
        profile_dir = data_dir() / "app_window_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            browser_exe,
            f"--app={url}",
            "--window-size=1280,860",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            proc = subprocess.Popen(cmd)
            proc.wait()
            server.shutdown()
            return
        except KeyboardInterrupt:
            server.shutdown()
            return
        except Exception:
            pass

    # Fallback: open default browser tab and stay alive until Ctrl+C
    webbrowser.open(url)
    if sys.stdout is not None:
        print("已在默认浏览器中打开 BiliDown Studio（按 Ctrl+C 退出服务）")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
