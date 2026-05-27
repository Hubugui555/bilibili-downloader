"""Bilibili 视频下载器 - 精美桌面版"""
import sys, os, threading, json, subprocess, time, math
from pathlib import Path
from tkinter import filedialog
from PIL import Image

import customtkinter as ctk

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bili_downloader as bd

# ─── 主题 ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# 亮色主题颜色
PINK = "#fb7299"
BLUE = "#00a1d6"
PURPLE = "#7c3aed"
BG = "#f5f5f8"
SURFACE = "#ffffff"
SURFACE2 = "#f0f0f4"
BORDER = "#e2e2e8"
TEXT = "#1a1a2e"
TEXT_DIM = "#8a8a9a"

FONT = ("Microsoft YaHei", 10)
FONT_BOLD = ("Microsoft YaHei", 10, "bold")
FONT_TITLE = ("Microsoft YaHei", 18, "bold")
FONT_MONO = ("Consolas", 10)


class LogRedirector:
    def __init__(self, callback):
        self.callback = callback
    def write(self, text):
        if text.strip():
            self.callback(text)
    def flush(self):
        pass


# ─── 自定义组件 ──────────────────────────────────────────────────────────

class Divider(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, height=1, fg_color=BORDER, **kwargs)


class StatusBadge(ctk.CTkLabel):
    def __init__(self, master, **kwargs):
        super().__init__(master, font=("Microsoft YaHei", 9), text_color=TEXT_DIM, **kwargs)


class IconButton(ctk.CTkButton):
    def __init__(self, master, text="", command=None, **kwargs):
        kwargs.pop("font", None)
        super().__init__(
            master, text=text, command=command,
            font=("Microsoft YaHei", 12),
            corner_radius=8, border_width=0,
            fg_color=PINK, hover_color="#e8618a",
            text_color="white", **kwargs
        )


class GhostButton(ctk.CTkButton):
    def __init__(self, master, text="", command=None, **kwargs):
        kwargs.pop("font", None)
        super().__init__(
            master, text=text, command=command,
            font=("Microsoft YaHei", 11),
            corner_radius=8, border_width=1, border_color=BORDER,
            fg_color="transparent", hover_color=SURFACE2,
            text_color=TEXT, **kwargs
        )


# ─── 选集窗口 ──────────────────────────────────────────────────────────

