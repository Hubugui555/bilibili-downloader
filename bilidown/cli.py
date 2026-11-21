"""Modern CLI for BiliDown 2.0 (supports Rich output with plain-text fallback)."""

import argparse
import sys
import time
from pathlib import Path

from . import __version__, api, engine
from .config import get_sessdata, save_config, save_cookie_string
from .constants import AUDIO_FORMATS, QUALITY_MAP
from .danmaku import download_extras_and_merge
from .parser import (
    extract_bvid,
    parse_episode_ranges,
    parse_target,
    safe_filename,
)
from .wbi import invalidate_wbi_cache


def _print_banner() -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel

        Console().print(
            Panel.fit(
                f"[bold magenta]BiliDown Studio[/bold magenta] [cyan]v{__version__}[/cyan]\n"
                "[dim]Bilibili 视频 · 番剧 · 课程 · 合集 · 收藏夹 · 音频下载器[/dim]",
                border_style="magenta",
            )
        )
    except ImportError:
        print(f"=== BiliDown Studio v{__version__} ===")


def login_qrcode(gui_mode: bool = False):
    """Bilibili QR code login (CLI ASCII QR or GUI dict)."""
    if not gui_mode:
        print("正在获取二维码...")
    result = api.generate_qrcode()
    if not result:
        if not gui_mode:
            print("获取二维码失败")
        return None if gui_mode else False

    if gui_mode:
        return result

    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(result["url"])
    qr.print_ascii(invert=True)
    print("\n请使用 Bilibili App 扫描上方二维码登录 (按 Ctrl+C 取消)\n")

    key = result["key"]
    while True:
        try:
            res = api.poll_login_status(key)
            st = res["status"]
            if st == "success":
                print("\n✓ 登录成功! 完整登录 Cookie 已保存到 config.json")
                return True
            if st == "scanned":
                sys.stdout.write("\r📱 已扫码，请在手机上确认登录...   ")
                sys.stdout.flush()
            elif st == "expired":
                print("\n✗ 二维码已失效，请重新运行 --login")
                return False
            else:
                sys.stdout.write("\r⏳ 等待扫码...                      ")
                sys.stdout.flush()
            time.sleep(1.5)
        except KeyboardInterrupt:
            print("\n已取消")
            return False


