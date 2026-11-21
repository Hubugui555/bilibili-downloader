#!/usr/bin/env python3
"""BiliDown Studio Web Server entry point (Cloudreve server & standalone web mode)."""

import argparse

from bilidown.web.app import create_app

# WSGI application object (auto-detects Cloudreve server mode vs desktop mode)
app = create_app()


def main():
    parser = argparse.ArgumentParser(description="BiliDown Studio Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="监听端口 (默认: 8787)")
    parser.add_argument("--threads", type=int, default=12, help="工作线程数 (默认: 12)")
    parser.add_argument("--desktop", action="store_true", help="强制启用本地桌面模式（允许打开文件夹）")
    parser.add_argument("--server", action="store_true", help="强制启用服务器模式（禁用本地目录操作）")
    args = parser.parse_args()

    wsgi_app = app
    if args.desktop:
        wsgi_app = create_app(desktop_mode=True)
    elif args.server:
        wsgi_app = create_app(desktop_mode=False)

    print(f"BiliDown Studio Web 服务运行于 http://{args.host}:{args.port}/")
    try:
        from waitress import serve

        serve(wsgi_app, host=args.host, port=args.port, threads=args.threads)
    except ImportError:
        wsgi_app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