class EpisodeSelectorWindow(ctk.CTkToplevel):
    """课程/番剧选集弹窗（分页渲染），通过 on_confirm 回调返回选中的 ep_id 列表。"""

    PAGE_SIZE = 30

    def __init__(self, parent, collection, selected_ids, on_confirm=None):
        super().__init__(parent)
        self._collection = collection
        self._on_confirm = on_confirm
        self._episodes = collection["episodes"]
        self._current_ep_id = collection["current_ep_id"]
        self.selected_ids = set(selected_ids)

        self._total_pages = max(1, math.ceil(len(self._episodes) / self.PAGE_SIZE))
        self._current_page = 0
        self._row_widgets = []

        # 定位到当前集所在页
        current_index = next(
            (i for i, ep in enumerate(self._episodes)
             if ep["ep_id"] == self._current_ep_id), 0)
        self._current_page = current_index // self.PAGE_SIZE

        media_type = collection["media_type"]
        type_name = "课程" if media_type == "course" else "番剧"
        n = len(self._episodes)

        self.title("选择下载集数")
        self.geometry("560x620")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 标题
        ctk.CTkLabel(self, text="选择下载集数",
                     font=("Microsoft YaHei", 16, "bold"),
                     text_color=TEXT).pack(pady=(16, 4))

        # 系列信息
        ctk.CTkLabel(self, text=f"{type_name}：{collection['title']}",
                     font=FONT, text_color=TEXT).pack(pady=(0, 4))

        # 计数
        self._count_label = ctk.CTkLabel(
            self, text=f"共 {n} 集 / 已选 {len(selected_ids)} 集",
            font=FONT, text_color=TEXT_DIM)
        self._count_label.pack(pady=(0, 8))

        # 快捷按钮
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 6))
        for text, color, hcolor, cmd in [
            ("全选", BLUE, "#008db5", self._select_all),
            ("全不选", TEXT_DIM, "#6a6a7a", self._select_none),
            ("反选", PURPLE, "#6629b3", self._invert_selection),
            ("仅当前集", PINK, "#e8618a", self._select_current),
        ]:
            ctk.CTkButton(btn_frame, text=text, width=66, height=28,
                          font=("Microsoft YaHei", 10), fg_color=color,
                          hover_color=hcolor, command=cmd).pack(side="left", padx=(0, 6))

        # 范围输入
        range_frame = ctk.CTkFrame(self, fg_color="transparent")
        range_frame.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(range_frame, text="范围", font=FONT,
                     text_color=TEXT_DIM).pack(side="left", padx=(0, 6))
        self._range_entry = ctk.CTkEntry(
            range_frame, placeholder_text="如：1-10,15,20-25",
            height=30, corner_radius=6, fg_color=BG, border_color=BORDER,
            font=FONT)
        self._range_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._range_entry.bind("<Return>", lambda e: self._apply_range())
        ctk.CTkButton(range_frame, text="应用", width=50, height=30,
                      font=("Microsoft YaHei", 10), fg_color=BLUE,
                      hover_color="#008db5",
                      command=self._apply_range).pack(side="left")
        self._range_error = ctk.CTkLabel(self, text="", font=("Microsoft YaHei", 9),
                                          text_color="#f87171")
        self._range_error.pack(fill="x", padx=20, pady=(0, 4))

        # 集数列表区域（可滚动，仅当前页的项）
        list_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=8,
                                   border_width=1, border_color=BORDER)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self._list_scroll = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent")
        self._list_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        self._list_container = ctk.CTkFrame(self._list_scroll, fg_color="transparent")
        self._list_container.pack(fill="x")

        # 分页栏
        page_bar = ctk.CTkFrame(self, fg_color="transparent")
        page_bar.pack(fill="x", padx=20, pady=(0, 4))
        self._prev_btn = ctk.CTkButton(
            page_bar, text="上一页", width=60, height=28,
            font=("Microsoft YaHei", 10), fg_color=TEXT_DIM,
            hover_color="#6a6a7a", command=self._previous_page)
        self._prev_btn.pack(side="left")
        self._page_label = ctk.CTkLabel(page_bar, text="", font=FONT,
                                         text_color=TEXT)
        self._page_label.pack(side="left", padx=(8, 8))
        self._next_btn = ctk.CTkButton(
            page_bar, text="下一页", width=60, height=28,
            font=("Microsoft YaHei", 10), fg_color=TEXT_DIM,
            hover_color="#6a6a7a", command=self._next_page)
        self._next_btn.pack(side="left")

        # 跳转栏
        jump_bar = ctk.CTkFrame(self, fg_color="transparent")
        jump_bar.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(jump_bar, text="跳转到", font=FONT,
                     text_color=TEXT_DIM).pack(side="left", padx=(0, 4))
        self._jump_entry = ctk.CTkEntry(
            jump_bar, placeholder_text="页码", width=50, height=28,
            corner_radius=6, fg_color=BG, border_color=BORDER,
            font=FONT, justify="center")
        self._jump_entry.pack(side="left")
        self._jump_entry.bind("<Return>", lambda e: self._do_jump())
        self._jump_entry.bind("<KeyRelease>", self._on_jump_input)
        self._jump_range_label = ctk.CTkLabel(
            jump_bar, text="", font=("Microsoft YaHei", 9),
            text_color=TEXT_DIM)
        self._jump_range_label.pack(side="left", padx=(6, 4))
        ctk.CTkButton(jump_bar, text="跳转", width=46, height=28,
                      font=("Microsoft YaHei", 10), fg_color=BLUE,
                      hover_color="#008db5",
                      command=self._do_jump).pack(side="left", padx=(4, 0))

        # 底部按钮
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(bottom, text="取消", width=80, height=34,
                      font=("Microsoft YaHei", 12), fg_color=TEXT_DIM,
                      hover_color="#6a6a7a",
                      command=self._on_cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bottom, text="确认选择", width=90, height=34,
                      font=("Microsoft YaHei", 12, "bold"), fg_color=PINK,
                      hover_color="#e8618a",
                      command=self._on_confirm_click).pack(side="right")

        # 首次渲染
        self._render_page()

    # ─── 页面渲染 ─────────────────────────────────────────────────

    def _render_page(self):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets.clear()

        start = self._current_page * self.PAGE_SIZE
        end = min(start + self.PAGE_SIZE, len(self._episodes))
        page_eps = self._episodes[start:end]

        for ep in page_eps:
            ep_id = ep["ep_id"]
            checked = ep_id in self.selected_ids

            row = ctk.CTkFrame(self._list_container, fg_color="transparent")
            row.pack(fill="x", pady=1)
            self._row_widgets.append(row)

            var = ctk.BooleanVar(value=checked)
            cb = ctk.CTkCheckBox(
                row, text="", variable=var, width=24,
                fg_color=PINK, hover_color="#e8618a",
                border_color=BORDER, corner_radius=3,
                border_width=1, checkmark_color="white",
                command=lambda ep_id=ep_id, v=var: self._toggle_episode(ep_id, v.get()))
            cb.pack(side="left", padx=(4, 8))

            idx_text = f"第{ep['index']}集"
            if ep_id == self._current_ep_id:
                idx_text += "  [当前]"
            idx_lbl = ctk.CTkLabel(row, text=idx_text, font=FONT,
                                    text_color=TEXT, width=80, anchor="w")
            idx_lbl.pack(side="left")
            self._row_widgets.append(idx_lbl)

            title_lbl = ctk.CTkLabel(row, text=ep.get("title", ""),
                                      font=FONT, text_color=TEXT, anchor="w")
            title_lbl.pack(side="left", fill="x", expand=True, padx=(4, 4))
            self._row_widgets.append(title_lbl)

        self._update_page_controls()

    def _update_page_controls(self):
        self._page_label.configure(
            text=f"第 {self._current_page + 1} / {self._total_pages} 页")
        self._prev_btn.configure(
            state="normal" if self._current_page > 0 else "disabled")
        self._next_btn.configure(
            state="normal" if self._current_page + 1 < self._total_pages else "disabled")

    # ─── 选择操作 ─────────────────────────────────────────────────

    def _toggle_episode(self, ep_id, checked):
        if checked:
            self.selected_ids.add(ep_id)
        else:
            self.selected_ids.discard(ep_id)
        self._update_counter()

    def _update_counter(self):
        n = len(self.selected_ids)
        total = len(self._episodes)
        self._count_label.configure(text=f"共 {total} 集 / 已选 {n} 集")

    def _select_all(self):
        self.selected_ids = {ep["ep_id"] for ep in self._episodes}
        self._render_page()
        self._update_counter()

    def _select_none(self):
        self.selected_ids.clear()
        self._render_page()
        self._update_counter()

    def _invert_selection(self):
        all_ids = {ep["ep_id"] for ep in self._episodes}
        self.selected_ids = all_ids - self.selected_ids
        self._render_page()
        self._update_counter()

    def _select_current(self):
        self.selected_ids = {self._current_ep_id}
        self._go_to_episode(self._current_ep_id)
        self._render_page()
        self._update_counter()

    def _apply_range(self):
        text = self._range_entry.get().strip()
        self._range_error.configure(text="")
        if not text:
            self._range_error.configure(text="请输入集数范围，例如：1-10,15,20-25")
            return
        try:
            indices = bd.parse_episode_ranges(text, len(self._episodes))
        except ValueError as e:
            self._range_error.configure(text=str(e))
            return
        self.selected_ids = {self._episodes[i - 1]["ep_id"] for i in indices}
        # 跳到所选范围第一项所在页
        first_idx = indices[0] - 1
        self._current_page = first_idx // self.PAGE_SIZE
        self._render_page()
        self._update_counter()

    # ─── 翻页 ─────────────────────────────────────────────────────

    def _previous_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._render_page()

    def _next_page(self):
        if self._current_page + 1 < self._total_pages:
            self._current_page += 1
            self._render_page()

    def _go_to_episode(self, ep_id):
        idx = next(
            (i for i, ep in enumerate(self._episodes) if ep["ep_id"] == ep_id), 0)
        self._current_page = idx // self.PAGE_SIZE

    def _on_jump_input(self, event=None):
        text = self._jump_entry.get().strip()
        self._jump_range_label.configure(text="")
        if not text.isdigit():
            return
        page = int(text)
        if page < 1 or page > self._total_pages:
            self._jump_range_label.configure(
                text=f"共 {self._total_pages} 页", text_color="#f87171")
            return
        start = (page - 1) * self.PAGE_SIZE + 1
        end = min(page * self.PAGE_SIZE, len(self._episodes))
        self._jump_range_label.configure(
            text=f"第 {start}-{end} 集", text_color=TEXT_DIM)

    def _do_jump(self):
        text = self._jump_entry.get().strip()
        if not text.isdigit():
            return
        page = int(text)
        if page < 1 or page > self._total_pages:
            return
        self._current_page = page - 1
        self._render_page()

    # ─── 确认 / 取消 ──────────────────────────────────────────────

    def _on_confirm_click(self):
        if not self.selected_ids:
            self._range_error.configure(text="请至少选择一集")
            return
        # 按原始集序返回
        selected = [ep["ep_id"] for ep in self._episodes
                    if ep["ep_id"] in self.selected_ids]
        if self._on_confirm:
            self._on_confirm(selected)
        self.destroy()

    def _on_cancel(self):
        self.destroy()


