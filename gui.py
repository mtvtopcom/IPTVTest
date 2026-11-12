#!/usr/bin/env python3
"""macOS 桌面端：数据源 / 运行 / 验证源管理 / 设置 四页结构."""
from __future__ import annotations

import copy
import json
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.api import ApiServer  # noqa: E402
from core.config import DEFAULT_SETTINGS, Config  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.checker import check_entry  # noqa: E402
from core.fetcher import decode_bytes  # noqa: E402
from core.models import Entry  # noqa: E402
from core.parser import parse_auto  # noqa: E402
from core.writer import build_channels, render_m3u, render_txt  # noqa: E402

HELP_TEXT = """IPTV直播源测试（简易版）使用说明

页面按使用流程排列：数据源 → 运行 → 验证源管理 → 设置。

1. 数据源
   · 顶部状态栏显示当前生效的「文件源 / 订阅源 / 输入源」数量与条数。
   · 每个模块标题右侧显示条数合计；文件源、订阅源列表里每项后面
     显示该源解析出的条数（文件源实时解析，订阅源为最近一次运行的统计，
     跑完一次更新后显示）。
   · 文件源：点「添加文件…」选本地 m3u/txt；添加、移除、清空后自动保存。
   · 订阅源：点「添加订阅源」在弹框里粘贴地址（每行一个，# 开头为注释），
     可点「粘贴内容」一次性替换、或 ⌘V 逐条追加；⌘↩ 或点蓝色「保存」
     即写入配置并生效，Esc 取消；移除、清空同样自动保存。
   · 输入源：用 ⌘V、右键「粘贴」或点「粘贴内容」按钮（用剪贴板替换整段），
     标题右侧会实时显示「N 行未保存」；必须点「保存输入数据」才生效。
   · 三个模块各自独立保存，互不影响；标题右侧显示各自数量。
   · 支持标准 M3U（tvg-name / group-title / 台标 / 自定义请求头）、
     TXT 的「分组,#genre#」+「频道名,地址」，以及每行一个纯 URL。
   · 完整 M3U 频道列表会整体解析；只有「每行一个纯 URL」的订阅链接列表
     才会被展开抓取（最多 30 个地址）。
   · 只校验 http/https；mitv://、p3p:// 等协议会标为「已过滤」，
     显示在运行结果表格和 CSV 明细里，不会静默丢弃。

2. 运行
   · 顶部「有效源」显示当前生效的文件源 / 订阅源 / 输入源数量，「刷新」重读配置。
   · 「排重更新」只检测「所有数据源管理」里没有的新数据，测过的自动跳过；
     结果文件会把上次成功的源合并写出，不会只剩新增的几条。
   · 「全量更新」把数据源里所有源重新测试一遍（包括测过的），点击后有二次确认。
   · 「停止」中断当前批次；进度、全部检测结果（含失败与已过滤）、日志在此查看。
   · 日志区「清空日志」只清窗口显示，不动磁盘日志；「复制全部」可整段拷出。
   · 流程：采集 → 频道名清洗去重 → 逐条 HTTP 流探测（延迟/速率/内容）
     → ffprobe 深度校验 → 按频道分组排序 → 写出结果。
   · 某次没解析出任何频道时会跳过写入，保留上一版 result 文件，避免被空结果覆盖。

3. 验证结果
   · 只显示验证成功的源：本次运行的实时结果，或本机保存的验证数据（重启后自动载入）。
   · 「打开输出目录」可直达 result 文件所在位置；「重新载入」从上次 result.json 恢复。
   · 搜索：范围可选「全部 / 频道 / 分组 / 地址 / 分辨率 / 深度校验 / 来源」，
     关键词支持空格分隔多条件（同时满足）；「清空搜索」恢复全部。
     导出只基于当前搜索结果，选中行时只导出所选。
   · 点击表头（频道、分组、地址、延迟、速率、分辨率、深度校验、来源、两个时间列）
     可在正向 / 倒序之间切换；分辨率按宽×高数值大小排序（如 1920x1080 > 720x540）。
     列表可横向拖动，鼠标悬停单元格可看完整内容。
   · 列表上方按钮：全选（再点取消全选）、播放、复制、重测、删除；行首「选」列
     可勾选（点「选」表头同样全选/取消）；勾选即选中，按住 Cmd 也可多选。
     播放只支持单条源（选中多条时播放按钮置灰）；重测/删除/复制支持多条。
   · 右键菜单：播放（系统默认播放器）、复制地址、重测（失效自动移除）、删除。
   · 新增「首次验证成功」「最新验证成功」两列，跨多次运行保留。
   · 「清空所有验证数据」需二次确认：可只清空当前搜索结果，也可清空全部；
     清空后会持久保存，重启不会自动恢复，需要时点「重新载入」恢复。
   · 双击行复制地址；「导出 ▾」支持 M3U / TXT，格式与输出目录里的 result 文件一致。

4. 所有数据源管理
   · 汇总安装软件以来导入过的全部数据（按 URL 归并，同一地址只保存一条，
     跨运行累计），分两个子页：「验证成功」显示最新一次验证成功的数据，
     「验证失败」显示失败/已过滤/未验证的。
   · 列包含「验证成功次数」「验证失败次数」（多次运行/重测累加）与最新状态、
     最近验证时间，所有表头点击都可在正向 / 倒序之间切换。
   · 不管重测成功还是失败，数据都不会被清除，只会累加成功次数、失败次数和
     最近验证时间；重测成功后条目会移到「验证成功」子页。
   · 旧版本运行过的历史成功源会从 result.json 自动回填（各计 1 次成功）。
   · 设计与验证结果页一致：搜索（全部/频道/分组/地址/来源/最新状态）、全选、
     播放、复制、重测、删除、行首勾选列、横向滚动与悬浮提示。
   · 数据保存在 config/all_entries.json；只有在这里手动「删除」才会移除记录。

5. 设置
   · 页面顶部「常用操作」里有打开日志目录、打开配置目录、使用说明。
   · 检测参数、深度校验、黑白名单、定时更新、本地 API 都在这里。
   · 黑白名单每格一行一个条目（逗号不是分隔符），# 开头的行当注释忽略；
     排除关键词匹配频道名或地址，排除域名匹配 URL 的主机名，仅保留关键词留空不过滤。
   · 「启动 API / 停止 API」在设置页，启动后显示播放器可订阅的地址（可选中复制）。
   · 接口：/m3u、/txt、/json、/api/status、/health；默认只监听本机。
   · 定时更新需窗口保持打开；修改设置后点「保存设置」。

6. 目录结构（logs 与 output 平级，都在数据目录下）
   output/  result.m3u / result.txt / result.json（含首次/最新成功时间）、*.bak 备份；
   logs/    entries-*.csv（逐条明细）、run-*.log（运行日志）、launcher.log；
   config/  settings.json / sources.json / verified_data.json / all_entries.json。

7. 合规提示
   工具只在本机对你提供的地址做可用性校验，不采集、不转发、不分发任何内容。
   请只使用自己有权限或合法订阅的直播源，勿用于公开传播未授权节目。
"""