def check_login_status() -> None:
    if not get_sessdata():
        print("未登录 (no SESSDATA)")
        return
    profile = api.get_login_profile()
    if profile.get("logged_in"):
        print(f"✓ 已登录: {profile['name']}")
        print(f"  等级: Lv.{profile['level']} ({profile['vip_label']})")
        print(f"  B币/硬币: {profile['coin']}")
    else:
        print("✗ 登录已失效，请重新运行 --login 或导入 Cookie")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BiliDown 2.0 — Bilibili 视频/番剧/课程/合集/收藏夹/音频下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python bili_downloader.py --gui                             # 启动现代桌面 Studio
  python bili_downloader.py --login                           # 终端二维码登录
  python bili_downloader.py --check-login                     # 检查登录状态
  python bili_downloader.py BV1xx411c7mD -q 4K --codec hevc   # 指定 4K HEVC 下载
  python bili_downloader.py "https://b23.tv/xxxxxx" --audio-only --audio-format mp3
  python bili_downloader.py "https://www.bilibili.com/cheese/play/ep12345" --episodes "1-10,15" -w 3
  python bili_downloader.py BV1xx411c7mD --cover --danmaku --subtitle
        """,
    )
    parser.add_argument("url", nargs="?", help="B站链接 / BV号 / av号 / b23.tv 短链 / toview")
    parser.add_argument("-o", "--output", default="./downloads", help="输出目录 (默认: ./downloads)")
    parser.add_argument(
        "-q", "--quality", default="1080P", choices=list(QUALITY_MAP.keys()), help="画质选择 (默认: 1080P)"
    )
    parser.add_argument(
        "--codec", default="auto", choices=["auto", "avc", "hevc", "av1"], help="视频编码偏好 (默认: auto)"
    )
    parser.add_argument("-p", "--page", type=int, default=0, help="指定分P序号")
    parser.add_argument("--episodes", help="指定集数/分P/列表范围，如 '1-10,15,20-25'")
    parser.add_argument("-w", "--workers", type=int, default=2, help="批量下载并发数 (默认: 2)")
    parser.add_argument("--audio-only", action="store_true", help="仅提取音频")
    parser.add_argument(
        "--audio-format", default="m4a", choices=list(AUDIO_FORMATS), help="仅音频输出格式 (默认: m4a)"
    )
    parser.add_argument("--cover", action="store_true", help="同时下载封面")
    parser.add_argument("--danmaku", action="store_true", help="同时下载弹幕并软封装")
    parser.add_argument("--subtitle", action="store_true", help="同时下载字幕(含AI)并软封装")
    parser.add_argument("--no-cdn-probe", action="store_true", help="跳过 CDN 线路测速")
    parser.add_argument("--sessdata", help="设置 SESSDATA (登录凭证)")
    parser.add_argument("--cookie", help="导入浏览器完整 Cookie")
    parser.add_argument("--login", action="store_true", help="二维码登录")
    parser.add_argument("--check-login", action="store_true", help="检查登录状态")
    parser.add_argument("--gui", action="store_true", help="启动 BiliDown Studio 桌面应用")
    parser.add_argument("--web", action="store_true", help="仅启动 Web Studio 后台服务")
    parser.add_argument("--port", type=int, default=0, help="Web/Studio 监听端口")
    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui or args.web:
        from .launcher import launch_desktop

        launch_desktop(port=args.port or None, no_open=args.web)
        return

    if args.login:
        login_qrcode()
        return

    if args.check_login:
        check_login_status()
        return

    if args.sessdata:
        save_config({"sessdata": args.sessdata})
        invalidate_wbi_cache()
        print("✓ SESSDATA 已保存到 config.json (已替换原有凭证)")
    if args.cookie:
        save_cookie_string(args.cookie)
        invalidate_wbi_cache()
        print("✓ 完整 Cookie 已保存到 config.json")

    if not args.url:
        parser.print_help()
        return

    _print_banner()
    output_dir = Path(args.output)
    cdn_probe = not args.no_cdn_probe
    target = parse_target(args.url, resolve_short=True)
    kind = target["kind"]

    if kind == "unknown":
        print("✗ 无法解析链接或 BV 号")
        sys.exit(1)

    # Collection (bangumi / course) with optional --episodes & --workers
    if kind in ("bangumi", "course"):
        collection = api.get_media_collection(target["resolved"], allow_season=True)
        eps = collection["episodes"]
        if args.episodes:
            indices = parse_episode_ranges(args.episodes, len(eps))
            selected_ids = [eps[i - 1]["ep_id"] for i in indices]
        elif target.get("ep_id"):
            selected_ids = [collection["current_ep_id"]]
        else:
            selected_ids = [ep["ep_id"] for ep in eps]

        print(f"📺 {collection['title']} | 选中 {len(selected_ids)} / {len(eps)} 集 | 并发 {args.workers}")
        summary = engine.download_selected_episodes(
            collection,
            selected_ids,
            output_dir,
            args.quality,
            max_workers=args.workers,
            danmaku=args.danmaku,
            subtitle=args.subtitle,
            codec=args.codec,
            audio_only=args.audio_only,
            audio_format=args.audio_format,
            cdn_probe=cdn_probe,
        )
        print(
            f"\n完成统计: 成功 {summary['success']} / {summary['total']}"
            + (f", 失败 {summary['failed']}" if summary["failed"] else "")
        )
        return

    # Playlist (ugc_season / series / favlist / watchlater)
    if kind in ("ugc_season", "series", "favlist", "watchlater"):
        preview = api.inspect_url(target["resolved"])
        items = preview["items"]
        if args.episodes:
            indices = parse_episode_ranges(args.episodes, len(items))
            items = [items[i - 1] for i in indices]
        print(f"📂 {preview['title']} | 共 {len(items)} 个视频")
        for i, item in enumerate(items, 1):
            bv = item["bvid"]
            print(f"\n[{i}/{len(items)}] {item['title']} ({bv})")
            ok = engine.download_single(
                bv,
                output_dir,
                args.quality,
                codec=args.codec,
                audio_only=args.audio_only,
                audio_format=args.audio_format,
                cdn_probe=cdn_probe,
                folder_override=preview["title"],
                index=item["index"],
            )
            if ok and not args.audio_only and (args.cover or args.danmaku or args.subtitle):
                download_extras_and_merge(
                    bv,
                    output_dir,
                    cover=args.cover,
                    danmaku=args.danmaku,
                    subtitle=args.subtitle,
                    video_dir=output_dir / safe_filename(preview["title"]),
                )
        return

    # Regular video (single or multi-P)
    bvid = target.get("bvid")
    if not bvid and target.get("aid"):
        bvid = api.get_video_info_by_aid(target["aid"]).get("bvid")

    if args.page > 0 and bvid:
        ok = engine.download_single(
            bvid,
            output_dir,
            args.quality,
            page=args.page,
            codec=args.codec,
            audio_only=args.audio_only,
            audio_format=args.audio_format,
            cdn_probe=cdn_probe,
        )
    elif args.episodes and bvid:
        info = api.get_video_info(bvid)
        pages = info.get("pages") or []
        indices = parse_episode_ranges(args.episodes, max(1, len(pages)))
        ok = True
        for p_idx in indices:
            engine.download_single(
                bvid,
                output_dir,
                args.quality,
                page=p_idx,
                codec=args.codec,
                audio_only=args.audio_only,
                audio_format=args.audio_format,
                cdn_probe=cdn_probe,
                info=info,
            )
    else:
        engine.download_batch(
            target["resolved"],
            output_dir,
            args.quality,
            danmaku=args.danmaku,
            subtitle=args.subtitle,
            codec=args.codec,
            audio_only=args.audio_only,
            audio_format=args.audio_format,
            cdn_probe=cdn_probe,
        )
        ok = True

    if bvid and ok and not args.audio_only and (args.cover or args.danmaku or args.subtitle):
        print("\n📎 下载附加内容...")
        merged = download_extras_and_merge(
            bvid, output_dir, cover=args.cover, danmaku=args.danmaku, subtitle=args.subtitle
        )
        if merged:
            print(f"\n✓ 弹幕/字幕已合并: {merged}")