# ─── 主窗口 ────────────────────────────────────────────────────────────

class BiliApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("BiliDown — Bilibili 视频下载器")
        self.geometry("700x680")
        self.minsize(640, 600)
        self._running = False
        self._stop_event = threading.Event()
        self._log_lines = []
        self._collection = None
        self._selected_ep_ids = []
        self._collection_url = ""

        # 居中
        self.update_idletasks()
        w, h = 700, 680
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._configure_styles()
        self._build_ui()

        # stdout 重定向
        self._orig_stdout = sys.stdout
        sys.stdout = LogRedirector(self._on_log)

        # 启动后检查登录
        self.after(500, self._check_login)

    def _configure_styles(self):
        self.configure(fg_color=BG)

    # ─── UI 构建 ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ===== 顶部 =====
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=28, pady=(24, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left")

        # Logo + 标题
        logo_frame = ctk.CTkFrame(brand, fg_color="transparent")
        logo_frame.pack(anchor="w")

        logo_canvas = ctk.CTkCanvas(logo_frame, width=42, height=42,
                                     bg=BG, highlightthickness=0)
        logo_canvas.pack(side="left", padx=(0, 12))
        logo_canvas.create_oval(2, 2, 40, 40, fill=PINK, outline="")
        logo_canvas.create_text(21, 21, text="B", fill="white",
                                font=("Microsoft YaHei", 20, "bold"))
        # 发光效果
        logo_canvas.create_oval(4, 4, 38, 38, outline=PURPLE, width=1, dash=(4, 4))

        titles = ctk.CTkFrame(logo_frame, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="BiliDown", font=FONT_TITLE,
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text="Bilibili 视频下载器",
                     font=("Microsoft YaHei", 10), text_color=TEXT_DIM).pack(anchor="w")

        # 右侧: 登录区域
        rbar = ctk.CTkFrame(header, fg_color="transparent")
        rbar.pack(side="right")
        self.login_badge = StatusBadge(rbar, text="未登录")
        self.login_badge.pack(side="left", padx=(0, 6))
        self.cookie_btn = GhostButton(rbar, text="导入Cookie", width=78, height=28,
                                       command=self._import_cookie,
                                       font=("Microsoft YaHei", 10))
        self.cookie_btn.pack(side="left", padx=(0, 6))
        self.login_btn = GhostButton(rbar, text="登录", width=60, height=28,
                                      command=self._login,
                                      font=("Microsoft YaHei", 10))
        self.login_btn.pack(side="left")

        # ===== 主卡片 =====
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid(row=1, column=0, padx=24, pady=(8, 10), sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        # 输入行
        ctk.CTkLabel(card, text="视频链接", font=FONT,
                     text_color=TEXT_DIM).grid(row=0, column=0, padx=(20, 10), pady=(18, 4), sticky="w")
        self.url_entry = ctk.CTkEntry(card, placeholder_text="BV号 或 B站链接...",
                                      height=36, corner_radius=8,
                                      fg_color=BG, border_color=BORDER,
                                      font=FONT)
        self.url_entry.grid(row=0, column=1, padx=(0, 6), pady=(18, 4), sticky="ew")
        self.url_entry.bind("<Return>", lambda e: self._fetch_info())
        self.url_entry.bind("<KeyRelease>", self._on_url_changed)
        self.fetch_btn = IconButton(card, text="获取信息", width=80, height=36,
                                     command=self._fetch_info)
        self.fetch_btn.grid(row=0, column=2, padx=(0, 6), pady=(18, 4))
        self.select_btn = GhostButton(card, text="选择集数", width=72, height=36,
                                       state="disabled", command=self._open_episode_selector)
        self.select_btn.grid(row=0, column=3, padx=(0, 20), pady=(18, 4))

        # 信息显示
        self.info_frame = ctk.CTkFrame(card, fg_color=BG, corner_radius=8,
                                       height=46, border_width=1, border_color=BORDER)
        self.info_frame.grid(row=1, column=0, columnspan=4, padx=20, pady=(0, 14), sticky="ew")
        self.info_frame.grid_propagate(False)
        self.info_icon = ctk.CTkLabel(self.info_frame, text="", font=("Microsoft YaHei", 14), width=20)
        self.info_icon.place(x=14, rely=0.5, anchor="w")
        self.info_label = ctk.CTkLabel(self.info_frame, text="等待输入...",
                                       font=FONT, text_color=TEXT_DIM)
        self.info_label.place(relx=0.5, rely=0.5, anchor="center")

        # 选项行
        opt_frame = ctk.CTkFrame(card, fg_color="transparent")
        opt_frame.grid(row=2, column=0, columnspan=4, padx=20, pady=(0, 14), sticky="ew")
        opt_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(opt_frame, text="画质", font=FONT,
                     text_color=TEXT_DIM).grid(row=0, column=0, padx=(0, 6))
        self.quality_var = ctk.StringVar(value="1080P")
        self.quality_cb = ctk.CTkComboBox(opt_frame, values=list(bd.QUALITY_MAP.keys()),
                                          variable=self.quality_var, width=110,
                                          state="readonly", corner_radius=6,
                                          fg_color=BG, border_color=BORDER,
                                          dropdown_fg_color=SURFACE,
                                          button_color=PINK, button_hover_color="#e8618a")
        self.quality_cb.grid(row=0, column=1, padx=(0, 18))

        # 复选框
        self.cover_v = ctk.BooleanVar(value=True)
        self.danmaku_v = ctk.BooleanVar(value=True)
        self.subtitle_v = ctk.BooleanVar(value=False)

        def mk_cb(text, var, col):
            cb = ctk.CTkCheckBox(opt_frame, text=text, variable=var,
                                 fg_color=PINK, hover_color="#e8618a",
                                 border_color=BORDER, text_color=TEXT,
                                 font=FONT, corner_radius=3,
                                 border_width=1, checkmark_color="white")
            cb.grid(row=0, column=col, padx=(0, 10))
        mk_cb("封面", self.cover_v, 2)
        mk_cb("弹幕", self.danmaku_v, 3)
        mk_cb("字幕", self.subtitle_v, 4)

        ctk.CTkLabel(opt_frame, text="并发", font=FONT,
                     text_color=TEXT_DIM).grid(row=0, column=5, padx=(8, 6))
        self.worker_var = ctk.StringVar(value="2")
        self.worker_cb = ctk.CTkComboBox(opt_frame, values=["1", "2", "3", "4"],
                                          variable=self.worker_var, width=60,
                                          state="readonly", corner_radius=6,
                                          fg_color=BG, border_color=BORDER,
                                          dropdown_fg_color=SURFACE,
                                          button_color=PINK, button_hover_color="#e8618a")
        self.worker_cb.grid(row=0, column=6, padx=(0, 10))

        # 输出目录
        out_frame = ctk.CTkFrame(card, fg_color="transparent")
        out_frame.grid(row=3, column=0, columnspan=4, padx=20, pady=(0, 18), sticky="ew")
        out_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out_frame, text="保存到", font=FONT,
                     text_color=TEXT_DIM).grid(row=0, column=0, padx=(0, 8))
        self.out_var = ctk.StringVar(value=str(Path.cwd() / "downloads"))
        self.out_entry = ctk.CTkEntry(out_frame, textvariable=self.out_var,
                                      height=32, corner_radius=6,
                                      fg_color=BG, border_color=BORDER,
                                      font=FONT)
        self.out_entry.grid(row=0, column=1, padx=(0, 6), sticky="ew")
        GhostButton(out_frame, text="浏览", width=54, height=32,
                     command=self._browse_out).grid(row=0, column=2)

        # 进度条
        self.progress_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.progress_frame.grid(row=4, column=0, columnspan=4, padx=20, pady=(0, 12), sticky="ew")
        self.progress_frame.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=6,
                                                corner_radius=3,
                                                fg_color=BORDER,
                                                progress_color=PINK)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0)
        self.pct_label = ctk.CTkLabel(self.progress_frame, text="0%",
                                       font=("Microsoft YaHei", 9), text_color=TEXT_DIM,
                                       width=36)
        self.pct_label.grid(row=0, column=1, padx=(8, 0))
        self.progress_frame.grid_remove()

        # ===== 日志区域 =====
        log_card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14,
                                border_width=1, border_color=BORDER)
        log_card.grid(row=2, column=0, padx=24, pady=(0, 12), sticky="nsew")
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.grid(row=0, column=0, padx=18, pady=(14, 6), sticky="ew")
        ctk.CTkLabel(log_header, text="运行日志", font=FONT_BOLD,
                     text_color=TEXT).pack(side="left")
        self.log_count = ctk.CTkLabel(log_header, text="",
                                      font=("Microsoft YaHei", 9), text_color=TEXT_DIM)
        self.log_count.pack(side="left", padx=(8, 0))
        GhostButton(log_header, text="清空", width=44, height=22,
                     command=self._clear_log,
                     font=("Microsoft YaHei", 9)).pack(side="right")

        self.log_box = ctk.CTkTextbox(log_card, font=FONT_MONO,
                                       corner_radius=8,
                                       fg_color=BG, border_width=0,
                                       text_color="#c0c0c0", wrap="word")
        self.log_box.grid(row=1, column=0, padx=12, pady=(0, 14), sticky="nsew")
        self.log_box.tag_config("ok", foreground="#4ade80")
        self.log_box.tag_config("err", foreground="#f87171")
        self.log_box.tag_config("info", foreground="#60a5fa")

        self._log_init = True
        self._log("就绪 — 输入视频链接开始下载", "info")

        # ===== 底部 =====
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, padx=28, pady=(0, 20), sticky="ew")

        ctk.CTkLabel(bottom, text="BiliDown v1.0", font=("Microsoft YaHei", 9),
                     text_color=TEXT_DIM).pack(side="left")

        self.dl_btn = ctk.CTkButton(bottom, text="开始下载", height=40,
                                     font=("Microsoft YaHei", 14, "bold"),
                                     fg_color=PINK, hover_color="#e8618a",
                                     corner_radius=10,
                                     text_color="white",
                                     command=self._download)
        self.dl_btn.pack(side="right")

        self.stop_btn = ctk.CTkButton(bottom, text="停止", height=40,
                                       font=("Microsoft YaHei", 14, "bold"),
                                       fg_color="#ef4444", hover_color="#dc2626",
                                       corner_radius=10,
                                       text_color="white",
                                       command=self._stop_download)
        self.stop_btn.pack(side="right", padx=(0, 8))
        self.stop_btn.pack_forget()  # 初始隐藏

    # ─── 功能 ─────────────────────────────────────────────────────────

    def _log(self, msg, tag=""):
        if not hasattr(self, '_log_init'):
            return
        tag_map = {"ok": "ok", "err": "err", "info": "info"}
        t = tag_map.get(tag, "")
        self.log_box.insert("end", msg + "\n", t)
        self.log_box.see("end")
        self._log_lines.append(msg)
        self.log_count.configure(text=f"{len(self._log_lines)} 条")

    def _on_log(self, text):
        tag = ""
        if any(m in text for m in ("✓", "完成", "成功", "已保存")):
            tag = "ok"
        elif any(m in text for m in ("✗", "失败", "失效", "错误")):
            tag = "err"
        elif any(m in text for m in ("等待", "检测", "获取", "下载", "标题", "分P")):
            tag = "info"
        self._log(text.rstrip(), tag)

    def _clear_log(self):
        self.log_box.delete("1.0", "end")
        self._log_lines.clear()
        self.log_count.configure(text="")

    def _check_login(self):
        sess = bd.get_sessdata()
        if sess:
            try:
                import requests
                resp = requests.get("https://api.bilibili.com/x/web-interface/nav",
                                    headers=bd.build_headers(), timeout=10).json()
                if resp["code"] == 0:
                    name = resp["data"]["uname"]
                    self.login_badge.configure(text=f"已登录: {name}", text_color="#4ade80")
                    self.login_btn.configure(text="切换账号")
                    self._log(f"已登录: {name}", "ok")
                    return
            except:
                pass
        self.login_badge.configure(text="未登录", text_color=TEXT_DIM)

    def _login(self):
        self.login_btn.configure(state="disabled")
        self._login_cancel = threading.Event()
        threading.Thread(target=self._do_login, daemon=True).start()

    def _import_cookie(self):
        dialog = ctk.CTkInputDialog(
            text="在浏览器中打开可播放的课程视频，从开发者工具 Network 请求头复制完整 Cookie 后粘贴到这里：",
            title="导入浏览器 Cookie",
        )
        cookie = dialog.get_input()
        if not cookie or not cookie.strip():
            return
        try:
            cookie = cookie.strip()
            if cookie.lower().startswith("cookie:"):
                cookie = cookie.split(":", 1)[1].strip()
            import requests
            resp = requests.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={**bd.HEADERS, "Cookie": cookie}, timeout=10
            ).json()
            if resp.get("code") != 0:
                raise Exception(resp.get("message", "Cookie 登录校验失败"))
            bd.save_cookie_string(cookie)
            self._log(f"已导入浏览器 Cookie: {resp['data'].get('uname', '?')}", "ok")
            self._check_login()
        except Exception as e:
            self._log(f"导入 Cookie 失败: {e}", "err")

    def _do_login(self):
        self._log("正在获取二维码...", "info")
        try:
            result = bd.login_qrcode(gui_mode=True)
            if result is None:
                self._log("获取二维码失败", "err")
                self.after(0, lambda: self.login_btn.configure(state="normal"))
                return
            # 在主线程中显示二维码弹窗
            self.after(0, lambda: self._show_qr_dialog(result["qr_path"], result["key"]))
        except Exception as e:
            self._log(f"登录失败: {e}", "err")
            self.after(0, lambda: self.login_btn.configure(state="normal"))

    def _show_qr_dialog(self, qr_path, key):
        """显示二维码弹窗"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("扫码登录")
        dialog.geometry("320x380")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._cancel_login(dialog))

        # 标题
        ctk.CTkLabel(dialog, text="请使用 Bilibili App 扫码登录",
                     font=("Microsoft YaHei", 12, "bold"),
                     text_color=TEXT).pack(pady=(20, 10))

        # 二维码图片
        try:
            img = Image.open(qr_path)
            # 缩放到合适大小
            img = img.resize((240, 240), Image.Resampling.LANCZOS)
            from customtkinter import CTkImage
            self._qr_ctk_image = CTkImage(light_image=img, dark_image=img, size=(240, 240))
            qr_label = ctk.CTkLabel(dialog, image=self._qr_ctk_image, text="")
            qr_label.pack(pady=10)
        except Exception as e:
            ctk.CTkLabel(dialog, text=f"二维码加载失败: {e}",
                         text_color="#f87171").pack(pady=10)

        # 状态标签
        self._login_status_label = ctk.CTkLabel(dialog, text="等待扫码...",
                                                 font=("Microsoft YaHei", 10),
                                                 text_color=TEXT_DIM)
        self._login_status_label.pack(pady=5)

        # 取消按钮
        ctk.CTkButton(dialog, text="取消登录", width=100, height=32,
                      fg_color="#ef4444", hover_color="#dc2626",
                      command=lambda: self._cancel_login(dialog)).pack(pady=10)

        # 启动轮询线程
        self._login_dialog = dialog
        threading.Thread(target=self._poll_login, args=(key, dialog), daemon=True).start()

    def _poll_login(self, key, dialog):
        """轮询登录状态"""
        while not self._login_cancel.is_set():
            try:
                result = bd.poll_login_status(key)
                status = result.get("status")

                if status == "success":
                    self._log("登录成功!", "ok")
                    self.after(0, lambda: dialog.destroy())
                    self.after(100, self._check_login)
                    return
                elif status == "scanned":
                    self.after(0, lambda: self._login_status_label.configure(
                        text="已扫码，等待确认...", text_color=BLUE))
                elif status == "expired":
                    self._log("二维码已失效，请重新登录", "err")
                    self.after(0, lambda: self._login_status_label.configure(
                        text="二维码已失效", text_color="#f87171"))
                    self.after(0, lambda: self.login_btn.configure(state="normal"))
                    return
                else:
                    self.after(0, lambda: self._login_status_label.configure(
                        text="等待扫码...", text_color=TEXT_DIM))

                time.sleep(1.5)
            except Exception as e:
                self._log(f"轮询出错: {e}", "err")
                break

        # 取消或出错
        self.after(0, lambda: self.login_btn.configure(state="normal"))

    def _cancel_login(self, dialog):
        """取消登录"""
        self._login_cancel.set()
        dialog.destroy()
        self.login_btn.configure(state="normal")
        self._log("已取消登录", "info")

    def _on_url_changed(self, event=None):
        raw = self.url_entry.get().strip()
        ep_id = bd.extract_epid(raw)
        is_cheese = "/cheese/" in raw.lower()
        is_bangumi = "/bangumi/" in raw.lower()
        # 仅当用户手动编辑（非 _fetch_info 写入）时才清空
        if self._collection and raw != self._collection_url:
            self._collection = None
            self._selected_ep_ids = []
            self._collection_url = ""
            self.select_btn.configure(state="disabled")
            self.info_label.configure(text="等待输入...", text_color=TEXT_DIM)

    def _fetch_info(self):
        raw = self.url_entry.get().strip()
        bvid = bd.extract_bvid(raw)
        ep_id = bd.extract_epid(raw)
        ss_id = bd.extract_ssid(raw)

        # 重置旧选择
        self._collection = None
        self._selected_ep_ids = []
        self.select_btn.configure(state="disabled")

        # 课程/番剧 ep 链接
        if ep_id:
            self.info_label.configure(text="获取中...", text_color="#60a5fa")
            self.fetch_btn.configure(state="disabled")
            self.select_btn.configure(state="disabled")
            threading.Thread(target=self._do_fetch_collection, args=(raw,), daemon=True).start()
            return

        # ss 链接：提示用户使用 ep 链接
        if ss_id:
            self.info_label.configure(
                text="检测到整季链接，请输入该季中任意一集的链接（/ep...）",
                text_color="#f87171")
            self.info_icon.configure(text="")
            return

        # 普通视频链接
        if not bvid:
            self.info_label.configure(text="无法识别链接", text_color="#f87171")
            self.info_icon.configure(text="")
            return
        self.info_label.configure(text="获取中...", text_color="#60a5fa")
        self.fetch_btn.configure(state="disabled")
        threading.Thread(target=self._do_fetch, args=(bvid,), daemon=True).start()

    def _do_fetch_collection(self, url):
        try:
            collection = bd.get_media_collection(url)
            media_type = collection["media_type"]
            title = collection["title"]
            n_eps = len(collection["episodes"])
            type_name = "课程" if media_type == "course" else "番剧"
            current_ep_id = collection["current_ep_id"]

            # 定位当前集标题
            current_ep = next(
                (ep for ep in collection["episodes"] if ep["ep_id"] == current_ep_id), None)
            current_title = current_ep["title"] if current_ep else ""

            self._collection = collection
            self._selected_ep_ids = [current_ep_id]
            self._collection_url = url

            text = f"{type_name}：{title} · 共{n_eps}集 · 已选择1集"
            if current_title:
                text += f"\n当前：{current_title}"

            self.after(0, lambda: self.info_label.configure(text=text, text_color=TEXT))
            self.after(0, lambda: self.info_icon.configure(text=""))
            self.after(0, lambda: self.select_btn.configure(state="normal"))
            self._log(f"获取成功: {type_name}「{title}」共{n_eps}集", "ok")
        except Exception as e:
            self.after(0, lambda: self.info_label.configure(
                text=f"获取失败: {e}", text_color="#f87171"))
            self._log(f"获取失败: {e}", "err")
        self.after(0, lambda: self.fetch_btn.configure(state="normal"))

    def _do_fetch(self, bvid):
        try:
            info = bd.get_video_info(bvid)
            pages = info.get("pages", [])
            n = len(pages) if pages else 1
            title = info["title"]
            owner = info["owner"]["name"]
            duration = info["duration"]
            # 格式化时长
            m, s = divmod(duration, 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            text = f"📺 {title}  ·  UP主 {owner}  ·  {dur_str}  ·  {n} 个分P"
            self.after(0, lambda: self.info_label.configure(text=text, text_color=TEXT))
            self.after(0, lambda: self.info_icon.configure(text=""))
            self._log(f"获取成功: {title}", "ok")
        except Exception as e:
            self.after(0, lambda: self.info_label.configure(
                text=f"获取失败: {e}", text_color="#f87171"))
            self._log(f"获取失败: {e}", "err")
        self.after(0, lambda: self.fetch_btn.configure(state="normal"))

    def _browse_out(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d:
            self.out_var.set(d.replace("/", "\\"))

    def _download(self):
        if self._running:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._log("请输入视频链接或BV号", "err")
            return

        out = Path(self.out_var.get().strip() or "downloads")
        quality = self.quality_var.get()
        bvid = bd.extract_bvid(url)
        ep_id = bd.extract_epid(url)

        # 课程/番剧：统一走 download_selected_episodes
        if ep_id and self._collection:
            if not self._selected_ep_ids:
                self._log('未选择任何集数，请先点击"选择集数"', "err")
                return
            # 校验当前 URL 与 collection 一致
            current_ep_id = bd.extract_epid(url)
            if self._collection["current_ep_id"] != current_ep_id:
                self._log('链接已变更，请重新点击"获取信息"', "err")
                return
        elif ep_id and not self._collection:
            self._log('请先点击"获取信息"加载课程/番剧列表', "err")
            return

        self._running = True
        self._stop_event.clear()
        self.dl_btn.configure(text="下载中...", fg_color="#4a4a5a", hover_color="#4a4a5a",
                              state="disabled")
        self.dl_btn.pack_forget()
        self.stop_btn.pack(side="right", padx=(0, 8))
        self.progress_bar.set(0)
        self.pct_label.configure(text="0%")
        self.progress_frame.grid()
        self._clear_log()

        self._log(f"链接: {url}", "info")
        self._log(f"画质: {quality}", "info")
        self._log(f"目录: {out}", "info")
        self._log("开始下载...", "")

        threading.Thread(target=self._do_download, args=(url, out, quality, bvid),
                         daemon=True).start()

    def _progress_cb(self, downloaded, total):
        if total > 0:
            pct = min(downloaded / total * 100, 100)
            self.after(0, lambda: self.progress_bar.set(pct / 100))
            self.after(0, lambda: self.pct_label.configure(text=f"{pct:.0f}%"))

    def _episode_status_cb(self, event):
        status = event["status"]
        ordinal = event["ordinal"]
        total = event["total"]
        title = event["title"]
        idx = event.get("index", "?")

        if status == "started":
            self.after(0, lambda: self._log(
                f"[{ordinal}/{total}] 开始下载：第{idx}集 {title}", "info"))
        elif status == "completed":
            self._batch_done_count += 1
            done = self._batch_done_count
            self.after(0, lambda: self._log(
                f"[{ordinal}/{total}] 下载完成：第{idx}集 {title}", "ok"))
            self.after(0, lambda: self.progress_bar.set(done / total))
            self.after(0, lambda: self.pct_label.configure(text=f"{done}/{total}"))
        elif status == "failed":
            self._batch_done_count += 1
            done = self._batch_done_count
            err = event.get("error", "")
            self.after(0, lambda: self._log(
                f"[{ordinal}/{total}] 下载失败：第{idx}集 {title}：{err}", "err"))
            self.after(0, lambda: self.progress_bar.set(done / total))
            self.after(0, lambda: self.pct_label.configure(text=f"{done}/{total}"))
        elif status == "cancelled":
            pass

    def _stop_download(self):
        self._stop_event.set()
        self._log("正在停止...", "info")
        self.stop_btn.configure(state="disabled")

    def _do_download(self, url, out, quality, bvid):
        ep_id = bd.extract_epid(url)
        summary = None
        failed = False
        try:
            if ep_id and self._collection:
                # 课程/番剧批量下载
                collection = self._collection
                selected_ids = self._selected_ep_ids
                max_workers = int(self.worker_var.get())
                type_name = "课程" if collection["media_type"] == "course" else "番剧"
                n_eps = len(collection["episodes"])
                n_sel = len(selected_ids)
                self._log(f"{type_name}：{collection['title']}", "info")
                self._log(f"共 {n_eps} 集，已选择 {n_sel} 集，并发数 {max_workers}", "info")

                self._batch_done_count = 0
                self._batch_total = n_sel

                summary = bd.download_selected_episodes(
                    collection, selected_ids, out, quality,
                    stop_event=self._stop_event,
                    progress_callback=self._progress_cb,
                    max_workers=max_workers,
                    episode_status_callback=self._episode_status_cb,
                )
            elif bvid:
                ok = bd.download_single(bvid, out, quality,
                                         stop_event=self._stop_event,
                                         progress_callback=self._progress_cb)
                if not ok and not self._stop_event.is_set():
                    failed = True
                if self.cover_v.get():
                    bd.download_cover(bvid, out)
                if self.danmaku_v.get():
                    bd.download_danmaku(bvid, out)
                if self.subtitle_v.get():
                    bd.download_subtitle(bvid, out)
            else:
                bd.download_batch(url, out, quality,
                                  stop_event=self._stop_event,
                                  progress_callback=self._progress_cb)
        except Exception as e:
            failed = True
            self._log(f"下载失败: {e}", "err")

        self.after(0, lambda: self._download_done(summary, failed))

    def _download_done(self, summary=None, failed=False):
        self._running = False
        self.stop_btn.pack_forget()
        self.stop_btn.configure(state="normal")
        self.dl_btn.pack(side="right")
        self.dl_btn.configure(text="开始下载", fg_color=PINK, hover_color="#e8618a",
                              state="normal")

        if summary:
            total = summary["total"]
            success = summary["success"]
            fail = summary["failed"]
            cancelled = summary["cancelled"]
            cancelled_count = summary.get("cancelled_count", 0)
            if cancelled:
                self._log(f"已停止：成功 {success} 集，失败 {fail} 集"
                          f"，取消 {cancelled_count} 集", "err")
            elif fail > 0:
                self._log(f"下载结束：成功 {success} 集，失败 {fail} 集", "err")
                for f_item in summary["failures"]:
                    self._log(f"  ✗ 第{f_item.get('ep_id')}集 "
                              f"{f_item['title']}: {f_item['error']}", "err")
            else:
                self.progress_bar.set(1)
                self.pct_label.configure(text="100%")
                self._log(f"全部完成！共 {total} 集", "ok")
        elif failed:
            self._log("下载未完成，请查看上方错误信息", "err")
        elif not self._stop_event.is_set():
            self.progress_bar.set(1)
            self.pct_label.configure(text="100%")
            self._log("全部完成!", "ok")
        else:
            self._log("已停止", "err")

    def _open_episode_selector(self):
        if not self._collection:
            return
        EpisodeSelectorWindow(
            self, self._collection, self._selected_ep_ids,
            on_confirm=self._on_episode_selected,
        )

    def _on_episode_selected(self, selected_ids):
        self._selected_ep_ids = selected_ids
        n = len(selected_ids)
        type_name = "课程" if self._collection["media_type"] == "course" else "番剧"
        title = self._collection["title"]
        total = len(self._collection["episodes"])
        current_ep = next(
            (ep for ep in self._collection["episodes"] if ep["ep_id"] in selected_ids), None)
        text = f"{type_name}：{title} · 共{total}集 · 已选择{n}集"
        if current_ep and n == 1:
            text += f"\n当前：{current_ep['title']}"
        self.info_label.configure(text=text, text_color=TEXT)
        self._log(f"已选择 {n} 集", "info")

    def on_close(self):
        sys.stdout = self._orig_stdout
        self.destroy()


def main():
    app = BiliApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