def _fmt_entries(value) -> str:
    """条数展示：有值显示「N 条」，未统计显示「待运行统计」."""
    return f"{value} 条" if value is not None else "待运行统计"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IPTV直播源测试")
        self.geometry("1120x840")
        self.minsize(1000, 720)
        self.config_obj = Config()
        self.queue: queue.Queue = queue.Queue()
        self.engine: Engine | None = None
        self.results: list = []
        self.verified: list[dict] = []
        self.verified_rows: dict[str, dict] = {}
        self.verified_times: dict[str, dict] = {}
        self.verify_sort_col: str | None = None
        self.verify_sort_reverse = False
        self._search_job = None
        self._queue_job = None
        self._schedule_job = None
        # 所有数据源管理：安装以来导入过的全部数据的登记簿（按 URL 归并）
        self.all_records: dict[str, dict] = {}
        self.all_rows: dict[str, dict] = {}
        self.all_rows_bad: dict[str, dict] = {}
        self.all_sort_col: str | None = None
        self.all_sort_reverse = False
        self._all_search_job = None
        self._all_dirty = 0
        # 不可用分析：单次运行中按大分类实时统计失败说明
        self._fail_stats: dict[str, int] = {}
        self._fail_total = 0
        # 每个数据源的条数统计：运行后由引擎写入并持久化；本地文件另有实时解析缓存
        self.source_stats: dict = {}
        self._local_count_cache: dict = {}
        self._local_raw: list[str] = []
        self._subs_raw: list[str] = []
        self.last_run_at = datetime.now()
        self.last_daily_date = None
        self.api = ApiServer(self.config_obj, self.log)
        self._init_palette()
        self._build_style()
        self._set_window_icon()
        self._build_menu()
        self._build_tabs()
        self._build_statusbar()
        self._fit_window()
        self._load_source_stats()
        self._load_sources_to_ui()
        self._load_settings_to_ui()
        self._refresh_ffprobe_label()
        self._refresh_source_status()
        self._load_verified_state()
        self._load_all_records()
        self.notebook.select(self.tab_sources)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._queue_job = self.after(120, self._drain_queue)
        self._schedule_job = self.after(30_000, self._schedule_tick)
        self.log("info", f"数据目录：{self.config_obj.home}")
        self.log("info", "就绪。先在「数据源」页添加源，再到「运行」页点「开始更新」。")
        if not self._has_any_source():
            self._note("当前没有生效的数据源，请先添加文件源或订阅源", warn=True)
        if self.config_obj.get("api_enabled"):
            self._toggle_api(start=True)

    # ---------- 界面骨架 ----------
    def _init_palette(self) -> None:
        """跟随 macOS 实时外观选一套配色.

        Tk 9 在深色模式下 tk.Text 的默认前景是白色；若再把背景硬编码成
        白色，就会出现「粘贴进去了却看不见」的白底白字。这里让所有原生
        控件显式配色，两套主题下都保持高对比。
        """
        dark = False
        try:
            rgb = self.winfo_rgb("systemWindowBackgroundColor")
            dark = sum(rgb) / 3 < 32768
        except (tk.TclError, ValueError, TypeError):
            try:
                out = subprocess.run(
                    ["defaults", "read", "-g", "AppleInterfaceStyle"],
                    capture_output=True, text=True, timeout=2,
                )
                dark = out.returncode == 0 and "dark" in out.stdout.lower()
            except (OSError, subprocess.SubprocessError):
                pass
        self.dark = dark
        if dark:
            self.c = {
                "text_bg": "#1c1d1f", "text_fg": "#f4f4f1", "caret": "#ffffff",
                "sel_bg": "#2f6f9e", "sel_fg": "#ffffff",
                "list_bg": "#232426", "list_fg": "#f0f0ec",
                "muted": "#a9a9a4", "ok": "#5ed3a3", "warn": "#ff9b8f",
                "frame_fg": "#e6e6e2", "panel": "systemWindowBackgroundColor",
                "btn_bg": "#4a4c50", "btn_fg": "#ffffff",
                "btn_active": "#5c5f64", "accent_bg": "#2f6f9e",
                "accent_active": "#3a82b8", "accent_fg": "#ffffff",
            }
        else:
            self.c = {
                "text_bg": "#ffffff", "text_fg": "#22221f", "caret": "#111111",
                "sel_bg": "#316ac5", "sel_fg": "#ffffff",
                "list_bg": "#ffffff", "list_fg": "#22221f",
                "muted": "#5f5e5a", "ok": "#0f6e56", "warn": "#a32d2d",
                "frame_fg": "#2c2c2a", "panel": "systemWindowBackgroundColor",
                "btn_bg": "#e4e4e1", "btn_fg": "#1d1d1b",
                "btn_active": "#d3d3cf", "accent_bg": "#2f6f9e",
                "accent_active": "#3a82b8", "accent_fg": "#ffffff",
            }
        # option database 兜底：后续新建的 Text/Listbox 也拿到同一套配色
        self.option_add("*Text.background", self.c["text_bg"])
        self.option_add("*Text.foreground", self.c["text_fg"])
        self.option_add("*Text.insertBackground", self.c["caret"])
        self.option_add("*Text.selectBackground", self.c["sel_bg"])
        self.option_add("*Text.selectForeground", self.c["sel_fg"])
        self.option_add("*Listbox.background", self.c["list_bg"])
        self.option_add("*Listbox.foreground", self.c["list_fg"])
        self.option_add("*Listbox.selectBackground", self.c["sel_bg"])
        self.option_add("*Listbox.selectForeground", self.c["sel_fg"])

    def _text_kwargs(self, **extra) -> dict:
        """所有 tk.Text 统一走这里，绝不再单独硬编码背景色."""
        base = {
            "background": self.c["text_bg"],
            "foreground": self.c["text_fg"],
            "insertbackground": self.c["caret"],
            "selectbackground": self.c["sel_bg"],
            "selectforeground": self.c["sel_fg"],
            "highlightthickness": 1,
            "highlightbackground": self.c["muted"],
            "highlightcolor": self.c["sel_bg"],
            "borderwidth": 0,
            "padx": 8,
            "pady": 6,
            "font": ("Menlo", 12),
            "undo": True,
            "maxundo": -1,
        }
        base.update(extra)
        return base

    def _bind_text_menu(self, widget: tk.Text, on_change=None) -> None:
        """右键菜单 + macOS 粘贴快捷键，确保一定能把内容贴进来."""
        menu = tk.Menu(widget, tearoff=0)

        def paste():
            self._paste_clipboard(widget)
            if on_change:
                on_change()

        menu.add_command(label="粘贴  ⌘V", command=paste)
        menu.add_command(label="全选  ⌘A", command=lambda: widget.tag_add("sel", "1.0", "end"))
        menu.add_command(label="清空", command=lambda: (widget.delete("1.0", "end"), on_change and on_change()))
        widget.bind("<Button-2>", lambda e: (menu.tk_popup(e.x_root, e.y_root), "break")[1])
        widget.bind("<Control-Button-1>", lambda e: (menu.tk_popup(e.x_root, e.y_root), "break")[1])
        widget.bind("<Command-v>", lambda e: (paste(), "break")[1])
        widget.bind("<Control-v>", lambda e: (paste(), "break")[1])
        if on_change:
            widget.bind("<<Paste>>", lambda e: self.after(80, on_change))
            widget.bind("<KeyRelease>", lambda e: on_change())

    def _paste_clipboard(self, widget: tk.Text, replace: bool = False) -> bool:
        try:
            data = self.clipboard_get()
        except tk.TclError:
            self.log("warn", "剪贴板为空或无法读取，请先在别处复制内容")
            return False
        if not data:
            return False
        if replace:
            widget.delete("1.0", "end")
            widget.insert("1.0", data)
            widget.mark_set("insert", "1.0")
        else:
            try:
                widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            widget.insert("insert", data)
        widget.see("insert")
        return True

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("aqua")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(10, 5))
        style.configure("Accent.TButton", padding=(10, 5))
        style.configure("TLabelframe.Label", foreground=self.c["frame_fg"])
        style.configure("Module.TLabel", font=("Helvetica", 13, "bold"))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开输出目录", command=lambda: self._open_dir(self.config_obj.output_dir))
        file_menu.add_command(label="打开日志目录", command=lambda: self._open_dir(self.config_obj.logs_dir))
        file_menu.add_command(label="打开配置目录", command=lambda: self._open_dir(self.config_obj.config_dir))
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self._show_help)
        help_menu.add_command(label="关于", command=lambda: messagebox.showinfo(
            "关于", "IPTV直播源测试 · 简易版\n采集 / 校验 / 测速 / 分组 / M3U·TXT·API 输出"))
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menubar)

    def _export_button(self, parent, label: str) -> ttk.Button:
        """普通按钮 + 弹出菜单，避开 macOS 上原生下拉按钮的兼容问题."""
        button = ttk.Button(parent, text=label)
        menu = tk.Menu(button, tearoff=0)
        menu.add_command(label="导出为 M3U 播放列表…", command=lambda: self._export_verified("m3u"))
        menu.add_command(label="导出为 TXT 文本…", command=lambda: self._export_verified("txt"))

        def show_menu():
            try:
                menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
            finally:
                menu.grab_release()

        button.configure(command=show_menu)
        return button

    def _set_window_icon(self) -> None:
        """命令行直接启动时也尽量用上应用图标（.app 启动由 Info.plist 提供）."""
        icon = Path(__file__).resolve().parent / "assets" / "icon.png"
        if not icon.is_file():
            return
        try:
            self._icon_image = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._icon_image)
        except (tk.TclError, OSError):
            pass

    def _fit_window(self) -> None:
        """按内容实际需要的尺寸开窗，避免底部按钮被压缩到只剩灰块."""
        self.update_idletasks()
        need_w = max(self.winfo_reqwidth() + 28, 1120)
        need_h = max(self.winfo_reqheight() + 28, 800)
        screen_w = max(940, self.winfo_screenwidth() - 80)
        screen_h = max(680, self.winfo_screenheight() - 100)
        width = min(need_w, screen_w)
        height = min(need_h, screen_h)
        self.geometry(f"{width}x{height}+80+60")
        # 最小尺寸同样按内容需求设定，用户再怎么缩小也不会把按钮挤没
        self.minsize(width, height)

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self, padding=(10, 6))
        self.notebook.pack(fill="both", expand=True)
        self.tab_sources = ttk.Frame(self.notebook)
        self.tab_run = ttk.Frame(self.notebook)
        self.tab_verify = ttk.Frame(self.notebook)
        self.tab_all = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_sources, text="  数据源  ")
        self.notebook.add(self.tab_run, text="  单次运行  ")
        self.notebook.add(self.tab_verify, text="  验证结果  ")
        self.notebook.add(self.tab_all, text="  所有数据源管理  ")
        self.notebook.add(self.tab_settings, text="  设置  ")
        self._build_sources_tab()
        self._build_run_tab()
        self._build_verify_tab()
        self._build_all_tab()
        self._build_settings_tab()

    # ---------- 数据源页 ----------
    def _build_sources_tab(self) -> None:
        container = ttk.Frame(self.tab_sources, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(2, weight=1)

        status = ttk.Labelframe(container, padding=(12, 10))
        status.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 10))
        self.lbl_source_counts = ttk.Label(status, text="", font=("Helvetica", 13))
        self.lbl_source_counts.pack(side="left")
        ttk.Button(status, text="刷新", command=self._refresh_source_status).pack(side="right")

        local = ttk.Labelframe(container, padding=8)
        local.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        head = ttk.Frame(local)
        head.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(head, text="文件源", style="Module.TLabel").pack(side="left")
        self.lbl_local_count = ttk.Label(head, text="0 个", foreground=self.c["ok"])
        self.lbl_local_count.pack(side="right")
        btns = ttk.Frame(local, padding=(0, 8, 0, 0))
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text="添加文件…", command=self._add_local_files).pack(side="left")
        ttk.Button(btns, text="移除选中",
                   command=lambda: self._remove_and_save(self.list_local, "local")).pack(side="left", padx=6)
        ttk.Button(btns, text="清空",
                   command=lambda: self._clear_and_save(self.list_local, "local")).pack(side="left")
        body = ttk.Frame(local)
        body.pack(side="top", fill="both", expand=True)
        self.list_local = tk.Listbox(body, height=7, activestyle="dotbox")
        local_sb = ttk.Scrollbar(body, orient="vertical", command=self.list_local.yview)
        self.list_local.configure(yscrollcommand=local_sb.set)
        self.list_local.pack(side="left", fill="both", expand=True)
        local_sb.pack(side="left", fill="y")

        subs = ttk.Labelframe(container, padding=8)
        subs.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        head2 = ttk.Frame(subs)
        head2.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(head2, text="订阅源", style="Module.TLabel").pack(side="left")
        self.lbl_subs_count = ttk.Label(head2, text="0 个", foreground=self.c["ok"])
        self.lbl_subs_count.pack(side="right")
        btns2 = ttk.Frame(subs, padding=(0, 8, 0, 0))
        btns2.pack(side="bottom", fill="x")
        ttk.Button(btns2, text="添加订阅源", style="Accent.TButton",
                   command=self._paste_subscribe).pack(side="left")
        ttk.Button(btns2, text="移除选中",
                   command=lambda: self._remove_and_save(self.list_subs, "sub")).pack(side="left", padx=6)
        ttk.Button(btns2, text="清空",
                   command=lambda: self._clear_and_save(self.list_subs, "sub")).pack(side="left")
        body2 = ttk.Frame(subs)
        body2.pack(side="top", fill="both", expand=True)
        self.list_subs = tk.Listbox(body2, height=7, activestyle="dotbox")
        subs_sb = ttk.Scrollbar(body2, orient="vertical", command=self.list_subs.yview)
        self.list_subs.configure(yscrollcommand=subs_sb.set)
        self.list_subs.pack(side="left", fill="both", expand=True)
        subs_sb.pack(side="left", fill="y")

        raw = ttk.Labelframe(container, padding=8)
        raw.grid(row=2, column=0, columnspan=2, sticky="nsew")
        head3 = ttk.Frame(raw)
        head3.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(head3, text="输入源", style="Module.TLabel").pack(side="left")
        self.lbl_raw_count = ttk.Label(head3, text="0 条已保存", foreground=self.c["ok"])
        self.lbl_raw_count.pack(side="right")
        raw_footer = ttk.Frame(raw, padding=(0, 8, 0, 0))
        raw_footer.pack(side="bottom", fill="x")
        ttk.Button(raw_footer, text="保存输入数据", style="Accent.TButton",
                   command=self._save_raw_sources).pack(side="left")
        ttk.Button(raw_footer, text="粘贴内容", command=self._paste_raw).pack(side="left", padx=(8, 0))
        ttk.Button(raw_footer, text="清空输入", command=self._clear_raw).pack(side="left", padx=(6, 0))
        raw_body = ttk.Frame(raw)
        raw_body.pack(side="top", fill="both", expand=True)
        self.txt_raw = tk.Text(raw_body, **self._text_kwargs(height=7, wrap="none"))
        raw_v = ttk.Scrollbar(raw_body, orient="vertical", command=self.txt_raw.yview)
        raw_h = ttk.Scrollbar(raw_body, orient="horizontal", command=self.txt_raw.xview)
        self.txt_raw.configure(yscrollcommand=raw_v.set, xscrollcommand=raw_h.set)
        self.txt_raw.grid(row=0, column=0, sticky="nsew")
        raw_v.grid(row=0, column=1, sticky="ns")
        raw_h.grid(row=1, column=0, sticky="ew")
        raw_body.rowconfigure(0, weight=1)
        raw_body.columnconfigure(0, weight=1)
        self._bind_text_menu(self.txt_raw, self._raw_input_changed)

        self.lbl_source_hint = ttk.Label(container, text="", foreground=self.c["ok"])
        self.lbl_source_hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # ---------- 通用模块样式（与数据源页一致） ----------
    def _h_scrollbar(self, parent, command) -> ttk.Scrollbar:
        """与主题一致的横向滚动条；列宽固定后 xview 有真实范围，滑块可拖动."""
        return ttk.Scrollbar(parent, orient="horizontal", command=command)

    def _bind_tree_tooltip(self, tree: ttk.Treeview) -> None:
        """鼠标悬停显示单元格完整内容，解决长地址/来源被列宽截断看不全."""
        state = {"win": None, "label": None, "key": None}

        def hide(_event=None):
            if state["win"] is not None:
                state["win"].withdraw()
            state["key"] = None

        def show(event):
            row = tree.identify_row(event.y)
            col = tree.identify_column(event.x)
            if not row or not col or col == "#0":
                hide()
                return
            try:
                value = str(tree.item(row, "values")[int(col[1:]) - 1])
            except (ValueError, IndexError, tk.TclError):
                hide()
                return
            key = (row, col)
            if state["key"] == key:
                return
            state["key"] = key
            if len(value) < 22 or value == "-":
                hide()
                return
            if state["win"] is None:
                win = tk.Toplevel(tree)
                win.overrideredirect(True)
                try:
                    win.attributes("-topmost", True)
                except tk.TclError:
                    pass
                label = tk.Label(
                    win, justify="left", wraplength=620,
                    background=self.c["text_bg"], foreground=self.c["text_fg"],
                    font=("Menlo", 11), padx=8, pady=6, relief="solid", borderwidth=1,
                )
                label.pack()
                state["win"], state["label"] = win, label
            state["label"].configure(text=value)
            x = max(0, min(event.x_root + 16, self.winfo_screenwidth() - 660))
            y = max(0, min(event.y_root + 18, self.winfo_screenheight() - 140))
            state["win"].geometry(f"+{int(x)}+{int(y)}")
            state["win"].deiconify()
            state["win"].lift()

        tree.bind("<Motion>", show, add="+")
        tree.bind("<Leave>", hide, add="+")

    # ---------- 运行页 ----------
    def _build_run_tab(self) -> None:
        outer = ttk.Frame(self.tab_run, padding=10)
        outer.pack(fill="both", expand=True)

        # 顶部「有效源」：只统计已写入配置、真正生效的源
        valid, valid_body, self.lbl_valid_state = self._module(outer, "有效源", "0 个")
        valid.pack(side="top", fill="x", pady=(0, 8))
        self.lbl_valid_counts = ttk.Label(
            valid_body,
            text="文件源 0 个    ·    订阅源 0 个    ·    输入源 0 条",
            font=("Helvetica", 12),
        )
        self.lbl_valid_counts.pack(side="left")
        ttk.Button(valid_body, text="刷新",
                   command=self._refresh_valid_sources).pack(side="right")

        runmod, run_body, self.lbl_run_state = self._module(outer, "单次运行", "就绪")
        runmod.pack(side="top", fill="x", pady=(0, 8))
        # 排重更新在前、全量更新在后；全量更新点击后需二次确认
        self.btn_incr = ttk.Button(run_body, text="排重更新",
                                   command=lambda: self._start_run("incremental"))
        self.btn_incr.pack(side="left")
        self.btn_start = ttk.Button(run_body, text="全量更新", style="Accent.TButton",
                                    command=lambda: self._start_run("full"))
        self.btn_start.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(run_body, text="停止", command=self._stop_run,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.lbl_stats = ttk.Label(run_body, text="尚未运行", font=("Helvetica", 12))
        self.lbl_stats.pack(side="left", padx=(18, 0))
        self.progress = ttk.Progressbar(run_body, mode="determinate", maximum=1)
        self.progress.pack(side="right", fill="x", expand=True, padx=(16, 0))

        # 不可用分析：把检测说明按大分类实时统计，运行时可直观了解失败构成
        ana, ana_body, self.lbl_fail_state = self._module(outer, "不可用分析", "不可用 0 条")
        ana.pack(side="top", fill="x", pady=(0, 8))
        self.lbl_fail_stats = ttk.Label(
            ana_body, text="暂无数据，运行后按分类实时统计",
            font=("Helvetica", 12), wraplength=1180, justify="left",
        )
        self.lbl_fail_stats.pack(side="left", fill="x", expand=True)
        ttk.Button(ana_body, text="清零",
                   command=self._reset_fail_stats).pack(side="right")

        panes = ttk.PanedWindow(outer, orient="vertical")
        panes.pack(side="top", fill="both", expand=True)

        table_frame, table_body, self.lbl_result_state = self._module(
            panes, "检测结果", "0 条")
        columns = ("status", "name", "group", "url", "delay", "speed", "res", "deep", "reason")
        headings = {
            "status": ("状态", 80), "name": ("频道", 150), "group": ("分组", 110),
            "url": ("地址", 420), "delay": ("延迟 ms", 90), "speed": ("速率 KB/s", 105),
            "res": ("分辨率", 105), "deep": ("深度校验", 95), "reason": ("说明", 300),
        }
        # 关闭尺寸传播：固定列宽不再把整个窗口撑到 1800px，横向滚动条仍然可用
        inner = ttk.Frame(table_body, width=960, height=260)
        inner.pack(fill="both", expand=True)
        inner.grid_propagate(False)
        inner.pack_propagate(False)
        self.tree = ttk.Treeview(inner, columns=columns, show="headings", height=11)
        for col, (title, width) in headings.items():
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=width, anchor="w", stretch=False)
        vsb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        hsb = self._h_scrollbar(inner, self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        self.tree.tag_configure("ok", foreground=self.c["ok"])
        self.tree.tag_configure("bad", foreground=self.c["warn"])
        self.tree.bind("<Double-1>", self._copy_selected_url)
        self._bind_tree_tooltip(self.tree)
        panes.add(table_frame, weight=3)

        log_frame, log_body, self.lbl_log_state = self._module(panes, "运行日志", "0 行")
        log_head = ttk.Frame(log_body)
        log_head.pack(fill="x", pady=(0, 4))
        ttk.Button(log_head, text="清空日志", command=self._clear_log).pack(side="right")
        ttk.Button(log_head, text="复制全部", command=self._copy_log).pack(side="right", padx=(0, 6))
        log_text_frame = ttk.Frame(log_body)
        log_text_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_text_frame, **self._text_kwargs(
            height=8, wrap="word", font=("Menlo", 11)))
        log_scroll = ttk.Scrollbar(log_text_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set, state="disabled")
        self.log_text.tag_configure("info", foreground=self.c["text_fg"])
        self.log_text.tag_configure("warn", foreground=self.c["warn"])
        self.log_text.tag_configure("error", foreground=self.c["warn"])
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        panes.add(log_frame, weight=1)

    # ---------- 验证源管理页 ----------
    def _bind_entry_menu(self, entry) -> None:
        """搜索框右键 / ⌘V 粘贴，保证一定能把关键词贴进来."""
        menu = tk.Menu(entry, tearoff=0)

        def paste():
            try:
                data = self.clipboard_get()
            except tk.TclError:
                return
            try:
                entry.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            entry.insert("insert", data)

        menu.add_command(label="粘贴  ⌘V", command=paste)
        menu.add_command(label="全选  ⌘A", command=lambda: entry.select_range(0, "end"))
        entry.bind("<Button-2>", lambda e: (menu.tk_popup(e.x_root, e.y_root), "break")[1])
        entry.bind("<Control-Button-1>",
                   lambda e: (menu.tk_popup(e.x_root, e.y_root), "break")[1])
        entry.bind("<Command-v>", lambda e: (paste(), "break")[1])
        entry.bind("<Control-v>", lambda e: (paste(), "break")[1])

    def _build_verify_tab(self) -> None:
        outer = ttk.Frame(self.tab_verify, padding=10)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="验证结果", style="Module.TLabel").pack(side="left")
        self.lbl_verified_count = ttk.Label(head, text="0 条", foreground=self.c["ok"])
        self.lbl_verified_count.pack(side="left", padx=10)
        self._export_button(head, "导出 ▾").pack(side="right")
        ttk.Button(head, text="重新载入", command=self._reload_verified_from_disk).pack(
            side="right", padx=(0, 8))
        ttk.Button(head, text="打开输出目录",
                   command=lambda: self._open_dir(self.config_obj.output_dir)).pack(
            side="right", padx=(0, 8))

        # 搜索模块：与数据源页一致的大标题 + 右上角绿色命中状态
        search, search_body, self.lbl_search_state = self._module(
            outer, "搜索", "命中 0 / 0 条")
        search.pack(side="top", fill="x", pady=(0, 8))
        self.var_verify_search = tk.StringVar()
        self.var_verify_search.trace_add("write", self._on_verify_search_changed)
        self.var_verify_field = tk.StringVar(value="全部")
        # 右侧按钮先占位，左侧输入框再 expand，避免按钮被挤没
        ttk.Button(search_body, text="清空所有验证数据",
                   command=self._clear_verified_data).pack(side="right")
        ttk.Button(search_body, text="清空搜索",
                   command=self._clear_verify_search).pack(side="right", padx=(0, 8))
        ttk.Label(search_body, text="范围").pack(side="left", padx=(0, 6))
        field_box = ttk.Combobox(
            search_body, textvariable=self.var_verify_field, width=9,
            values=("全部", "频道", "分组", "地址", "分辨率", "深度校验", "来源"),
            state="readonly",
        )
        field_box.pack(side="left")
        field_box.bind(
            "<<ComboboxSelected>>", lambda *_: self._rebuild_verified_tree()
        )
        ttk.Label(search_body, text="关键词").pack(side="left", padx=(12, 8))
        # 原生 tk.Entry 显式配色，避免深色模式下输入框与背景融在一起
        search_entry = tk.Entry(
            search_body, textvariable=self.var_verify_search,
            background=self.c["text_bg"], foreground=self.c["text_fg"],
            insertbackground=self.c["caret"],
            selectbackground=self.c["sel_bg"], selectforeground=self.c["sel_fg"],
            highlightthickness=1, highlightbackground=self.c["muted"],
            highlightcolor=self.c["sel_bg"], relief="flat", borderwidth=0,
            font=("Menlo", 12),
        )
        search_entry.pack(side="left", fill="x", expand=True, ipady=4,
                          padx=(0, 12))
        self._bind_entry_menu(search_entry)
        # KeyRelease 兜底：中文输入法组词期间 StringVar 的 trace 可能不触发
        search_entry.bind("<KeyRelease>", lambda *_: self._on_verify_search_changed())

        # 列表上方操作按钮栏：与右键菜单同一套逻辑
        bar = ttk.Frame(outer)
        bar.pack(side="top", fill="x", pady=(0, 6))
        self.btn_select_all = ttk.Button(
            bar, text="全选", command=self._toggle_select_all_verified)
        self.btn_select_all.pack(side="left")
        self.btn_play_row = ttk.Button(
            bar, text="播放", command=self._play_verified, state="disabled")
        self.btn_play_row.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="复制",
                   command=self._copy_verified_clipboard).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="重测",
                   command=self._retest_verified).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="删除",
                   command=self._delete_verified_rows).pack(side="left", padx=(8, 0))
        self.lbl_selected_state = ttk.Label(bar, text="未选中", foreground=self.c["ok"])
        self.lbl_selected_state.pack(side="right")

        table = ttk.Labelframe(outer, padding=4)
        table.pack(fill="both", expand=True)
        inner = ttk.Frame(table, width=980, height=400)
        inner.pack(fill="both", expand=True)
        inner.grid_propagate(False)
        inner.pack_propagate(False)
        columns = ("sel", "name", "group", "url", "delay", "speed", "res", "deep",
                   "source", "first_at", "last_at")
        self._verify_headings = {
            "sel": ("选", 44), "name": ("频道", 160), "group": ("分组", 120),
            "url": ("地址", 520),
            "delay": ("延迟 ms", 95), "speed": ("速率 KB/s", 110),
            "res": ("分辨率", 110), "deep": ("深度校验", 100), "source": ("来源", 220),
            "first_at": ("首次验证成功", 175), "last_at": ("最新验证成功", 175),
        }
        self.tree_verified = ttk.Treeview(inner, columns=columns, show="headings", height=16)
        for col, (title, width) in self._verify_headings.items():
            # 固定列宽（stretch=False）才会产生真实横向范围，滚动条才能拖动
            anchor = "center" if col == "sel" else "w"
            self.tree_verified.column(col, width=width, minwidth=width,
                                      anchor=anchor, stretch=False)
            if col == "sel":
                # 点「选」表头 = 全选/取消全选
                self.tree_verified.heading(col, text=title,
                                           command=self._toggle_select_all_verified)
            else:
                self.tree_verified.heading(
                    col, text=title, command=lambda c=col: self._sort_verified(c)
                )
        vsb = ttk.Scrollbar(inner, orient="vertical", command=self.tree_verified.yview)
        hsb = self._h_scrollbar(inner, self.tree_verified.xview)
        self.tree_verified.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree_verified.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        self.tree_verified.tag_configure("ok", foreground=self.c["ok"])
        self.tree_verified.bind("<Double-1>", self._copy_verified_url)
        # 点击行首「选」列切换勾选；勾选状态 = 行选中状态
        self.tree_verified.bind("<Button-1>", self._on_verify_click, add="+")
        self.tree_verified.bind("<<TreeviewSelect>>",
                                self._on_verify_selection_changed, add="+")
        # macOS 右键是 Button-2，其它平台是 Button-3；两个都绑
        self.tree_verified.bind("<Button-2>", self._show_verified_menu)
        self.tree_verified.bind("<Button-3>", self._show_verified_menu)
        self._bind_tree_tooltip(self.tree_verified)

        footer = ttk.Frame(outer, padding=(0, 8, 0, 0))
        footer.pack(fill="x")
        self.lbl_verify_hint = ttk.Label(
            footer, text="导出按当前搜索结果；点击表头可正/倒序排序",
            foreground=self.c["ok"],
        )
        self.lbl_verify_hint.pack(side="left")

    # ---------- 设置页 ----------
    def _module(self, parent, title: str, status: str = ""):
        """与数据源页保持一致：模块大标题在左、绿色状态在右，不写灰色说明小字."""
        frame = ttk.Labelframe(parent, padding=8)
        head = ttk.Frame(frame)
        head.pack(side="top", fill="x", pady=(0, 8))
        ttk.Label(head, text=title, style="Module.TLabel").pack(side="left")
        state = ttk.Label(head, text=status, foreground=self.c["ok"])
        state.pack(side="right")
        body = ttk.Frame(frame)
        body.pack(side="top", fill="both", expand=True)
        return frame, body, state

    def _build_settings_tab(self) -> None:
        outer = ttk.Frame(self.tab_settings, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        # 常用操作：原「打开输出目录」已移到验证源管理页，这里换成打开日志目录
        tools, tools_body, self.lbl_ffprobe = self._module(outer, "常用操作")
        tools.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        ttk.Button(tools_body, text="打开日志目录",
                   command=lambda: self._open_dir(self.config_obj.logs_dir)).pack(side="left")
        ttk.Button(tools_body, text="打开配置目录",
                   command=lambda: self._open_dir(self.config_obj.config_dir)).pack(side="left", padx=8)
        ttk.Button(tools_body, text="使用说明", command=self._show_help).pack(side="left")

        left, left_body, self.lbl_param_state = self._module(outer, "检测参数")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        left_body.columnconfigure(1, weight=1)
        self.var_concurrency = tk.StringVar()
        self.var_connect_timeout = tk.StringVar()
        self.var_read_timeout = tk.StringVar()
        self.var_probe_seconds = tk.StringVar()
        self.var_min_speed = tk.StringVar()
        self.var_max_per_channel = tk.StringVar()
        self.var_ua = tk.StringVar()
        rows = [
            ("并发数", self.var_concurrency),
            ("连接超时 (秒)", self.var_connect_timeout),
            ("读取超时 (秒)", self.var_read_timeout),
            ("单源探测时长 (秒)", self.var_probe_seconds),
            ("最小速率 (KB/s)", self.var_min_speed),
            ("每频道保留源数", self.var_max_per_channel),
            ("User-Agent", self.var_ua),
        ]
        for index, (label, var) in enumerate(rows):
            ttk.Label(left_body, text=label).grid(row=index, column=0, sticky="w", pady=5)
            ttk.Entry(left_body, textvariable=var, width=34).grid(
                row=index, column=1, sticky="we", pady=5, padx=6)

        deep, deep_body, self.lbl_deep_state = self._module(outer, "深度校验与过滤")
        deep.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=(8, 0))
        self.var_ffprobe = tk.BooleanVar()
        self.var_ffprobe_strict = tk.BooleanVar()
        self.var_ffprobe_path = tk.StringVar()
        ttk.Checkbutton(deep_body, text="启用 ffprobe 深度校验",
                        variable=self.var_ffprobe).pack(anchor="w")
        ttk.Checkbutton(deep_body, text="严格模式",
                        variable=self.var_ffprobe_strict).pack(anchor="w", pady=(4, 6))
        ttk.Entry(deep_body, textvariable=self.var_ffprobe_path,
                  state="readonly").pack(fill="x", pady=(0, 6))
        ttk.Button(deep_body, text="重新检测 ffprobe",
                   command=self._refresh_ffprobe_label).pack(anchor="w")

        right_top, filters_body, self.lbl_filter_state = self._module(
            outer, "黑白名单 / 去重")
        right_top.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self.var_dedupe = tk.BooleanVar()
        ttk.Checkbutton(filters_body, text="按 URL 全局去重",
                        variable=self.var_dedupe).pack(anchor="w", pady=(0, 6))
        for caption, attr in (
            ("排除关键词（一行一个，# 开头的行忽略）", "txt_black_kw"),
            ("排除域名（一行一个，如 example.com）", "txt_black_host"),
            ("仅保留关键词（一行一个，留空不过滤）", "txt_white_kw"),
        ):
            ttk.Label(filters_body, text=caption).pack(anchor="w", pady=(6, 0))
            widget = tk.Text(filters_body, **self._text_kwargs(
                height=3, wrap="word", font=("Menlo", 11)))
            widget.pack(fill="x", pady=(2, 0))
            self._bind_text_menu(widget)
            setattr(self, attr, widget)

        right_bottom, api_body, self.lbl_api_state = self._module(
            outer, "本地 API 与定时更新", "未启动")
        right_bottom.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=(8, 0))
        self.lbl_api_state.configure(foreground=self.c["warn"])
        self.var_api_enabled = tk.BooleanVar()
        self.var_api_host = tk.StringVar()
        self.var_api_port = tk.StringVar()
        self.var_api_url = tk.StringVar()
        self.var_schedule = tk.BooleanVar()
        self.var_schedule_mode = tk.StringVar()
        self.var_schedule_hours = tk.StringVar()
        self.var_schedule_time = tk.StringVar()
        ttk.Checkbutton(api_body, text="启动程序时自动开启 API",
                        variable=self.var_api_enabled).pack(anchor="w", pady=(0, 6))
        self.btn_api = ttk.Button(api_body, text="启动 API", command=self._toggle_api)
        self.btn_api.pack(anchor="w", pady=(0, 6))
        self.var_api_url.set(self.api.display_url)
        ttk.Entry(api_body, textvariable=self.var_api_url, state="readonly").pack(
            fill="x", pady=(0, 6))
        line = ttk.Frame(api_body)
        line.pack(fill="x", pady=2)
        ttk.Label(line, text="监听主机").pack(side="left")
        ttk.Combobox(line, textvariable=self.var_api_host, width=14,
                     values=["127.0.0.1", "0.0.0.0"], state="readonly").pack(side="left", padx=6)
        ttk.Label(line, text="端口").pack(side="left", padx=(10, 0))
        ttk.Entry(line, textvariable=self.var_api_port, width=8).pack(side="left", padx=6)
        ttk.Checkbutton(api_body, text="启用定时自动更新",
                        variable=self.var_schedule).pack(anchor="w", pady=(8, 4))
        line2 = ttk.Frame(api_body)
        line2.pack(fill="x", pady=2)
        ttk.Label(line2, text="模式").pack(side="left")
        ttk.Combobox(line2, textvariable=self.var_schedule_mode, width=10,
                     values=["interval", "daily"], state="readonly").pack(side="left", padx=6)
        ttk.Label(line2, text="间隔小时").pack(side="left", padx=(8, 0))
        ttk.Entry(line2, textvariable=self.var_schedule_hours, width=6).pack(side="left", padx=4)
        ttk.Label(line2, text="或每天").pack(side="left", padx=(8, 0))
        ttk.Entry(line2, textvariable=self.var_schedule_time, width=8).pack(side="left", padx=4)

        footer = ttk.Frame(outer, padding=(10, 8))
        footer.grid(row=3, column=0, columnspan=2, sticky="we")
        ttk.Button(footer, text="保存设置", style="Accent.TButton",
                   command=self._save_settings).pack(side="left")
        ttk.Button(footer, text="恢复默认", command=self._restore_defaults).pack(side="left", padx=8)
        self.lbl_settings_hint = ttk.Label(footer, text="", foreground=self.c["ok"])
        self.lbl_settings_hint.pack(side="right")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(12, 4, 12, 8))
        bar.pack(fill="x")
        self.lbl_status = ttk.Label(bar, text="", foreground=self.c["muted"])
        self.lbl_status.pack(side="left")
        ttk.Label(bar, text="请仅使用合法授权的源",
                  foreground=self.c["muted"]).pack(side="right")
        self._refresh_statusbar()

    # ---------- 日志与队列 ----------
    def log(self, level: str, message: str) -> None:
        self.queue.put(("log", level, message))

    def _append_log(self, level: str, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n", level)
        lines = int(self.log_text.index("end").split(".")[0])
        if lines > 600:
            self.log_text.delete("1.0", "150.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.lbl_log_state.configure(
            text=f"{max(0, int(self.log_text.index('end').split('.')[0]) - 1)} 行",
            foreground=self.c["ok"],
        )

    def _clear_log(self) -> None:
        """清空窗口里的日志显示（磁盘日志文件保持不动）."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.lbl_log_state.configure(text="0 行", foreground=self.c["ok"])

    def _copy_log(self) -> None:
        text = self.log_text.get("1.0", "end").rstrip()
        if not text:
            self.log("warn", "日志为空，没有可复制的内容")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log("info", f"已复制 {len(text.splitlines())} 行日志到剪贴板")

    def _on_progress(self, done: int, total: int, ok: int, failed: int, label: str) -> None:
        self.queue.put(("progress", done, total, ok, failed, label))

    # ---------- 不可用分析（说明大分类实时统计） ----------
    @staticmethod
    def _classify_reason(text: str) -> str:
        """把失败说明归入大分类，便于一眼看出失败构成."""
        raw = (text or "").strip()
        if not raw or raw == "-":
            return "未给出说明"
        if raw.startswith("[已过滤]"):
            return "规则过滤"
        low = raw.casefold()
        if raw.startswith("HTTP "):
            return "HTTP 错误（如 404/403/超5xx）"
        if ("深度校验" in low or "ffprobe" in low):
            return "深度校验失败"
        if "超时" in low or "timed out" in low or "timeout" in low:
            return "连接或读取超时"
        if ("连接" in low or "connection" in low
                or "ssl" in low or "certificate" in low
                or "getaddrinfo" in low or "name or service not known" in low
                or "temporary failure" in low or "network" in low
                or "broken pipe" in low or "reset by peer" in low
                or "disconnected" in low or "refused" in low):
            return "连接失败 / 被断开"
        if ("无效" in low or "分片" in low or "格式" in low or "空" in low
                or "过少" in low or "网页" in low or "错误内容" in low
                or "非媒体" in low or "invalid" in low):
            return "格式 / 内容无效"
        if "检测异常" in low or "error" in low or "exception" in low:
            return "检测异常"
        return "其他"

    def _reset_fail_stats(self) -> None:
        self._fail_stats.clear()
        self._fail_total = 0
        self.lbl_fail_state.configure(text="不可用 0 条")
        self.lbl_fail_stats.configure(text="暂无数据，运行后按分类实时统计")

    def _update_fail_stats(self, reason_text: str) -> None:
        """检测结果里每出现一条不可用/已过滤说明，就累加对应大分类并刷新显示."""
        cat = self._classify_reason(reason_text)
        self._fail_stats[cat] = self._fail_stats.get(cat, 0) + 1
        self._fail_total += 1
        parts = [f"{name} {count} 条"
                 for name, count in sorted(self._fail_stats.items(),
                                           key=lambda kv: (-kv[1], kv[0]))]
        self.lbl_fail_state.configure(text=f"不可用 {self._fail_total} 条")
        self.lbl_fail_stats.configure(
            text="    ·    ".join(parts) if parts else "暂无数据",
            foreground=self.c["text_fg"],
        )

    def _on_entry(self, result) -> None:
        self.queue.put(("entry", result))

    def _drain_queue(self) -> None:
        try:
            for _ in range(400):
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2])
                elif kind == "progress":
                    _, done, total, ok, failed, label = item
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.lbl_stats.configure(
                        text=f"进度 {done}/{total}   可用 {ok}   不可用 {failed}   当前：{label}"
                    )
                    self.lbl_result_state.configure(
                        text=f"{done}/{total} 条", foreground=self.c["ok"]
                    )
                elif kind == "entry":
                    self._insert_result(item[1])
                elif kind == "finish":
                    self._on_finish(item[1])
                elif kind == "retest_done":
                    self._rebuild_verified_tree()
                    self._save_verified_state()
                    self.lbl_verify_hint.configure(
                        text="重测完成，列表已按最新结果刷新", foreground=self.c["ok"]
                    )
                elif kind == "all_result":
                    result = item[1]
                    self._record_check(result.entry, ok=bool(result.ok),
                                       reason=result.reason or "")
                elif kind == "all_done":
                    self._rebuild_all_trees()
                    self._save_all_records()
                    self.log("info", "重测完成，所有数据源管理页已按最新结果刷新")
        except queue.Empty:
            pass
        self._queue_job = self.after(120, self._drain_queue)

    def _insert_result(self, result) -> None:
        self.results.append(result)
        entry = result.entry
        filtered = result.reason.startswith("[已过滤]")
        status = ("已过滤" if filtered
                  else "可用" if result.ok else "不可用")
        # 不可用分析：非可用结果实时累加大分类统计
        if not result.ok:
            self._update_fail_stats(result.note or result.reason or "-")
        # 登记/更新到「所有数据源管理」（安装以来导入过的全部数据）
        self._record_check(entry, ok=bool(result.ok),
                           reason=result.reason or "", filtered=filtered)
        self.tree.insert(
            "", "end",
            values=(
                status,
                entry.name,
                entry.group or "-",
                entry.url,
                f"{result.delay_ms:.0f}" if result.delay_ms else "-",
                f"{result.speed_kbs:.1f}" if result.speed_kbs else "-",
                result.resolution or "-",
                "通过" if result.deep_ok else ("未完成" if result.note else "-"),
                result.note or result.reason or "-",
            ),
            tags=("ok" if result.ok else "bad",),
        )
        if result.ok:
            self._add_verified({
                "name": entry.name,
                "group": entry.group,
                "logo": entry.logo,
                "url": entry.url,
                "headers": dict(entry.headers or {}),
                "delay_ms": result.delay_ms,
                "speed_kbs": result.speed_kbs,
                "resolution": result.resolution,
                "deep_ok": result.deep_ok,
                "source": entry.source,
            })

    # ---------- 验证源管理 ----------
    def _verified_state_path(self) -> Path:
        return self.config_obj.config_dir / "verified_data.json"

    def _load_times_from_result(self) -> dict:
        """从上次 result.json 取回每个地址首次/最新验证成功时间."""
        times: dict = {}
        try:
            data = json.loads(
                (self.config_obj.output_dir / "result.json").read_text(encoding="utf-8")
            )
            for channel in data.get("channels", []) or []:
                for source in channel.get("sources", []) or []:
                    url = source.get("url") or ""
                    first = source.get("first_ok_at") or source.get("last_ok_at")
                    last = source.get("last_ok_at") or first
                    if url and first:
                        times[url] = {"first": first, "last": last}
        except Exception:
            pass
        return times

    def _normalize_verified(self, record: dict, from_disk: bool = False) -> dict:
        """补齐首次验证成功时间 / 最新验证成功时间."""
        rec = dict(record)
        url = rec.get("url") or ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if from_disk:
            first = rec.get("first_ok_at") or rec.get("last_ok_at") or ""
            last = rec.get("last_ok_at") or first
        else:
            prev = self.verified_times.get(url) or {}
            first = prev.get("first") or now
            last = now
        rec["first_ok_at"] = first
        rec["last_ok_at"] = last
        if first:
            self.verified_times[url] = {"first": first, "last": last}
        return rec

    def _verified_values(self, record: dict) -> tuple:
        return (
            record.get("name") or "-",
            record.get("group") or "-",
            record.get("url") or "-",
            f"{record.get('delay_ms', 0):.0f}" if record.get("delay_ms") else "-",
            f"{record.get('speed_kbs', 0):.1f}" if record.get("speed_kbs") else "-",
            record.get("resolution") or "-",
            "通过" if record.get("deep_ok") else "未完成",
            record.get("source") or "-",
            record.get("first_ok_at") or "-",
            record.get("last_ok_at") or "-",
        )

    def _insert_verified_row(self, record: dict) -> str:
        iid = self.tree_verified.insert(
            "", "end", values=("☐",) + self._verified_values(record), tags=("ok",)
        )
        self.verified_rows[iid] = record
        return iid

    def _add_verified(self, record: dict) -> None:
        """运行中实时新增一条可用源；结束后会统一重排一次."""
        rec = self._normalize_verified(record)
        self.verified.append(rec)
        if self._verify_matches(rec, self._verify_terms()):
            self._insert_verified_row(rec)
        self._update_verify_count(len(self.tree_verified.get_children()))

    def _clear_verified(self, persist: bool = False) -> None:
        self.verified.clear()
        self.verified_rows.clear()
        self.tree_verified.delete(*self.tree_verified.get_children())
        self._update_verify_count(0)
        if persist:
            self._save_verified_state()

    def _set_verified_records(self, records: list[dict], hint: str) -> None:
        self.verified = [self._normalize_verified(r, from_disk=True) for r in records]
        self._rebuild_verified_tree()
        self.lbl_verify_hint.configure(text=hint, foreground=self.c["ok"])

    def _save_verified_state(self) -> None:
        """把当前验证数据（含清空后的空列表）持久保存到 config/verified_data.json."""
        try:
            self._verified_state_path().write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "records": self.verified,
                    },
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            self.log("warn", f"保存验证数据状态失败：{exc}")

    def _load_verified_state(self) -> None:
        """启动时优先读本机状态；没有状态文件才回落到上次 result.json."""
        self.verified_times = self._load_times_from_result()
        state = self._verified_state_path()
        result_path = self.config_obj.output_dir / "result.json"
        if state.is_file():
            try:
                # 命令行或外部新跑出的 result.json 比状态文件新时，以最新结果为准
                if (result_path.is_file()
                        and result_path.stat().st_mtime > state.stat().st_mtime + 1):
                    self._reload_verified_from_disk()
                    return
                data = json.loads(state.read_text(encoding="utf-8"))
                self._set_verified_records(
                    data.get("records") or [],
                    f"已载入本机验证数据（{data.get('updated_at') or '时间未知'}）",
                )
                return
            except Exception as exc:
                self.log("warn", f"读取本机验证数据失败，改读上次结果：{exc}")
        self._reload_verified_from_disk()

    def _reload_verified_from_disk(self) -> None:
        """「重新载入」：从上次运行的 result.json 恢复全部成功源."""
        path = self.config_obj.output_dir / "result.json"
        if not path.is_file():
            self._clear_verified()
            self.lbl_verify_hint.configure(
                text="暂无历史结果，运行一次更新后自动填充", foreground=self.c["warn"]
            )
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            updated_at = data.get("updated_at") or ""
            records: list[dict] = []
            for channel in data.get("channels", []) or []:
                for source in channel.get("sources", []) or []:
                    if source.get("ok") is False:
                        continue
                    records.append({
                        "name": channel.get("name") or "",
                        "group": channel.get("group") or "",
                        "logo": channel.get("logo") or "",
                        "url": source.get("url") or "",
                        "headers": source.get("headers") or {},
                        "delay_ms": source.get("delay_ms") or 0,
                        "speed_kbs": source.get("speed_kbs") or 0,
                        "resolution": source.get("resolution") or "",
                        "deep_ok": bool(source.get("deep_verified")),
                        "source": source.get("source") or "",
                        "first_ok_at": source.get("first_ok_at") or updated_at,
                        "last_ok_at": source.get("last_ok_at") or updated_at,
                    })
            self.verified_times = self._load_times_from_result()
            self._set_verified_records(
                records, f"已载入上次结果（{updated_at or '时间未知'}）"
            )
            self._save_verified_state()
            self._sync_all_from_result()
        except Exception as exc:
            self.log("warn", f"读取上次验证结果失败：{exc}")
            self.lbl_verify_hint.configure(
                text="读取历史结果失败", foreground=self.c["warn"]
            )

    # ---------- 搜索 / 排序 ----------
    # 搜索范围下拉框对应的记录字段；None 表示全部字段
    VERIFY_FIELD_KEYS = {
        "全部": None,
        "频道": ("name",),
        "分组": ("group",),
        "地址": ("url",),
        "分辨率": ("resolution",),
        "深度校验": ("deep",),
        "来源": ("source",),
    }

    def _verify_field(self) -> str:
        getter = getattr(self, "var_verify_field", None)
        return getter.get() if getter else "全部"

    def _verify_terms(self) -> list[str]:
        return (self.var_verify_search.get() or "").strip().casefold().split()

    def _verify_matches(self, record: dict, terms: list[str]) -> bool:
        if not terms:
            return True
        keys = self.VERIFY_FIELD_KEYS.get(self._verify_field()) or (
            "name", "group", "url", "resolution", "deep", "source"
        )
        values = []
        for key in keys:
            if key == "deep":
                values.append("通过" if record.get("deep_ok") else "未完成")
            else:
                values.append(str(record.get(key) or ""))
        haystack = " ".join(values).casefold()
        return all(term in haystack for term in terms)

    def _on_verify_search_changed(self, *_args) -> None:
        if self._search_job:
            try:
                self.after_cancel(self._search_job)
            except (tk.TclError, ValueError):
                pass
        self._search_job = self.after(160, self._rebuild_verified_tree)

    def _clear_verify_search(self) -> None:
        self.var_verify_search.set("")
        self._rebuild_verified_tree()

    @staticmethod
    def _resolution_key(text) -> tuple[int, int]:
        """分辨率按宽x高数值排序（1920x1080 > 1280x720 > 720x540），无值排最后由 reverse 控制."""
        match = re.match(r"\s*(\d+)\s*[xX×*]\s*(\d+)", str(text or ""))
        if match:
            return (int(match.group(1)), int(match.group(2)))
        return (0, 0)

    def _verified_sort_value(self, record: dict, col: str):
        if col == "delay":
            return float(record.get("delay_ms") or 0)
        if col == "speed":
            return float(record.get("speed_kbs") or 0)
        if col == "res":
            return self._resolution_key(record.get("resolution"))
        if col == "deep":
            return 1 if record.get("deep_ok") else 0
        if col == "first_at":
            return str(record.get("first_ok_at") or "")
        if col == "last_at":
            return str(record.get("last_ok_at") or "")
        return str(record.get(col) or "").casefold()

    def _sort_verified(self, col: str) -> None:
        if self.verify_sort_col == col:
            self.verify_sort_reverse = not self.verify_sort_reverse
        else:
            self.verify_sort_col = col
            self.verify_sort_reverse = False
        self._rebuild_verified_tree()

    def _update_heading_arrows(self) -> None:
        for col, (title, _width) in self._verify_headings.items():
            arrow = ""
            if col == self.verify_sort_col:
                arrow = " ▼" if self.verify_sort_reverse else " ▲"
            self.tree_verified.heading(col, text=title + arrow)

    def _rebuild_verified_tree(self) -> None:
        """按当前搜索词重建表格，并应用排序；导出始终以这批可见行为准."""
        if self._search_job:
            try:
                self.after_cancel(self._search_job)
            except (tk.TclError, ValueError):
                pass
        self._search_job = None
        terms = self._verify_terms()
        records = [r for r in self.verified if self._verify_matches(r, terms)]
        if self.verify_sort_col:
            try:
                records.sort(
                    key=lambda r: self._verified_sort_value(r, self.verify_sort_col),
                    reverse=self.verify_sort_reverse,
                )
            except (TypeError, ValueError):
                pass
        selected_urls = {
            self.verified_rows[i].get("url")
            for i in self.tree_verified.selection() if i in self.verified_rows
        }
        self.tree_verified.delete(*self.tree_verified.get_children())
        self.verified_rows.clear()
        for record in records:
            iid = self._insert_verified_row(record)
            if record.get("url") in selected_urls:
                self.tree_verified.selection_add(iid)
        self._update_heading_arrows()
        self._update_verify_count(len(records))
        self._on_verify_selection_changed()

    def _update_verify_count(self, visible: int) -> None:
        total = len(self.verified)
        label = f"{visible} 条" if visible == total else f"{visible} / {total} 条"
        self.lbl_verified_count.configure(text=label, foreground=self.c["ok"])
        self.lbl_search_state.configure(
            text=f"命中 {visible} / {total} 条", foreground=self.c["ok"]
        )

    # ---------- 清空验证数据（二次确认） ----------
    def _clear_verified_data(self) -> None:
        total = len(self.verified)
        if total == 0:
            messagebox.showinfo("清空验证数据", "当前没有可清空的验证数据。")
            return
        visible = [
            self.verified_rows[i] for i in self.tree_verified.get_children()
            if i in self.verified_rows
        ]
        filtered = len(visible) < total
        panel = self.c["panel"]

        dialog = tk.Toplevel(self)
        dialog.title("清空验证数据")
        dialog.geometry("620x235")
        dialog.minsize(540, 215)
        dialog.transient(self)
        dialog.configure(background=panel)

        header = tk.Frame(dialog, background=panel)
        header.pack(fill="x", padx=18, pady=(18, 8))
        tk.Label(header, text="确定要清空验证数据吗？", background=panel,
                 foreground=self.c["text_fg"],
                 font=("Helvetica", 14, "bold")).pack(side="left")

        info = tk.Frame(dialog, background=panel)
        info.pack(fill="both", expand=True, padx=18, pady=(0, 6))
        for line in (
            f"当前搜索结果 {len(visible)} 条，全部验证数据 {total} 条。",
            "清空后会同步保存到本机，重启不会自动恢复。",
            "如需恢复，可点「重新载入」从上次运行结果载入。",
        ):
            tk.Label(info, text=line, background=panel, foreground=self.c["text_fg"],
                     anchor="w", justify="left",
                     font=("Helvetica", 12)).pack(fill="x", pady=2)

        # 底部按钮区先占位，保证窗口再小也能完整看到
        footer = tk.Frame(dialog, background=panel)
        footer.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def close() -> None:
            dialog.grab_release()
            dialog.destroy()

        def apply_clear(scope: str) -> None:
            if scope == "all":
                removed = total
                self.verified.clear()
            else:
                removed = len(visible)
                remove_ids = {id(r) for r in visible}
                self.verified = [r for r in self.verified if id(r) not in remove_ids]
            self.var_verify_search.set("")
            self._rebuild_verified_tree()
            self._save_verified_state()
            message = f"已清空 {removed} 条验证数据"
            self.lbl_verify_hint.configure(text=message, foreground=self.c["ok"])
            self.log("info", message)
            close()

        self._flat_button(footer, "取消", close).pack(side="right")
        self._flat_button(footer, f"清空全部（{total} 条）",
                          lambda: apply_clear("all")).pack(side="right", padx=(0, 10))
        if filtered:
            self._flat_button(footer, f"仅清空当前结果（{len(visible)} 条）",
                              lambda: apply_clear("visible"), accent=True).pack(
                side="right", padx=(0, 10))
        else:
            self._flat_button(footer, "确定清空", lambda: apply_clear("all"),
                              accent=True).pack(side="right", padx=(0, 10))
        dialog.bind("<Escape>", lambda _e: close())
        dialog.grab_set()

    def _copy_verified_url(self, event) -> None:
        row = self.tree_verified.identify_row(event.y)
        if not row:
            return
        values = self.tree_verified.item(row, "values")
        if len(values) > 2:
            self.clipboard_clear()
            self.clipboard_append(values[2])
            self.log("info", f"已复制地址：{values[2]}")

    # ---------- 验证源右键菜单：播放 / 复制 / 重测 / 删除 ----------
    def _selected_verified_records(self) -> list[dict]:
        return [self.verified_rows[i] for i in self.tree_verified.selection()
                if i in self.verified_rows]

    def _on_verify_click(self, event) -> str | None:
        """点击行首「选」列 = 勾选/取消该行；勾选即行选中."""
        if self.tree_verified.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree_verified.identify_column(event.x) != "#1":
            return None
        row = self.tree_verified.identify_row(event.y)
        if not row:
            return None
        if row in self.tree_verified.selection():
            self.tree_verified.selection_remove(row)
        else:
            self.tree_verified.selection_add(row)
        return "break"

    def _on_verify_selection_changed(self, *_args) -> None:
        self._sync_check_column()
        rows = self.tree_verified.get_children()
        selected = self.tree_verified.selection()
        if getattr(self, "btn_play_row", None):
            self.btn_play_row.configure(
                state="normal" if len(selected) == 1 else "disabled")
        if getattr(self, "lbl_selected_state", None):
            self.lbl_selected_state.configure(
                text=f"已选 {len(selected)} 条" if selected else "未选中")
        if getattr(self, "btn_select_all", None):
            self.btn_select_all.configure(
                text="取消全选" if rows and len(selected) >= len(rows) else "全选")

    def _sync_check_column(self) -> None:
        """行首勾选框与行选中状态保持一致（☑/☐）."""
        selected = set(self.tree_verified.selection())
        for iid in self.tree_verified.get_children():
            mark = "☑" if iid in selected else "☐"
            if self.tree_verified.set(iid, "sel") != mark:
                self.tree_verified.set(iid, "sel", mark)

    def _toggle_select_all_verified(self) -> None:
        """全选/取消全选（只作用于当前搜索结果里的可见行）."""
        rows = self.tree_verified.get_children()
        if not rows:
            return
        if len(self.tree_verified.selection()) >= len(rows):
            self.tree_verified.selection_remove(self.tree_verified.selection())
        else:
            self.tree_verified.selection_set(rows)

    def _show_verified_menu(self, event) -> None:
        row = self.tree_verified.identify_row(event.y)
        if row and row not in self.tree_verified.selection():
            self.tree_verified.selection_set(row)
        if not self.tree_verified.selection():
            return
        menu = tk.Menu(
            self, tearoff=0,
            background=self.c["panel"], foreground=self.c["text_fg"],
            activebackground=self.c["sel_bg"], activeforeground=self.c["sel_fg"],
            relief="flat", borderwidth=1, font=("Helvetica", 13),
        )
        menu.add_command(label="播放", command=self._play_verified)
        menu.add_command(label="复制", command=self._copy_verified_clipboard)
        menu.add_command(label="重测", command=self._retest_verified)
        menu.add_separator()
        menu.add_command(label="删除", command=self._delete_verified_rows)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_verified_clipboard(self) -> None:
        records = self._selected_verified_records()
        urls = [r.get("url") or "" for r in records if r.get("url")]
        if not urls:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(urls))
        self.log("info", f"已复制 {len(urls)} 条地址")

    def _play_verified(self) -> None:
        records = self._selected_verified_records()
        if not records:
            return
        if len(records) > 1:
            self.log("warn", "播放一次只支持一条源；多条源请用「复制」或「重测」")
            self.lbl_verify_hint.configure(
                text="播放仅支持单条源，请减少选中数量", foreground=self.c["warn"]
            )
            return
        url = records[0].get("url") or ""
        if not url:
            return
        try:
            subprocess.Popen(["open", url])
            self.log("info", f"已调用系统默认播放器打开：{records[0].get('name') or url}")
        except Exception as exc:
            self.log("warn", f"播放失败：{exc}")

    def _delete_verified_rows(self) -> None:
        records = self._selected_verified_records()
        if not records:
            return
        remove_ids = {id(r) for r in records}
        self.verified = [r for r in self.verified if id(r) not in remove_ids]
        self._rebuild_verified_tree()
        self._save_verified_state()
        message = f"已删除 {len(records)} 条验证数据（可点「重新载入」从 result.json 恢复）"
        self.lbl_verify_hint.configure(text=message, foreground=self.c["ok"])
        self.log("info", message)

    def _retest_verified(self) -> None:
        """右键「重测」：对选中源逐条重新校验；仍可用则刷新数据，失效则移除."""
        records = self._selected_verified_records()
        if not records:
            return
        settings = self.config_obj.settings
        ffprobe_bin = (self.config_obj.ffprobe_bin()
                       if settings.get("ffprobe_enabled") else "")
        self.log("info", f"开始重测 {len(records)} 条源…")

        def worker():
            for rec in records:
                entry = Entry(
                    name=rec.get("name") or "", url=rec.get("url") or "",
                    group=rec.get("group") or "",
                    source=rec.get("source") or "retest",
                    logo=rec.get("logo") or "",
                    headers=rec.get("headers") or {},
                )
                try:
                    result = check_entry(entry, settings, ffprobe_bin)
                except Exception as exc:
                    self.queue.put(("log", "warn", f"重测出错 {entry.url}：{exc}"))
                    continue
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if result.ok:
                    rec.update({
                        "delay_ms": result.delay_ms,
                        "speed_kbs": result.speed_kbs,
                        "resolution": result.resolution or "",
                        "deep_ok": bool(result.deep_ok),
                        "last_ok_at": now,
                    })
                    self.verified_times[entry.url] = {
                        "first": rec.get("first_ok_at") or now,
                        "last": now,
                    }
                    # 同步登记到「所有数据源管理」的成功次数
                    self.queue.put(("all_result", result))
                    self.queue.put(("log", "info",
                                    f"重测可用：{entry.name or entry.url}"
                                    f"（延迟 {result.delay_ms:.0f}ms）"))
                else:
                    self.verified = [r for r in self.verified if r is not rec]
                    # 失败次数与最新状态同样登记
                    self.queue.put(("all_result", result))
                    self.queue.put(("log", "warn",
                                    f"重测不可用，已从列表移除：{entry.name or entry.url}"
                                    f"（{result.reason or '无数据'}）"))
            self.queue.put(("retest_done",))

        threading.Thread(target=worker, daemon=True).start()

    def _selected_verified_or_all(self) -> list[dict]:
        """导出只基于当前搜索结果；有选中行时导出所选."""
        selected = self.tree_verified.selection()
        if selected:
            return [self.verified_rows[i] for i in selected if i in self.verified_rows]
        return [
            self.verified_rows[i] for i in self.tree_verified.get_children()
            if i in self.verified_rows
        ]

    def _render_verified(self, records: list[dict], fmt: str) -> str:
        """复用 writer 的 m3u/txt 渲染，保证与 result 文件格式一致."""
        views = []
        for item in records:
            entry = SimpleNamespace(
                name=item.get("name") or "未命名频道",
                url=item.get("url") or "",
                group=item.get("group") or "",
                logo=item.get("logo") or "",
                headers=item.get("headers") or {},
                key="",
                source=item.get("source") or "",
            )
            views.append(SimpleNamespace(
                entry=entry,
                ok=True,
                deep_ok=bool(item.get("deep_ok")),
                speed_kbs=float(item.get("speed_kbs") or 0),
                delay_ms=float(item.get("delay_ms") or 0),
            ))
        channels = build_channels(views, {"min_speed_kbs": 0, "max_per_channel": 0})
        if fmt == "m3u":
            return render_m3u(channels)
        return render_txt(channels, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _export_verified(self, fmt: str) -> None:
        records = self._selected_verified_or_all()
        if not records:
            hint = ("当前搜索结果为空，没有可导出的可用源" if self._verify_terms()
                    else "没有可导出的可用源")
            self.lbl_verify_hint.configure(text=hint, foreground=self.c["warn"])
            self.log("warn", f"导出取消：{hint}")
            return
        ext = ".m3u" if fmt == "m3u" else ".txt"
        default_name = f"iptv-verified-{datetime.now():%Y%m%d-%H%M%S}{ext}"
        path = filedialog.asksaveasfilename(
            title=f"导出验证成功的源为 {ext.upper()}",
            defaultextension=ext,
            initialfile=default_name,
            initialdir=str(self.config_obj.output_dir),
            filetypes=[("M3U 播放列表" if fmt == "m3u" else "TXT 文本", f"*{ext}")],
        )
        if not path:
            return
        try:
            content = self._render_verified(records, fmt)
            Path(path).write_text(content, encoding="utf-8")
            scope = "当前搜索结果" if self._verify_terms() else "全部验证数据"
            message = f"已导出 {len(records)} 条可用源（{scope}）：{path}"
            self.lbl_verify_hint.configure(text=message, foreground=self.c["ok"])
            self.log("info", message)
        except OSError as exc:
            self.lbl_verify_hint.configure(text="导出失败", foreground=self.c["warn"])
            messagebox.showerror("导出失败", str(exc))

    # ---------- 所有数据源管理 ----------
    # 安装软件以来导入过的全部数据：按 URL 归并，记录验证成功/失败次数
    ALL_FIELD_KEYS = {
        "全部": None,
        "频道": ("name",),
        "分组": ("group",),
        "地址": ("url",),
        "来源": ("source",),
        "最新状态": ("status",),
    }

    def _all_state_path(self) -> Path:
        return self.config_obj.config_dir / "all_entries.json"

    def _load_all_records(self) -> None:
        path = self._all_state_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for rec in data.get("records") or []:
                url = rec.get("url") or ""
                if url:
                    self.all_records[url] = rec
            self._sync_all_from_result()
            self._rebuild_all_trees()
            self.log("info", f"已载入历史数据记录 {len(self.all_records)} 条")
        except Exception as exc:
            self.log("warn", f"读取所有数据源记录失败：{exc}")

    def _save_all_records(self) -> None:
        try:
            self._all_state_path().write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "records": list(self.all_records.values()),
                    },
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            self._all_dirty = 0
        except OSError as exc:
            self.log("warn", f"保存所有数据源记录失败：{exc}")

    def _sync_all_from_result(self) -> None:
        """把 result.json 里的历史成功源并入登记簿（只补缺失的 URL，不重复计数）.

        旧版本运行没有累计次数，这里给历史成功源补上 1 次成功作为起点；
        本次运行实时计过数的 URL 已在登记簿里，不会重复累加。
        """
        path = self.config_obj.output_dir / "result.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        updated_at = data.get("updated_at") or ""
        added = 0
        for channel in data.get("channels", []) or []:
            for source in channel.get("sources", []) or []:
                if source.get("ok") is False:
                    continue
                url = source.get("url") or ""
                if not url or url in self.all_records:
                    continue
                last_at = source.get("last_ok_at") or updated_at
                self.all_records[url] = {
                    "name": channel.get("name") or "",
                    "group": channel.get("group") or "",
                    "url": url,
                    "source": source.get("source") or "",
                    "headers": source.get("headers") or {},
                    "ok_count": 1, "fail_count": 0,
                    "last_status": "成功",
                    "last_at": last_at or updated_at,
                    "last_reason": "",
                    "first_seen": source.get("first_ok_at") or updated_at,
                }
                added += 1
        if added:
            self._save_all_records()
            self._rebuild_all_trees()
            self.log("info", f"已从历史结果回填 {added} 条数据记录（各计 1 次成功）")

    def _record_check(self, entry, ok: bool, reason: str = "",
                      filtered: bool = False) -> None:
        """登记/更新一条数据的验证情况；同一 URL 多次导入会归并计数."""
        url = entry.url
        if not url:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rec = self.all_records.get(url)
        if rec is None:
            rec = {
                "name": entry.name or "", "group": entry.group or "",
                "url": url, "source": entry.source or "",
                "headers": dict(entry.headers or {}),
                "ok_count": 0, "fail_count": 0,
                "last_status": "", "last_at": now, "last_reason": "",
                "first_seen": now,
            }
            self.all_records[url] = rec
        else:
            if entry.name:
                rec["name"] = entry.name
            if entry.group:
                rec["group"] = entry.group
            if entry.source:
                rec["source"] = entry.source
            if entry.headers and not rec.get("headers"):
                rec["headers"] = dict(entry.headers)
        if filtered:
            rec["last_status"] = "已过滤"
            rec["last_reason"] = reason
            rec["last_at"] = now
        elif ok:
            rec["ok_count"] = int(rec.get("ok_count") or 0) + 1
            rec["last_status"] = "成功"
            rec["last_reason"] = ""
            rec["last_at"] = now
        else:
            rec["fail_count"] = int(rec.get("fail_count") or 0) + 1
            rec["last_status"] = "失败"
            rec["last_reason"] = reason
            rec["last_at"] = now
        self._all_dirty += 1
        if self._all_dirty >= 100:
            self._save_all_records()

    def _all_values(self, rec: dict) -> tuple:
        return (
            rec.get("name") or "-",
            rec.get("group") or "-",
            rec.get("url") or "-",
            str(rec.get("ok_count") or 0),
            str(rec.get("fail_count") or 0),
            rec.get("last_status") or "未验证",
            rec.get("last_at") or "-",
            rec.get("source") or "-",
        )

    def _build_all_tab(self) -> None:
        outer = ttk.Frame(self.tab_all, padding=10)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="所有数据源管理", style="Module.TLabel").pack(side="left")
        self.lbl_all_count = ttk.Label(head, text="0 条", foreground=self.c["ok"])
        self.lbl_all_count.pack(side="left", padx=10)

        # 搜索模块：与验证结果页一致
        search, search_body, self.lbl_all_search_state = self._module(
            outer, "搜索", "命中 0 / 0 条")
        search.pack(side="top", fill="x", pady=(0, 8))
        self.var_all_search = tk.StringVar()
        self.var_all_search.trace_add("write", self._on_all_search_changed)
        self.var_all_field = tk.StringVar(value="全部")
        ttk.Button(search_body, text="清空搜索",
                   command=self._clear_all_search).pack(side="right")
        ttk.Label(search_body, text="范围").pack(side="left", padx=(0, 6))
        field_box = ttk.Combobox(
            search_body, textvariable=self.var_all_field, width=9,
            values=("全部", "频道", "分组", "地址", "来源", "最新状态"),
            state="readonly",
        )
        field_box.pack(side="left")
        field_box.bind("<<ComboboxSelected>>", lambda *_: self._rebuild_all_trees())
        ttk.Label(search_body, text="关键词").pack(side="left", padx=(12, 8))
        all_entry = tk.Entry(
            search_body, textvariable=self.var_all_search,
            background=self.c["text_bg"], foreground=self.c["text_fg"],
            insertbackground=self.c["caret"],
            selectbackground=self.c["sel_bg"], selectforeground=self.c["sel_fg"],
            highlightthickness=1, highlightbackground=self.c["muted"],
            highlightcolor=self.c["sel_bg"], relief="flat", borderwidth=0,
            font=("Menlo", 12),
        )
        all_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 12))
        self._bind_entry_menu(all_entry)
        all_entry.bind("<KeyRelease>", lambda *_: self._on_all_search_changed())

        # 列表上方操作按钮栏
        bar = ttk.Frame(outer)
        bar.pack(side="top", fill="x", pady=(0, 6))
        self.btn_all_select = ttk.Button(
            bar, text="全选", command=self._toggle_select_all_all)
        self.btn_all_select.pack(side="left")
        self.btn_all_play = ttk.Button(
            bar, text="播放", command=self._play_all, state="disabled")
        self.btn_all_play.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="复制",
                   command=self._copy_all_clipboard).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="重测",
                   command=self._retest_all).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="删除",
                   command=self._delete_all_rows).pack(side="left", padx=(8, 0))
        self.lbl_all_selected = ttk.Label(bar, text="未选中", foreground=self.c["ok"])
        self.lbl_all_selected.pack(side="right")

        # 两个子页：验证成功 / 验证失败
        self.all_sub = ttk.Notebook(outer)
        self.all_sub.pack(fill="both", expand=True)
        self.all_ok_tab = ttk.Frame(self.all_sub)
        self.all_bad_tab = ttk.Frame(self.all_sub)
        self.all_sub.add(self.all_ok_tab, text="  验证成功  ")
        self.all_sub.add(self.all_bad_tab, text="  验证失败  ")
        self._all_headings = {
            "sel": ("选", 44), "name": ("频道", 160), "group": ("分组", 120),
            "url": ("地址", 520), "ok_count": ("验证成功次数", 120),
            "fail_count": ("验证失败次数", 120), "status": ("最新状态", 95),
            "last_at": ("最近验证时间", 175), "source": ("来源", 210),
        }
        self.tree_all_ok = self._build_all_tree(self.all_ok_tab)
        self.tree_all_bad = self._build_all_tree(self.all_bad_tab)
        # 切换子页时刷新按钮可用状态（播放单选限制、全选文案）
        self.all_sub.bind(
            "<<NotebookTabChanged>>",
            lambda _e: self._on_all_selection_changed(self._active_all_tree())
            if hasattr(self, "tree_all_bad") else None,
        )
        self._rebuild_all_trees()

        footer = ttk.Frame(outer, padding=(0, 8, 0, 0))
        footer.pack(fill="x")
        self.lbl_all_hint = ttk.Label(
            footer, text="数据按 URL 归并；点击表头可正/倒序排序",
            foreground=self.c["ok"],
        )
        self.lbl_all_hint.pack(side="left")

    def _build_all_tree(self, parent) -> ttk.Treeview:
        frame = ttk.Frame(parent, padding=(6, 6))
        frame.pack(fill="both", expand=True)
        inner = ttk.Frame(frame, width=980, height=400)
        inner.pack(fill="both", expand=True)
        inner.grid_propagate(False)
        inner.pack_propagate(False)
        columns = tuple(self._all_headings)
        tree = ttk.Treeview(inner, columns=columns, show="headings", height=15)
        for col, (title, width) in self._all_headings.items():
            anchor = "center" if col == "sel" else "w"
            tree.column(col, width=width, minwidth=width, anchor=anchor,
                        stretch=False)
            if col == "sel":
                tree.heading(col, text=title, command=self._toggle_select_all_all)
            else:
                tree.heading(col, text=title,
                             command=lambda c=col: self._sort_all(c))
        vsb = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
        hsb = self._h_scrollbar(inner, tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        tree.tag_configure("ok", foreground=self.c["ok"])
        tree.tag_configure("bad", foreground=self.c["warn"])
        tree.bind("<Button-1>", lambda e: self._on_all_click(e, tree), add="+")
        tree.bind("<<TreeviewSelect>>",
                  lambda _e: self._on_all_selection_changed(tree), add="+")
        tree.bind("<Button-2>", self._show_all_menu)
        tree.bind("<Button-3>", self._show_all_menu)
        tree.bind("<Double-1>", self._copy_all_url)
        self._bind_tree_tooltip(tree)
        return tree

    def _active_all_tree(self) -> ttk.Treeview:
        if not hasattr(self, "all_sub"):
            return self.tree_all_ok
        return (self.tree_all_ok
                if self.all_sub.index(self.all_sub.select()) == 0
                else self.tree_all_bad)

    def _on_all_search_changed(self, *_args) -> None:
        if self._all_search_job:
            try:
                self.after_cancel(self._all_search_job)
            except (tk.TclError, ValueError):
                pass
        self._all_search_job = self.after(160, self._rebuild_all_trees)

    def _clear_all_search(self) -> None:
        self.var_all_search.set("")
        self._rebuild_all_trees()

    def _all_terms(self) -> list[str]:
        return (self.var_all_search.get() or "").strip().casefold().split()

    def _all_matches(self, rec: dict, terms: list[str]) -> bool:
        if not terms:
            return True
        keys = self.ALL_FIELD_KEYS.get(self.var_all_field.get()) or (
            "name", "group", "url", "source", "status"
        )
        values = []
        for key in keys:
            if key == "status":
                values.append(rec.get("last_status") or "未验证")
            else:
                values.append(str(rec.get(key) or ""))
        haystack = " ".join(values).casefold()
        return all(term in haystack for term in terms)

    def _all_sort_value(self, rec: dict, col: str):
        if col == "ok_count":
            return int(rec.get("ok_count") or 0)
        if col == "fail_count":
            return int(rec.get("fail_count") or 0)
        if col == "status":
            # 表头键为 status，实际字段是 last_status
            return str(rec.get("last_status") or "未验证").casefold()
        if col == "last_at":
            return str(rec.get("last_at") or "")
        return str(rec.get(col) or "").casefold()

    def _sort_all(self, col: str) -> None:
        if self.all_sort_col == col:
            self.all_sort_reverse = not self.all_sort_reverse
        else:
            self.all_sort_col = col
            self.all_sort_reverse = False
        self._rebuild_all_trees()

    def _fill_all_tree(self, tree: ttk.Treeview, rows: dict,
                       records: list[dict]) -> None:
        selected_urls = {rows[i].get("url") for i in tree.selection()
                         if i in rows}
        tree.delete(*tree.get_children())
        rows.clear()
        for rec in records:
            tag = "ok" if rec.get("last_status") == "成功" else "bad"
            iid = tree.insert("", "end",
                              values=("☐",) + self._all_values(rec), tags=(tag,))
            rows[iid] = rec
            if rec.get("url") in selected_urls:
                tree.selection_add(iid)
        self._sync_all_check(tree)

    def _rebuild_all_trees(self) -> None:
        """按搜索词与排序重建两个子页；成功页显示最新状态为成功的记录."""
        if self._all_search_job:
            try:
                self.after_cancel(self._all_search_job)
            except (tk.TclError, ValueError):
                pass
        self._all_search_job = None
        terms = self._all_terms()
        ok_recs: list[dict] = []
        bad_recs: list[dict] = []
        for rec in self.all_records.values():
            if not self._all_matches(rec, terms):
                continue
            (ok_recs if rec.get("last_status") == "成功" else bad_recs).append(rec)
        for recs in (ok_recs, bad_recs):
            if self.all_sort_col:
                try:
                    recs.sort(key=lambda r: self._all_sort_value(r, self.all_sort_col),
                              reverse=self.all_sort_reverse)
                except (TypeError, ValueError):
                    pass
        self._fill_all_tree(self.tree_all_ok, self.all_rows, ok_recs)
        self._fill_all_tree(self.tree_all_bad, self.all_rows_bad, bad_recs)
        for tree in (self.tree_all_ok, self.tree_all_bad):
            for col, (title, _width) in self._all_headings.items():
                arrow = ""
                if col == self.all_sort_col:
                    arrow = " ▼" if self.all_sort_reverse else " ▲"
                tree.heading(col, text=title + arrow)
        self.all_sub.tab(0, text=f"  验证成功（{len(ok_recs)}）  ")
        self.all_sub.tab(1, text=f"  验证失败（{len(bad_recs)}）  ")
        total = len(self.all_records)
        visible = len(ok_recs) + len(bad_recs)
        label = f"{visible} 条" if visible == total else f"{visible} / {total} 条"
        self.lbl_all_count.configure(text=label, foreground=self.c["ok"])
        self.lbl_all_search_state.configure(
            text=f"命中 {visible} / {total} 条", foreground=self.c["ok"])

    def _on_all_click(self, event, tree) -> str | None:
        """点击行首「选」列 = 勾选/取消该行."""
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        if tree.identify_column(event.x) != "#1":
            return None
        row = tree.identify_row(event.y)
        if not row:
            return None
        if row in tree.selection():
            tree.selection_remove(row)
        else:
            tree.selection_add(row)
        return "break"

    def _sync_all_check(self, tree: ttk.Treeview) -> None:
        selected = set(tree.selection())
        for iid in tree.get_children():
            mark = "☑" if iid in selected else "☐"
            if tree.set(iid, "sel") != mark:
                tree.set(iid, "sel", mark)

    def _on_all_selection_changed(self, tree: ttk.Treeview) -> None:
        self._sync_all_check(tree)
        selected = tree.selection()
        if getattr(self, "btn_all_play", None):
            self.btn_all_play.configure(
                state="normal" if len(selected) == 1 else "disabled")
        if getattr(self, "lbl_all_selected", None):
            self.lbl_all_selected.configure(
                text=f"已选 {len(selected)} 条" if selected else "未选中")
        if getattr(self, "btn_all_select", None):
            rows = tree.get_children()
            self.btn_all_select.configure(
                text="取消全选" if rows and len(selected) >= len(rows) else "全选")

    def _toggle_select_all_all(self) -> None:
        tree = self._active_all_tree()
        rows = tree.get_children()
        if not rows:
            return
        if len(tree.selection()) >= len(rows):
            tree.selection_remove(tree.selection())
        else:
            tree.selection_set(rows)

    def _selected_all_records(self) -> list[dict]:
        tree = self._active_all_tree()
        rows = self.all_rows if tree is self.tree_all_ok else self.all_rows_bad
        return [rows[i] for i in tree.selection() if i in rows]

    def _copy_all_url(self, event) -> None:
        tree = self._active_all_tree()
        row = tree.identify_row(event.y)
        if not row:
            return
        values = tree.item(row, "values")
        if len(values) > 2:
            self.clipboard_clear()
            self.clipboard_append(values[2])
            self.log("info", f"已复制地址：{values[2]}")

    def _copy_all_clipboard(self) -> None:
        records = self._selected_all_records()
        urls = [r.get("url") or "" for r in records if r.get("url")]
        if not urls:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(urls))
        self.log("info", f"已复制 {len(urls)} 条地址")

    def _play_all(self) -> None:
        records = self._selected_all_records()
        if not records:
            return
        if len(records) > 1:
            self.log("warn", "播放一次只支持一条源；多条源请用「复制」或「重测」")
            return
        url = records[0].get("url") or ""
        if not url:
            return
        try:
            subprocess.Popen(["open", url])
            self.log("info", f"已调用系统默认播放器打开：{records[0].get('name') or url}")
        except Exception as exc:
            self.log("warn", f"播放失败：{exc}")

    def _delete_all_rows(self) -> None:
        records = self._selected_all_records()
        if not records:
            return
        if not messagebox.askyesno(
                "确认删除",
                f"确定从所有数据中删除选中的 {len(records)} 条吗？\n"
                "（删除后，下次运行如果重新导入会再次出现）"):
            return
        for rec in records:
            self.all_records.pop(rec.get("url") or "", None)
        self._rebuild_all_trees()
        self._save_all_records()
        self.log("info", f"已删除 {len(records)} 条数据记录")

    def _retest_all(self) -> None:
        records = self._selected_all_records()
        if not records:
            return
        settings = self.config_obj.settings
        ffprobe_bin = (self.config_obj.ffprobe_bin()
                       if settings.get("ffprobe_enabled") else "")
        self.log("info", f"开始重测 {len(records)} 条数据源…")

        def worker():
            for rec in records:
                entry = Entry(
                    name=rec.get("name") or "", url=rec.get("url") or "",
                    group=rec.get("group") or "",
                    source=rec.get("source") or "retest",
                    headers=rec.get("headers") or {},
                )
                try:
                    result = check_entry(entry, settings, ffprobe_bin)
                except Exception as exc:
                    self.queue.put(("log", "warn", f"重测出错 {entry.url}：{exc}"))
                    continue
                # 成功/失败次数与最新状态在主线程里统一登记
                self.queue.put(("all_result", result))
            self.queue.put(("all_done",))

        threading.Thread(target=worker, daemon=True).start()

    def _show_all_menu(self, event) -> None:
        tree = self._active_all_tree()
        row = tree.identify_row(event.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
        if not tree.selection():
            return
        menu = tk.Menu(
            self, tearoff=0,
            background=self.c["panel"], foreground=self.c["text_fg"],
            activebackground=self.c["sel_bg"], activeforeground=self.c["sel_fg"],
            relief="flat", borderwidth=1, font=("Helvetica", 13),
        )
        menu.add_command(label="播放", command=self._play_all)
        menu.add_command(label="复制", command=self._copy_all_clipboard)
        menu.add_command(label="重测", command=self._retest_all)
        menu.add_separator()
        menu.add_command(label="删除", command=self._delete_all_rows)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---------- 运行控制 ----------
    def _start_run(self, mode: str = "full") -> None:
        if self.engine and self.engine.running:
            return
        if mode == "full":
            if not messagebox.askyesno(
                    "全量更新",
                    "全量更新会把数据源中添加的所有源重新测试一次"
                    "（包括已经测试过的数据），耗时较长。\n\n确定继续吗？"):
                return
        self._run_mode = mode
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self._clear_verified()
        self._reset_fail_stats()
        self.progress.configure(value=0, maximum=1)
        self.btn_start.configure(state="disabled")
        self.btn_incr.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        if mode == "incremental":
            self.lbl_stats.configure(text="排重更新：正在采集数据源…")
            self.lbl_run_state.configure(text="排重更新中", foreground=self.c["ok"])
        else:
            self.lbl_stats.configure(text="全量更新：正在采集数据源…")
            self.lbl_run_state.configure(text="全量更新中", foreground=self.c["ok"])
        self.lbl_result_state.configure(text="采集中…", foreground=self.c["ok"])
        self.engine = Engine(
            self.config_obj,
            on_log=self.log,
            on_progress=self._on_progress,
            on_entry=self._on_entry,
            # 排重更新：只检测「所有数据源管理」里没有的 URL
            skip_urls=(set(self.all_records.keys())
                       if mode == "incremental" else None),
            merge_previous=(mode == "incremental"),
        )

        def worker():
            summary = self.engine.run()
            self.queue.put(("finish", summary))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_run(self) -> None:
        if self.engine:
            self.engine.stop()
        self.btn_stop.configure(state="disabled")

    def _on_finish(self, summary) -> None:
        self.btn_start.configure(state="normal")
        self.btn_incr.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.last_run_at = datetime.now()
        self.api.set_summary(summary)
        incremental = getattr(self, "_run_mode", "full") == "incremental"
        # 记录本次运行每个数据源的解析条数，供数据源页展示
        counts = dict(getattr(self.engine, "source_counts", {}) or {})
        if counts:
            self.source_stats.update(counts)
            self._save_source_stats()
            self.log("info", "各数据源解析条数："
                     + "、".join(
                         f"{k.split(':', 1)[-1]} {v} 条"
                         for k, v in sorted(counts.items(), key=lambda x: -x[1])[:8]))
            self._refresh_source_status()
        prefix = "排重更新完成" if incremental else "全量更新完成"
        text = (f"{prefix}：{summary.channels} 个频道 / 可用 {summary.ok} 条 / "
                f"不可用 {summary.failed} 条 / 已过滤 {summary.filters_removed} 条 / "
                f"耗时 {summary.duration_s} 秒")
        if summary.error:
            text += f"（{summary.error}）"
        self.lbl_stats.configure(text=text)
        self.lbl_run_state.configure(text="已完成", foreground=self.c["ok"])
        self.lbl_result_state.configure(text=f"{len(self.results)} 条", foreground=self.c["ok"])
        if incremental:
            # 排重更新只检测了新数据：验证结果页改从合并后的 result.json 恢复
            self._reload_verified_from_disk()
        else:
            self._rebuild_verified_tree()
            self._save_verified_state()
        self._sync_all_from_result()
        self._save_all_records()
        self._rebuild_all_trees()
        self.lbl_verify_hint.configure(
            text=f"本次运行得到 {len(self.verified)} 条验证数据；导出按当前搜索结果",
            foreground=self.c["ok"],
        )
        self._refresh_statusbar()

    def _schedule_tick(self) -> None:
        try:
            settings = self.config_obj.settings
            if settings.get("schedule_enabled") and not (self.engine and self.engine.running):
                now = datetime.now()
                due = False
                if settings.get("schedule_mode") == "daily":
                    raw = str(settings.get("schedule_daily_time") or "03:00")
                    try:
                        hour, minute = [int(x) for x in raw.strip().split(":")[:2]]
                    except ValueError:
                        hour, minute = 3, 0
                    if now.hour == hour and now.minute == minute and self.last_daily_date != now.date():
                        self.last_daily_date = now.date()
                        due = True
                else:
                    hours = max(0.1, float(settings.get("schedule_interval_hours") or 12))
                    if (now - self.last_run_at).total_seconds() >= hours * 3600:
                        due = True
                if due:
                    self.log("info", "定时任务触发，开始自动更新")
                    self._start_run()
        except Exception as exc:
            self.log("warn", f"定时检查出错：{exc}")
        self._schedule_job = self.after(30_000, self._schedule_tick)

    # ---------- API ----------
    def _toggle_api(self, start: bool | None = None) -> None:
        if self.api.running and start is not True:
            self.api.stop()
        else:
            ok, message = self.api.start()
            if not ok:
                messagebox.showwarning("API 启动失败", message)
            else:
                self.log("info", f"播放器订阅地址：{message}")
        running = self.api.running
        self.btn_api.configure(text="停止 API" if running else "启动 API")
        self.var_api_url.set(self.api.display_url)
        self.lbl_api_state.configure(
            text="运行中" if running else "未启动",
            foreground=self.c["ok"] if running else self.c["warn"],
        )
        self._refresh_statusbar()

    def _copy_selected_url(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        values = self.tree.item(row, "values")
        if len(values) > 3:
            self.clipboard_clear()
            self.clipboard_append(values[3])
            self.log("info", f"已复制地址：{values[3]}")

    # ---------- 数据源 ----------
    def _load_sources_to_ui(self) -> None:
        self._local_raw = [str(item) for item in
                           (self.config_obj.sources.get("local_files") or [])]
        self._subs_raw = [str(item) for item in
                          (self.config_obj.sources.get("subscribe_urls") or [])]
        self._render_source_lists()
        self.txt_raw.delete("1.0", "end")
        self.txt_raw.insert("1.0", str(self.config_obj.sources.get("raw_text") or ""))

    def _has_any_source(self) -> bool:
        s = self.config_obj.sources
        return bool(
            (s.get("local_files") or [])
            or (s.get("subscribe_urls") or [])
            or str(s.get("raw_text") or "").strip()
        )

    def _note(self, text: str, warn: bool = False) -> None:
        self.lbl_source_hint.configure(
            text=text, foreground=self.c["warn"] if warn else self.c["ok"]
        )

    def _source_stats_path(self) -> Path:
        return self.config_obj.config_dir / "source_counts.json"

    def _load_source_stats(self) -> None:
        try:
            path = self._source_stats_path()
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                self.source_stats = dict(data.get("counts") or {})
        except Exception:
            self.source_stats = {}

    def _save_source_stats(self) -> None:
        try:
            self._source_stats_path().write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "counts": self.source_stats,
                    },
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            self.log("warn", f"保存数据源条数统计失败：{exc}")

    def _local_entry_count(self, path: str) -> int | None:
        """本地文件源实时解析条数（按 mtime+大小缓存，避免重复解析大文件）."""
        try:
            real = Path(self.config_obj.local_path(str(path)))
            stat = real.stat()
            key = str(real)
            cached = self._local_count_cache.get(key)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                return cached[2]
            count = len(parse_auto(decode_bytes(real.read_bytes()), "local"))
            self._local_count_cache[key] = (stat.st_mtime, stat.st_size, count)
            return count
        except Exception:
            return None

    def _sub_entry_count(self, url: str) -> int | None:
        """订阅源条数来自最近一次运行的逐源统计."""
        value = self.source_stats.get(f"subscribe:{url}")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _source_totals(self) -> tuple[int | None, int | None]:
        """文件源 / 订阅源的条数合计；无法统计时为 None."""
        s = self.config_obj.sources
        local_counts = [self._local_entry_count(p) for p in (s.get("local_files") or [])]
        local_total = (sum(c for c in local_counts if c is not None)
                       if any(c is not None for c in local_counts) else None)
        sub_counts = [self._sub_entry_count(u) for u in (s.get("subscribe_urls") or [])]
        sub_total = (sum(c for c in sub_counts if c is not None)
                     if any(c is not None for c in sub_counts) else None)
        return local_total, sub_total

    def _refresh_source_status(self) -> None:
        """状态栏只显示已写入配置（真正生效）的数量与每个来源的条数."""
        s = self.config_obj.sources
        files = list(s.get("local_files") or [])
        subs = list(s.get("subscribe_urls") or [])
        raw = str(s.get("raw_text") or "")
        try:
            raw_count = len(parse_auto(raw, "raw"))
        except Exception:
            raw_count = 0
        local_total, sub_total = self._source_totals()
        counts = (
            f"文件源 {len(files)} 个/{_fmt_entries(local_total)}"
            f"    ·    订阅源 {len(subs)} 个/{_fmt_entries(sub_total)}"
            f"    ·    输入源 {raw_count} 条"
        )
        self.lbl_source_counts.configure(text=counts)
        self.lbl_valid_counts.configure(text=counts)
        total_entries = sum(x for x in (local_total, sub_total, raw_count)
                            if x is not None)
        self.lbl_valid_state.configure(
            text=f"共 {len(files) + len(subs) + raw_count} 个/{total_entries} 条",
            foreground=self.c["ok"],
        )
        self.lbl_local_count.configure(
            text=f"{len(files)} 个/{_fmt_entries(local_total)}")
        self.lbl_subs_count.configure(
            text=f"{len(subs)} 个/{_fmt_entries(sub_total)}")
        self._saved_raw_count = raw_count
        self._render_source_lists()
        self._raw_input_changed()
        return raw_count

    def _render_source_lists(self) -> None:
        """列表按「原始值 + 条数」渲染；原始值保存在 _local_raw/_subs_raw."""
        local_labels = []
        for item in self._local_raw:
            count = self._local_entry_count(item)
            local_labels.append(
                f"{item}    （{count} 条）" if count is not None
                else f"{item}    （无法读取）")
        sub_labels = []
        for item in self._subs_raw:
            count = self._sub_entry_count(item)
            sub_labels.append(
                f"{item}    （{count} 条）" if count is not None
                else f"{item}    （运行后统计）")
        if tuple(self.list_local.get(0, "end")) == tuple(local_labels) and \
                tuple(self.list_subs.get(0, "end")) == tuple(sub_labels):
            return  # 显示内容没变就不重绘，避免丢掉用户当前选中
        selection_local = self.list_local.curselection()
        selection_subs = self.list_subs.curselection()
        self.list_local.delete(0, "end")
        for label in local_labels:
            self.list_local.insert("end", label)
        self.list_subs.delete(0, "end")
        for label in sub_labels:
            self.list_subs.insert("end", label)
        for index in selection_local:
            if index < self.list_local.size():
                self.list_local.selection_set(index)
        for index in selection_subs:
            if index < self.list_subs.size():
                self.list_subs.selection_set(index)

    def _refresh_valid_sources(self) -> None:
        """运行页「有效源」刷新按钮：重读已保存的配置并给出日志反馈."""
        self._refresh_source_status()
        self.log("info", f"有效源已刷新：{self.lbl_valid_counts.cget('text')}")

    def _raw_input_changed(self, _event=None) -> None:
        """输入源实时反馈：粘贴后立刻看到行数，避免误以为没贴进去."""
        current = self.txt_raw.get("1.0", "end").rstrip()
        saved = str(self.config_obj.sources.get("raw_text") or "").rstrip()
        if current == saved:
            self.lbl_raw_count.configure(
                text=f"{getattr(self, '_saved_raw_count', 0)} 条已保存",
                foreground=self.c["ok"],
            )
            return
        lines = len([line for line in current.splitlines() if line.strip()])
        self.lbl_raw_count.configure(
            text=f"{lines} 行未保存", foreground=self.c["warn"])

    def _paste_raw(self) -> None:
        if self._paste_clipboard(self.txt_raw, replace=True):
            self._raw_input_changed()
            self._note("已用剪贴板内容替换输入框，请点「保存输入数据」写入配置")

    def _clear_raw(self) -> None:
        self.txt_raw.delete("1.0", "end")
        self._raw_input_changed()
        self._note("输入框已清空，点「保存输入数据」才会写入配置")

    def _save_file_sources(self) -> None:
        self.config_obj.sources["local_files"] = list(self._local_raw)
        self.config_obj.save_sources()
        self._refresh_source_status()

    def _save_sub_sources(self) -> None:
        self.config_obj.sources["subscribe_urls"] = list(self._subs_raw)
        self.config_obj.save_sources()
        self._refresh_source_status()

    def _save_raw_sources(self) -> None:
        self.config_obj.sources["raw_text"] = self.txt_raw.get("1.0", "end").rstrip() + "\n"
        self.config_obj.save_sources()
        count = self._refresh_source_status()
        self._note(f"输入源已保存：当前 {count} 条")
        self.log("info", f"输入源已保存：{count} 条")
        if not self._has_any_source():
            self._note("当前没有生效的数据源，运行会得到 0 条结果", warn=True)

    def _add_local_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择本地播放列表",
            filetypes=[("播放列表", "*.m3u *.m3u8 *.txt"), ("所有文件", "*.*")],
        )
        added = 0
        for path in paths:
            if path not in self._local_raw:
                self._local_raw.append(path)
                added += 1
        if added:
            self._save_file_sources()
            self._note(f"文件源已自动保存：新增 {added} 个，当前 {len(self._local_raw)} 个")
            self.log("info", f"文件源已自动保存：新增 {added} 个，当前 {len(self._local_raw)} 个")
        else:
            self._note("没有添加新文件（可能已存在）", warn=True)

    def _apply_subscription_text(self, raw: str) -> int:
        """把弹框内容写入列表并立即保存，返回新增数量."""
        added = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line in self._subs_raw:
                continue
            self._subs_raw.append(line)
            added += 1
        self._save_sub_sources()
        self.log("info", f"订阅源已保存：新增 {added} 个，当前共 {len(self._subs_raw)} 个")
        return added

    def _flat_button(self, parent, label: str, command, accent: bool = False) -> tk.Label:
        """自绘按钮.

        macOS 深色模式下，Toplevel 里的原生 ttk 按钮会出现「只剩灰块、文字不渲染」
        的情况；用显式配色的 Label 做按钮，两套主题下都能看清，并且强调主操作。
        """
        bg = self.c["accent_bg"] if accent else self.c["btn_bg"]
        fg = self.c["accent_fg"] if accent else self.c["btn_fg"]
        active = self.c["accent_active"] if accent else self.c["btn_active"]
        button = tk.Label(
            parent, text=label, background=bg, foreground=fg,
            padx=18, pady=8, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=self.c["muted"],
            font=("Helvetica", 13, "bold" if accent else "normal"),
            cursor="pointinghand", takefocus=0,
        )
        button.bind("<Button-1>", lambda _e: command())
        button.bind("<Enter>", lambda _e: button.configure(background=active))
        button.bind("<Leave>", lambda _e: button.configure(background=bg))
        return button

    def _paste_subscribe(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("添加订阅源")
        dialog.geometry("720x440")
        dialog.minsize(580, 360)
        dialog.transient(self)
        panel = self.c["panel"]
        dialog.configure(background=panel)

        header = tk.Frame(dialog, background=panel)
        header.pack(fill="x", padx=14, pady=(14, 8))
        tk.Label(header, text="每行一个订阅地址（# 开头为注释，会自动去重）",
                 background=panel, foreground=self.c["text_fg"],
                 font=("Helvetica", 13, "bold")).pack(side="left")
        counter = tk.Label(header, text="已输入 0 个地址", background=panel,
                           foreground=self.c["muted"])
        counter.pack(side="right")

        # 关键：底部按钮区先占位，文本框再 expand，内容再多也不会把按钮挤出窗口
        footer = tk.Frame(dialog, background=panel)
        footer.pack(side="bottom", fill="x", padx=14, pady=(8, 14))
        tk.Label(footer, text="⌘V 粘贴   ·   ⌘↩ 保存   ·   Esc 取消",
                 background=panel, foreground=self.c["muted"]).pack(side="left")

        text = tk.Text(dialog, **self._text_kwargs(height=12, wrap="none"))
        text.pack(fill="both", expand=True, padx=14, pady=(0, 2))

        def refresh(_event=None):
            lines = [
                line for line in text.get("1.0", "end").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            counter.configure(text=f"已输入 {len(lines)} 个地址")

        def close():
            dialog.grab_release()
            dialog.destroy()

        def confirm():
            added = self._apply_subscription_text(text.get("1.0", "end"))
            self._note(f"订阅源已保存：新增 {added} 个")
            close()

        self._flat_button(footer, "取消", close).pack(side="right")
        self._flat_button(footer, "保存  ⌘↩", confirm, accent=True).pack(
            side="right", padx=(0, 10))
        self._flat_button(
            footer, "粘贴内容",
            lambda: (self._paste_clipboard(text, replace=True), refresh()),
        ).pack(side="right", padx=(0, 10))

        self._bind_text_menu(text, refresh)
        text.bind("<Command-Return>", lambda _e: (confirm(), "break")[1])
        text.bind("<Control-Return>", lambda _e: (confirm(), "break")[1])
        dialog.bind("<Escape>", lambda _e: close())
        text.focus_set()
        dialog.grab_set()

    def _remove_and_save(self, widget: tk.Listbox, kind: str) -> None:
        selected = widget.curselection()
        if not selected:
            self._note("请先选中要移除的项", warn=True)
            return
        for index in reversed(selected):
            widget.delete(index)
            if kind == "local":
                del self._local_raw[index]
            else:
                del self._subs_raw[index]
        if kind == "local":
            self._save_file_sources()
        else:
            self._save_sub_sources()
        label = "文件源" if kind == "local" else "订阅源"
        self._note(f"{label}已自动保存：移除 {len(selected)} 个")
        self.log("info", f"{label}已自动保存：移除 {len(selected)} 个，当前 {widget.size()} 个")

    def _clear_and_save(self, widget: tk.Listbox, kind: str) -> None:
        label = "文件源" if kind == "local" else "订阅源"
        if widget.size() == 0:
            self._note(f"{label}本来就是空的", warn=True)
            return
        if not messagebox.askyesno("确认清空", f"确定清空全部{label}吗？此操作会立即保存。"):
            return
        widget.delete(0, "end")
        if kind == "local":
            self._local_raw.clear()
            self._save_file_sources()
        else:
            self._subs_raw.clear()
            self._save_sub_sources()
        self._note(f"{label}已清空并自动保存")
        self.log("warn", f"{label}已清空并自动保存")

    # ---------- 设置 ----------
    def _text_lines(self, widget: tk.Text) -> list[str]:
        return [
            line.strip()
            for line in widget.get("1.0", "end").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def _load_settings_to_ui(self) -> None:
        s = self.config_obj.settings
        self.var_concurrency.set(str(s.get("concurrency", 10)))
        self.var_connect_timeout.set(str(s.get("connect_timeout", 8)))
        self.var_read_timeout.set(str(s.get("read_timeout", 12)))
        self.var_probe_seconds.set(str(s.get("probe_seconds", 3)))
        self.var_min_speed.set(str(s.get("min_speed_kbs", 0)))
        self.var_max_per_channel.set(str(s.get("max_per_channel", 0)))
        self.var_ua.set(str(s.get("user_agent") or ""))
        self.var_ffprobe.set(bool(s.get("ffprobe_enabled", True)))
        self.var_ffprobe_strict.set(bool(s.get("ffprobe_strict", False)))
        self.var_dedupe.set(bool(s.get("dedupe", True)))
        self.var_api_enabled.set(bool(s.get("api_enabled", False)))
        self.var_api_host.set(str(s.get("api_host") or "127.0.0.1"))
        self.var_api_port.set(str(s.get("api_port", 8787)))
        self.var_schedule.set(bool(s.get("schedule_enabled", False)))
        self.var_schedule_mode.set(str(s.get("schedule_mode") or "interval"))
        self.var_schedule_hours.set(str(s.get("schedule_interval_hours", 12)))
        self.var_schedule_time.set(str(s.get("schedule_daily_time") or "03:00"))
        for widget, key in (
            (self.txt_black_kw, "blacklist_keywords"),
            (self.txt_black_host, "blacklist_hosts"),
            (self.txt_white_kw, "whitelist_keywords"),
        ):
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(str(x) for x in (s.get(key) or [])))
        self._refresh_settings_status()

    def _refresh_settings_status(self, feedback: str = "") -> None:
        """模块右上角的绿色状态，参考数据源页的数量状态设计."""
        s = self.config_obj.settings
        self.lbl_param_state.configure(
            text=f"并发 {s.get('concurrency', 10)} · 探测 {s.get('probe_seconds', 3)} 秒",
            foreground=self.c["ok"],
        )
        self.lbl_deep_state.configure(
            text="深度校验 {} · 严格模式 {}".format(
                "开" if s.get("ffprobe_enabled") else "关",
                "开" if s.get("ffprobe_strict") else "关",
            ),
            foreground=self.c["ok"],
        )
        self.lbl_filter_state.configure(
            text="排除 {} · 域名 {} · 保留 {}".format(
                len(s.get("blacklist_keywords") or []),
                len(s.get("blacklist_hosts") or []),
                len(s.get("whitelist_keywords") or []),
            ),
            foreground=self.c["ok"],
        )
        if feedback:
            self.lbl_settings_hint.configure(text=feedback, foreground=self.c["ok"])

    def _save_settings(self) -> None:
        s = self.config_obj.settings
        try:
            s["concurrency"] = max(1, int(self.var_concurrency.get()))
            s["connect_timeout"] = max(1.0, float(self.var_connect_timeout.get()))
            s["read_timeout"] = max(1.0, float(self.var_read_timeout.get()))
            s["probe_seconds"] = max(0.5, float(self.var_probe_seconds.get()))
            s["min_speed_kbs"] = max(0.0, float(self.var_min_speed.get()))
            s["max_per_channel"] = max(0, int(self.var_max_per_channel.get()))
            s["api_port"] = int(self.var_api_port.get())
        except ValueError as exc:
            messagebox.showwarning("设置无效", f"数值格式有误：{exc}")
            return
        s["user_agent"] = self.var_ua.get().strip()
        s["ffprobe_enabled"] = self.var_ffprobe.get()
        s["ffprobe_strict"] = self.var_ffprobe_strict.get()
        s["dedupe"] = self.var_dedupe.get()
        s["api_enabled"] = self.var_api_enabled.get()
        s["api_host"] = self.var_api_host.get()
        s["schedule_enabled"] = self.var_schedule.get()
        s["schedule_mode"] = self.var_schedule_mode.get()
        try:
            s["schedule_interval_hours"] = max(0.1, float(self.var_schedule_hours.get()))
        except ValueError:
            s["schedule_interval_hours"] = 12
        s["schedule_daily_time"] = self.var_schedule_time.get().strip()
        s["blacklist_keywords"] = self._text_lines(self.txt_black_kw)
        s["blacklist_hosts"] = self._text_lines(self.txt_black_host)
        s["whitelist_keywords"] = self._text_lines(self.txt_white_kw)
        self.config_obj.save_settings()
        self.api.host = s["api_host"]
        self.api.port = s["api_port"]
        self.var_api_url.set(self.api.display_url)
        self._refresh_statusbar()
        self._refresh_settings_status("设置已保存")
        self.log("info", "设置已保存（API 端口等改动需重启服务生效）")

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno("恢复默认", "确定要把所有设置恢复为默认值吗？数据源不会被改动。"):
            return
        self.config_obj.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.config_obj.save_settings()
        self._load_settings_to_ui()
        self._refresh_settings_status("设置已恢复默认")
        self.log("info", "设置已恢复默认")

    def _refresh_ffprobe_label(self) -> None:
        binary = self.config_obj.ffprobe_bin()
        if binary:
            self.lbl_ffprobe.configure(text="ffprobe 已就绪", foreground=self.c["ok"])
            self.var_ffprobe_path.set(f"检测到：{binary}")
        else:
            self.lbl_ffprobe.configure(text="ffprobe 未找到", foreground=self.c["warn"])
            self.var_ffprobe_path.set("未找到 ffprobe，自动降级为纯 HTTP 探测")

    # ---------- 杂项 ----------
    def _refresh_statusbar(self) -> None:
        api_state = f"API {self.api.display_url}" if self.api.running else "API 未启动"
        home = str(self.config_obj.home).replace(str(Path.home()), "~", 1)
        self.lbl_status.configure(text=f"数据目录 {home}   |   {api_state}")

    def _open_dir(self, path: Path) -> None:
        try:
            subprocess.Popen(["open", str(path)])
        except OSError as exc:
            self.log("warn", f"打开目录失败：{exc}")

    def _show_help(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("使用说明")
        dialog.geometry("680x600")
        text = tk.Text(dialog, **self._text_kwargs(
            wrap="word", padx=16, pady=12, font=("Menlo", 12)))
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _on_close(self) -> None:
        if self.engine and self.engine.running:
            if not messagebox.askyesno("正在运行", "更新任务还在进行，确定要退出吗？"):
                return
            self.engine.stop()
        # 取消尚未触发的定时器，避免窗口关闭后 Tk 报「invalid command name」
        for job in (self._queue_job, self._schedule_job, self._search_job):
            if job:
                try:
                    self.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
        self.api.stop()
        self.destroy()


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
