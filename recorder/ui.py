"""Tkinter user interface for RecordOnTime (bilingual, pause/resume, rounds)."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import sys
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio import AudioInventory, find_microphone, list_audio_devices
from .config import (
    AUDIO_BOTH,
    AUDIO_LABELS,
    AUDIO_MIC,
    AUDIO_NONE,
    AUDIO_SYSTEM,
    END_AT,
    END_DURATION,
    END_NONE,
    START_NOW,
    START_SCHEDULE,
    AppSettings,
    RecordingConfig,
    RoundPlan,
)
from .engine import (
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PAUSING,
    STATE_RECORDING,
    STATE_RESUMING,
    STATE_STARTING,
    STATE_STOPPING,
    RecordingEngine,
)
from .ffmpeg import ffmpeg_version, find_ffmpeg
from .i18n import format_datetime, label_for_audio, localize_message, tr
from .monitors import MonitorInfo, find_monitor, list_monitors, monitor_choices
from .util import (
    APP_NAME,
    APP_VERSION,
    data_dir,
    format_bytes,
    format_hms,
    log_dir,
    open_folder,
    parse_clock,
    parse_datetime,
    play_file,
    resource_path,
    safe_filename,
    set_sleep_block,
)

_LOG = logging.getLogger(__name__)

BG = "#0b0e14"
CARD = "#141821"
CARD2 = "#1b2130"
CARD3 = "#222938"
BORDER = "#262d3c"
TEXT = "#eef2fa"
MUTED = "#8e98ad"
FAINT = "#626d82"
ACCENT = "#ff4d5a"
ACCENT_DARK = "#d73745"
ACCENT_SOFT = "#3a2026"
GREEN = "#3ddc97"
BLUE = "#5b8cff"
YELLOW = "#ffc266"

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_TINY = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 20, "bold")
FONT_CARD = ("Microsoft YaHei UI", 11, "bold")
FONT_BIG = ("Consolas", 30, "bold")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


class SegmentedControl(tk.Frame):
    def __init__(self, parent, options, value, command=None, *, font=FONT_SMALL) -> None:
        super().__init__(parent, bg=CARD)
        self._command = command
        self._value = value
        self._buttons: dict[str, tk.Button] = {}
        self._enabled = True
        self._option_enabled: dict[str, bool] = {key: True for key, _ in options}
        for index, (key, label) in enumerate(options):
            button = tk.Button(
                self,
                text=label,
                font=font,
                relief="flat",
                bd=0,
                highlightthickness=0,
                padx=10,
                pady=5,
                cursor="hand2",
                command=lambda k=key: self._select(k, notify=True),
            )
            button.pack(side="left", padx=(0 if index == 0 else 4, 0))
            self._buttons[key] = button
        self._refresh()

    def get(self) -> str:
        return self._value

    def set(self, value: str, notify: bool = False) -> None:
        if value in self._buttons:
            self._value = value
            self._refresh()
            if notify and self._command:
                self._command(value)

    def _select(self, value: str, notify: bool) -> None:
        if not self._enabled or not self._option_enabled.get(value, True):
            return
        if self._value == value:
            return
        self._value = value
        self._refresh()
        if notify and self._command:
            self._command(value)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._refresh()

    def set_option_enabled(self, value: str, enabled: bool) -> None:
        self._option_enabled[value] = enabled
        self._refresh()

    def _refresh(self) -> None:
        for key, button in self._buttons.items():
            available = self._enabled and self._option_enabled.get(key, True)
            selected = key == self._value
            if selected:
                button.configure(
                    bg=ACCENT if available else ACCENT_SOFT,
                    fg="#ffffff" if available else "#9a666b",
                    activebackground=ACCENT_DARK if available else ACCENT_SOFT,
                    activeforeground="#ffffff",
                )
            else:
                button.configure(
                    bg=CARD2,
                    fg=TEXT if available else FAINT,
                    activebackground=CARD3,
                    activeforeground=TEXT,
                )
            button.configure(
                state="normal" if available else "disabled",
                cursor="hand2" if available else "arrow",
                disabledforeground=FAINT,
            )


class RecorderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self._alive = True
        self._ui_ready = False
        self._updating = False
        self._closing = False
        self._controls_enabled = True
        self._armed = False
        self._scheduled_start: datetime | None = None
        self._scheduled_stop: datetime | None = None
        self._scheduled_config: RecordingConfig | None = None
        self._recording_was_scheduled = False
        self._recording_started_at: datetime | None = None
        self._active_stop_at: datetime | None = None
        self._last_result: dict | None = None
        self._last_geometry = "1160x1000"
        self._last_disk_check = 0.0
        self._close_deadline_id = None
        self._tr_static: list[tuple[tk.Widget, str]] = []
        self._warning_sources: list[str] = []
        self._footer_source: tuple[str, str, dict] | None = None
        self._status_key = "待机"
        self._header_key = "●  待机"

        self.settings = AppSettings.load()
        self.lang = self.settings.language if self.settings.language in ("en", "zh") else "en"
        self._next_rounds: list[RoundPlan] = []
        for raw in self.settings.next_rounds:
            plan = RoundPlan.from_dict(raw)
            if plan is not None:
                self._next_rounds.append(plan)

        self._monitors: list[MonitorInfo] = []
        self._monitor_options: list[MonitorInfo] = []
        self._monitor_by_label: dict[str, MonitorInfo] = {}
        self._audio_inventory = AudioInventory(None, [])
        self._mic_by_label = {}
        self._ffmpeg_path = None
        self._ffmpeg_ready = False
        self._ffmpeg_version = "检测中…"
        self._ffmpeg_error = ""
        self._donate_image = None

        self._configure_window()
        self._load_environment()
        self._init_variables()
        self._build_ui()
        self._register_static_translation()
        self._bind_events()
        self._apply_settings()
        self._ui_ready = True
        self.report_callback_exception = self._report_callback_exception
        self._rebuild_menu()
        self._refresh_language_controls()
        self._update_preview()
        self._update_mode_rows()
        self._update_audio_controls()
        self._update_primary()
        self._apply_engine_state(self._engine.state)
        self.after(80, self._poll_events)
        self.after(120, self._tick)
        self.after(400, self._startup_notice)

    # ------------------------------------------------------------------ setup
    def t(self, text: str, **kwargs) -> str:
        return tr(text, self.lang, **kwargs)

    def _fmt_dt(self, value: datetime, *, seconds: bool = True) -> str:
        return format_datetime(value, self.lang, seconds=seconds)

    def _configure_window(self) -> None:
        self.title("RecordOnTime  ·  720P Screen Recorder")
        self.configure(bg=BG)
        self.minsize(1020, 700)
        try:
            geometry = self.settings.window_geometry
            width, height = 1160, 1000
            if "x" in geometry:
                parts = geometry.lower().split("x")
                width, height = int(parts[0]), int(parts[1].split("+")[0])
            width = max(1020, min(width, self.winfo_screenwidth() - 60))
            height = max(680, min(height, self.winfo_screenheight() - 70))
        except Exception:
            width, height = 1160, 1000
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2 - 10)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self._last_geometry = f"{width}x{height}"
        try:
            icon = tk.PhotoImage(file=str(resource_path("assets/app_256.png")))
            self._window_icon = icon
            self.iconphoto(True, icon)
        except Exception:
            pass
        try:
            self.iconbitmap(str(resource_path("assets/app.ico")))
        except Exception:
            pass
        self.option_add("*TCombobox*Listbox.background", CARD2)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=CARD2,
            background=CARD2,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", CARD2), ("disabled", CARD)],
            background=[("readonly", CARD2), ("active", CARD3), ("disabled", CARD)],
            foreground=[("readonly", TEXT), ("disabled", FAINT)],
            selectbackground=[("readonly", CARD2)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=CARD3,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            lightcolor=CARD3,
            darkcolor=CARD3,
        )
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=CARD2,
            bordercolor=CARD2,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

    def _load_environment(self) -> None:
        try:
            self._monitors = list_monitors()
        except Exception:
            _LOG.exception("Monitor enumeration failed")
            self._monitors = []
        self._monitor_options = monitor_choices(self._monitors) if self._monitors else []
        try:
            self._audio_inventory = list_audio_devices()
        except Exception:
            _LOG.exception("Audio enumeration failed")
            self._audio_inventory = AudioInventory(None, [], error="音频设备检测失败")
        try:
            self._ffmpeg_path = find_ffmpeg()
            self._ffmpeg_ready = True
            self._ffmpeg_version = ffmpeg_version(self._ffmpeg_path)
        except Exception as exc:
            self._ffmpeg_path = None
            self._ffmpeg_ready = False
            self._ffmpeg_error = str(exc)
            self._ffmpeg_version = "未找到"

    def _init_variables(self) -> None:
        now = datetime.now()
        start = (now + timedelta(minutes=max(1, self.settings.start_offset_minutes))).replace(
            second=0, microsecond=0
        )
        if start <= now:
            start += timedelta(minutes=1)
        duration = max(5, int(self.settings.duration_seconds))
        end = start + timedelta(seconds=duration)
        self.monitor_var = tk.StringVar()
        self.audio_var = tk.StringVar(value=self.settings.audio_source)
        self.mic_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(self.settings.resolved_output_dir))
        self.prefix_var = tk.StringVar(value=self.settings.prefix)
        self.start_date_var = tk.StringVar(value=start.strftime("%Y-%m-%d"))
        self.start_time_var = tk.StringVar(value=start.strftime("%H:%M:%S"))
        self.end_date_var = tk.StringVar(value=end.strftime("%Y-%m-%d"))
        self.end_time_var = tk.StringVar(value=end.strftime("%H:%M:%S"))
        self.duration_var = tk.StringVar(value=self.settings.duration_text())
        self.cursor_var = tk.BooleanVar(value=self.settings.capture_cursor)
        self.noise_var = tk.BooleanVar(value=self.settings.noise_reduction)
        self.minimize_var = tk.BooleanVar(value=self.settings.minimize_on_schedule)
        self.keep_awake_var = tk.BooleanVar(value=self.settings.keep_awake)
        self.progress_mode = False
        self._engine = RecordingEngine()

    # -------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_header()
        self._build_body()
        self._build_footer()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(16, 8))
        header.columnconfigure(1, weight=1)
        try:
            raw = tk.PhotoImage(file=str(resource_path("assets/app_256.png")))
            self._logo = raw.subsample(5)
            logo = tk.Label(header, image=self._logo, bg=BG)
            logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        except Exception:
            self._logo = None
        title = tk.Label(header, text="RecordOnTime", bg=BG, fg=TEXT, font=FONT_TITLE)
        title.grid(row=0, column=1, sticky="sw")
        subtitle = tk.Label(
            header,
            text="720P  ·  MP4  ·  定时开始  ·  自动结束",
            bg=BG,
            fg=MUTED,
            font=FONT_SMALL,
        )
        subtitle.grid(row=1, column=1, sticky="nw", pady=(1, 0))

        controls = tk.Frame(header, bg=BG)
        controls.grid(row=0, column=2, rowspan=2, sticky="e")
        chip = tk.Frame(controls, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        chip.pack(side="right")
        self.header_state = tk.Label(
            chip,
            text="●  待机",
            bg=CARD,
            fg=MUTED,
            font=FONT_SMALL,
            padx=12,
            pady=6,
        )
        self.header_state.pack()
        self.menu_btn = tk.Button(
            controls,
            text="菜单  v",
            command=self._show_main_menu,
            bg=CARD2,
            fg=MUTED,
            activebackground=CARD3,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_SMALL,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        self.menu_btn.pack(side="right", padx=(0, 8))
        self.lang_btn = tk.Button(
            controls,
            text="中文",
            command=self._toggle_language,
            bg=CARD2,
            fg=MUTED,
            activebackground=CARD3,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_SMALL,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        self.lang_btn.pack(side="right", padx=(0, 6))
        self.header_ffmpeg = tk.Label(
            header,
            text=self._header_ffmpeg_text(),
            bg=BG,
            fg=FAINT,
            font=FONT_TINY,
        )
        self.header_ffmpeg.grid(row=2, column=2, sticky="e", pady=(4, 0))

    def _build_body(self) -> None:
        outer = tk.Frame(self, bg=BG)
        outer.grid(row=1, column=0, sticky="nsew", padx=(26, 18))
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        self._body_outer = outer
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        canvas.configure(yscrollcommand=scrollbar.set)
        self._body_canvas = canvas
        self._body_scrollbar = scrollbar
        content = tk.Frame(canvas, bg=BG)
        self._body_content = content
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        self._body_window = window
        content.bind("<Configure>", self._on_content_configure)
        canvas.bind("<Configure>", self._on_canvas_configure)
        content.columnconfigure(0, weight=3, minsize=540, uniform="main")
        content.columnconfigure(1, weight=2, minsize=380, uniform="main")
        left = tk.Frame(content, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = tk.Frame(content, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 4))
        self._build_video_card(left)
        self._build_audio_card(left)
        self._build_save_card(left)
        self._build_schedule_card(right)
        self._build_status_card(right)

    def _card(self, parent, title: str, subtitle: str | None = None, *, last: bool = False, header_button=None):
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 0 if last else 10))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=16, pady=11)
        heading = tk.Frame(inner, bg=CARD)
        heading.pack(fill="x")
        tk.Label(heading, text=title, bg=CARD, fg=TEXT, font=FONT_CARD).pack(side="left")
        if header_button:
            text, command = header_button
            button = tk.Button(
                heading,
                text=text,
                command=command,
                bg=CARD2,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                highlightthickness=0,
                font=("Microsoft YaHei UI", 12, "bold"),
                width=2,
                cursor="hand2",
            )
            button.pack(side="right")
            self._add_round_btn = button
        if subtitle:
            tk.Label(heading, text=subtitle, bg=CARD, fg=FAINT, font=FONT_TINY).pack(
                side="right", padx=(0, 7), pady=(4, 0)
            )
        return card, inner

    def _build_video_card(self, parent) -> None:
        _, body = self._card(parent, "画面", "输出固定 720P")
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(10, 2))
        tk.Label(row, text="录屏范围", bg=CARD, fg=MUTED, font=FONT_SMALL, width=8, anchor="w").pack(
            side="left"
        )
        self.monitor_combo = ttk.Combobox(row, textvariable=self.monitor_var, state="readonly")
        self.monitor_combo.pack(side="left", fill="x", expand=True)
        self.monitor_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_monitor_note())
        self.monitor_refresh_btn = self._small_button(row, "刷新", self._refresh_monitors)
        self.monitor_refresh_btn.pack(side="left", padx=(6, 0))
        self.monitor_note = tk.Label(
            body,
            text="",
            bg=CARD,
            fg=FAINT,
            font=FONT_TINY,
            anchor="w",
            justify="left",
            wraplength=520,
        )
        self.monitor_note.pack(fill="x", pady=(6, 8), padx=(70, 0))
        opts = tk.Frame(body, bg=CARD)
        opts.pack(fill="x", padx=(70, 0))
        self.cursor_check = self._check(opts, "显示鼠标指针", self.cursor_var)
        self.cursor_check.grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.minimize_check = self._check(opts, "定时开始时最小化窗口", self.minimize_var)
        self.minimize_check.grid(row=0, column=1, sticky="w", padx=(0, 14))
        self.keep_awake_check = self._check(opts, "计划期间阻止休眠", self.keep_awake_var)
        self.keep_awake_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self._refresh_monitor_combo()

    def _build_audio_card(self, parent) -> None:
        _, body = self._card(parent, "声音", "48 kHz · AAC 192 kbps")
        head = tk.Frame(body, bg=CARD)
        head.pack(fill="x", pady=(9, 5))
        tk.Label(head, text="声音来源", bg=CARD, fg=MUTED, font=FONT_SMALL).pack(side="left")
        self.audio_refresh_btn = self._small_button(head, "刷新设备", self._refresh_audio_devices)
        self.audio_refresh_btn.pack(side="right")
        self.audio_segment = SegmentedControl(
            body,
            [
                (AUDIO_SYSTEM, "系统声音"),
                (AUDIO_MIC, "麦克风"),
                (AUDIO_BOTH, "两者"),
                (AUDIO_NONE, "静音"),
            ],
            self.settings.audio_source,
            command=self._on_audio_source_changed,
        )
        self.audio_segment.pack(fill="x", pady=(0, 8))
        mic_row = tk.Frame(body, bg=CARD)
        mic_row.pack(fill="x", pady=(0, 6))
        tk.Label(mic_row, text="麦克风", bg=CARD, fg=MUTED, font=FONT_SMALL, width=10, anchor="w").pack(
            side="left"
        )
        self.mic_combo = ttk.Combobox(mic_row, textvariable=self.mic_var, state="readonly")
        self.mic_combo.pack(side="left", fill="x", expand=True)
        self.noise_check = self._check(body, "麦克风降噪（轻度）", self.noise_var)
        self.noise_check.pack(anchor="w", padx=(70, 0), pady=(0, 8))
        self.audio_note = tk.Label(
            body,
            text="",
            bg=CARD,
            fg=FAINT,
            font=FONT_TINY,
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.audio_note.pack(fill="x", padx=(70, 0), pady=(0, 6))
        meters = tk.Frame(body, bg=CARD)
        meters.pack(fill="x", padx=(70, 0))
        self.meter_canvases: dict[str, tk.Canvas] = {}
        self.meter_values: dict[str, tk.Label] = {}
        self.meter_labels: dict[str, tk.Label] = {}
        for label, color in (("系统声音", BLUE), ("麦克风", GREEN)):
            line = tk.Frame(meters, bg=CARD)
            line.pack(fill="x", pady=1)
            name = tk.Label(line, text=label, bg=CARD, fg=FAINT, font=FONT_TINY, width=11, anchor="w")
            name.pack(side="left")
            canvas = tk.Canvas(line, height=7, width=200, bg=CARD2, highlightthickness=0, bd=0)
            canvas.pack(side="left", fill="x", expand=True, padx=(4, 7))
            value = tk.Label(line, text="—", bg=CARD, fg=FAINT, font=FONT_TINY, width=6, anchor="e")
            value.pack(side="right")
            self.meter_canvases[label] = canvas
            self.meter_values[label] = value
            self.meter_labels[label] = name
        self._refresh_audio_combo()

    def _build_save_card(self, parent) -> None:
        _, body = self._card(parent, "保存", "MP4 (H.264)", last=True)
        folder_row = tk.Frame(body, bg=CARD)
        folder_row.pack(fill="x", pady=(10, 6))
        tk.Label(folder_row, text="文件夹", bg=CARD, fg=MUTED, font=FONT_SMALL, width=10, anchor="w").pack(
            side="left"
        )
        self.output_entry = self._entry(folder_row, self.output_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.browse_btn = self._small_button(folder_row, "选择…", self._browse_output)
        self.browse_btn.pack(side="left", padx=(6, 0))
        prefix_row = tk.Frame(body, bg=CARD)
        prefix_row.pack(fill="x", pady=(0, 8))
        tk.Label(prefix_row, text="文件名", bg=CARD, fg=MUTED, font=FONT_SMALL, width=10, anchor="w").pack(
            side="left"
        )
        self.prefix_entry = self._entry(prefix_row, self.prefix_var)
        self.prefix_entry.pack(side="left", fill="x", expand=True)
        tk.Label(prefix_row, text=".mp4", bg=CARD, fg=FAINT, font=FONT_SMALL).pack(
            side="left", padx=(5, 0)
        )
        info = tk.Frame(body, bg=CARD)
        info.pack(fill="x", padx=(70, 0))
        self.disk_label = tk.Label(info, text="", bg=CARD, fg=FAINT, font=FONT_TINY, anchor="w")
        self.disk_label.pack(side="left")
        self.open_folder_btn = self._small_button(info, "打开文件夹", self._open_output_folder)
        self.open_folder_btn.pack(side="right")
        self._update_disk_free()

    def _build_schedule_card(self, parent) -> None:
        _, outer = self._card(
            parent,
            "录制计划",
            "本机时间 · F9 启停",
            header_button=("+", self._open_add_round),
        )
        body = tk.Frame(outer, bg=CARD)
        body.pack(fill="x")
        body.columnconfigure(0, weight=1)
        start_head = tk.Frame(body, bg=CARD)
        start_head.grid(row=0, column=0, sticky="ew", pady=(9, 0))
        tk.Label(start_head, text="开始", bg=CARD, fg=MUTED, font=FONT_SMALL, width=5, anchor="w").pack(
            side="left"
        )
        self.start_segment = SegmentedControl(
            start_head,
            [(START_NOW, "立即"), (START_SCHEDULE, "定时")],
            self.settings.start_mode,
            command=self._on_start_mode_changed,
        )
        self.start_segment.pack(side="left")
        self.start_when = tk.Frame(body, bg=CARD)
        self.start_when.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self._date_time_fields(self.start_when, self.start_date_var, self.start_time_var, "开始")
        start_quick = tk.Frame(self.start_when, bg=CARD)
        start_quick.pack(fill="x", padx=(45, 0), pady=(6, 0))
        self.start_quick_buttons = [
            self._chip(start_quick, "5 分钟后", lambda: self._quick_start(5)),
            self._chip(start_quick, "30 分钟后", lambda: self._quick_start(30)),
            self._chip(start_quick, "明天", lambda: self._quick_start(24 * 60)),
        ]
        for button in self.start_quick_buttons:
            button.pack(side="left", padx=(0, 6))
        tk.Frame(body, bg=BORDER, height=1).grid(row=2, column=0, sticky="ew", pady=10)
        end_head = tk.Frame(body, bg=CARD)
        end_head.grid(row=3, column=0, sticky="ew")
        tk.Label(end_head, text="结束", bg=CARD, fg=MUTED, font=FONT_SMALL, width=5, anchor="w").pack(
            side="left"
        )
        self.end_segment = SegmentedControl(
            end_head,
            [(END_DURATION, "按时长"), (END_AT, "到时间"), (END_NONE, "手动")],
            self.settings.end_mode,
            command=self._on_end_mode_changed,
        )
        self.end_segment.pack(side="left")
        self.duration_row = tk.Frame(body, bg=CARD)
        self.duration_row.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        duration_line = tk.Frame(self.duration_row, bg=CARD)
        duration_line.pack(fill="x")
        tk.Label(
            duration_line, text="时长", bg=CARD, fg=MUTED, font=FONT_SMALL, width=8, anchor="w"
        ).pack(side="left")
        self.duration_entry = self._entry(duration_line, self.duration_var, width=9)
        self.duration_entry.pack(side="left")
        tk.Label(
            duration_line, text="时:分:秒", bg=CARD, fg=FAINT, font=FONT_TINY
        ).pack(side="left", padx=(6, 0))
        duration_quick = tk.Frame(self.duration_row, bg=CARD)
        duration_quick.pack(fill="x", padx=(45, 0), pady=(5, 0))
        self.duration_quick_buttons = [
            self._chip(duration_quick, "15分", lambda: self._quick_duration(15)),
            self._chip(duration_quick, "30分", lambda: self._quick_duration(30)),
            self._chip(duration_quick, "1时", lambda: self._quick_duration(60)),
            self._chip(duration_quick, "2时", lambda: self._quick_duration(120)),
        ]
        for button in self.duration_quick_buttons:
            button.pack(side="left", padx=(0, 5))
        self.end_at_row = tk.Frame(body, bg=CARD)
        self.end_at_row.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self._date_time_fields(self.end_at_row, self.end_date_var, self.end_time_var, "结束")
        preview_box = tk.Frame(body, bg=CARD2, highlightbackground=BORDER, highlightthickness=1)
        preview_box.grid(row=6, column=0, sticky="ew", pady=(11, 10))
        self.preview_label = tk.Label(
            preview_box,
            text="",
            bg=CARD2,
            fg=MUTED,
            font=FONT_SMALL,
            justify="left",
            anchor="w",
            wraplength=395,
            padx=11,
            pady=9,
        )
        self.preview_label.pack(fill="x")
        self.primary_btn = tk.Button(
            body,
            text="安排定时录制",
            command=self._on_primary,
            bg=ACCENT,
            fg="#ffffff",
            activebackground=ACCENT_DARK,
            activeforeground="#ffffff",
            disabledforeground="#b9bdc7",
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 11, "bold"),
            pady=11,
            cursor="hand2",
        )
        self.primary_btn.grid(row=7, column=0, sticky="ew")

    def _build_status_card(self, parent) -> None:
        _, body = self._card(parent, "录制状态", "", last=True)
        state_row = tk.Frame(body, bg=CARD)
        state_row.pack(fill="x", pady=(9, 2))
        self.state_dot = tk.Canvas(state_row, width=14, height=14, bg=CARD, highlightthickness=0)
        self.state_dot.pack(side="left", padx=(0, 7))
        self._dot_item = self.state_dot.create_oval(3, 3, 11, 11, fill=MUTED, outline="")
        self.state_label = tk.Label(
            state_row, text="待机", bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")
        )
        self.state_label.pack(side="left")
        self.auto_label = tk.Label(state_row, text="", bg=CARD, fg=FAINT, font=FONT_TINY)
        self.auto_label.pack(side="right")
        self.timer_label = tk.Label(body, text="00:00:00", bg=CARD, fg=TEXT, font=FONT_BIG, anchor="w")
        self.timer_label.pack(fill="x", pady=(1, 0))
        self.detail_label = tk.Label(
            body,
            text="设置开始和结束时间，或立即录制。",
            bg=CARD,
            fg=MUTED,
            font=FONT_SMALL,
            justify="left",
            anchor="w",
            wraplength=400,
        )
        self.detail_label.pack(fill="x", pady=(1, 5))
        self.pause_btn = tk.Button(
            body,
            text="暂停",
            command=self._on_pause,
            bg=CARD2,
            fg=TEXT,
            activebackground=CARD3,
            activeforeground=TEXT,
            disabledforeground=FAINT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_SMALL,
            pady=7,
            cursor="hand2",
        )
        self.progress = ttk.Progressbar(
            body, style="Accent.Horizontal.TProgressbar", mode="indeterminate", length=200
        )
        self.warning_label = tk.Label(
            body,
            text="",
            bg=CARD,
            fg=YELLOW,
            font=FONT_TINY,
            justify="left",
            anchor="w",
            wraplength=400,
        )
        self.warning_label.pack(fill="x", pady=(6, 0))
        self.result_frame = tk.Frame(body, bg=CARD)
        tk.Frame(self.result_frame, bg=BORDER, height=1).pack(fill="x", pady=(8, 8))
        tk.Label(
            self.result_frame, text="已保存到", bg=CARD, fg=FAINT, font=FONT_TINY, anchor="w"
        ).pack(fill="x")
        self.result_path = tk.Label(
            self.result_frame,
            text="",
            bg=CARD,
            fg=GREEN,
            font=FONT_SMALL,
            justify="left",
            anchor="w",
            wraplength=400,
        )
        self.result_path.pack(fill="x", pady=(2, 7))
        buttons = tk.Frame(self.result_frame, bg=CARD)
        buttons.pack(fill="x")
        self.result_folder_btn = self._small_button(buttons, "打开文件夹", self._open_result_folder)
        self.result_folder_btn.pack(side="left")
        self.result_play_btn = self._small_button(buttons, "播放视频", self._play_result)
        self.result_play_btn.pack(side="left", padx=(6, 0))

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=BG)
        footer.grid(row=2, column=0, sticky="ew", padx=26, pady=(6, 12))
        footer.columnconfigure(0, weight=1)
        self.footer_status = tk.Label(
            footer, text="就绪", bg=BG, fg=MUTED, font=FONT_TINY, anchor="w"
        )
        self.footer_status.grid(row=0, column=0, sticky="w")
        self.footer_ffmpeg = tk.Label(footer, text="", bg=BG, fg=FAINT, font=FONT_TINY)
        self.footer_ffmpeg.grid(row=0, column=1, sticky="e", padx=(10, 10))
        self.diag_btn = tk.Button(
            footer,
            text="诊断信息",
            command=self._show_diagnostics,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_TINY,
            cursor="hand2",
        )
        self.diag_btn.grid(row=0, column=2, sticky="e")
        tk.Label(footer, text=f"v{APP_VERSION}", bg=BG, fg=FAINT, font=FONT_TINY).grid(
            row=0, column=3, sticky="e", padx=(10, 0)
        )
        self.footer_ffmpeg.configure(text=self._header_ffmpeg_text())

    # --------------------------------------------------------------- widgets
    def _small_button(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=CARD2,
            fg=MUTED,
            activebackground=CARD3,
            activeforeground=TEXT,
            disabledforeground=FAINT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_TINY,
            padx=9,
            pady=4,
            cursor="hand2",
        )

    def _chip(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=CARD2,
            fg=MUTED,
            activebackground=CARD3,
            activeforeground=TEXT,
            disabledforeground=FAINT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_TINY,
            padx=7,
            pady=3,
            cursor="hand2",
        )

    def _entry(self, parent, variable, width: int = 24) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=CARD2,
            fg=TEXT,
            insertbackground=TEXT,
            disabledbackground=CARD,
            disabledforeground=FAINT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=FONT_SMALL,
        )

    def _check(self, parent, text: str, variable) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            bg=CARD,
            fg=MUTED,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD2,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_SMALL,
            anchor="w",
            cursor="hand2",
        )

    def _date_time_fields(self, parent, date_var, time_var, prefix: str) -> None:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="日期", bg=CARD, fg=FAINT, font=FONT_TINY).pack(side="left", padx=(45, 4))
        date_entry = self._entry(row, date_var, width=12)
        date_entry.pack(side="left")
        tk.Label(row, text="时间", bg=CARD, fg=FAINT, font=FONT_TINY).pack(side="left", padx=(8, 4))
        time_entry = self._entry(row, time_var, width=10)
        time_entry.pack(side="left")
        if prefix == "开始":
            self.start_date_entry, self.start_time_entry = date_entry, time_entry
        else:
            self.end_date_entry, self.end_time_entry = date_entry, time_entry

    # ------------------------------------------------------------- i18n setup
    def _register_static_translation(self) -> None:
        dynamic = {
            self.header_state,
            self.header_ffmpeg,
            self.state_label,
            self.timer_label,
            self.detail_label,
            self.auto_label,
            self.preview_label,
            self.warning_label,
            self.result_path,
            self.disk_label,
            self.footer_status,
            self.footer_ffmpeg,
            self.monitor_note,
            self.audio_note,
            self.primary_btn,
            self.pause_btn,
            self.lang_btn,
            self.menu_btn,
        }
        self._dynamic_widgets = dynamic
        self._tr_static.clear()

        def walk(widget) -> None:
            for child in widget.winfo_children():
                walk(child)
            if widget in dynamic:
                return
            try:
                text = widget.cget("text")
            except (tk.TclError, AttributeError):
                return
            if not isinstance(text, str) or not text:
                return
            if _has_cjk(text) or text in ("FFmpeg 未就绪", "FFmpeg 就绪"):
                self._tr_static.append((widget, text))
                widget.configure(text=self.t(text))

        walk(self)

    def _refresh_language_controls(self) -> None:
        self.lang_btn.configure(text="EN" if self.lang == "zh" else "中文")
        self.menu_btn.configure(text=self.t("菜单") + "  v")
        self.header_ffmpeg.configure(text=self.t(self._header_ffmpeg_text()))
        self.footer_ffmpeg.configure(text=self.t(self._header_ffmpeg_text()))
        self._rebuild_menu()

    def _apply_language(self) -> None:
        for widget, source in self._tr_static:
            try:
                widget.configure(text=self.t(source))
            except tk.TclError:
                pass
        self._refresh_language_controls()
        self._relabel_monitors()
        self._refresh_audio_combo()
        self._update_monitor_note()
        self._update_audio_controls()
        self._update_preview()
        self._update_primary()
        self._update_disk_free()
        self._update_pause_button()
        self._refresh_footer()
        self._refresh_warnings()
        self._refresh_status_text()

    def _relabel_monitors(self) -> None:
        current = self._current_monitor()
        self._monitor_by_label = {self._monitor_label(m): m for m in self._monitor_options}
        values = list(self._monitor_by_label)
        self.monitor_combo.configure(values=values)
        if current is not None:
            for label, monitor in self._monitor_by_label.items():
                if monitor.device == current.device:
                    self.monitor_var.set(label)
                    break

    def _toggle_language(self) -> None:
        self.lang = "zh" if self.lang == "en" else "en"
        self.settings.language = self.lang
        self._save_settings()
        self._apply_language()

    def _refresh_status_text(self) -> None:
        state = self._engine.state
        if state == STATE_IDLE and not self._armed:
            if self._last_result:
                self._set_status_labels("已保存", "●  已保存", GREEN)
                self.timer_label.configure(text=format_hms(self._last_result.get("duration", 0)))
                self.detail_label.configure(text=self._result_detail(self._last_result))
            else:
                self._set_status_labels("待机", "●  待机", MUTED)
                self.timer_label.configure(text="00:00:00")
                self.detail_label.configure(text=self.t("设置开始和结束时间，或立即录制。"))
            self.auto_label.configure(text="")
            return
        if not self._armed:
            self._apply_engine_state(state)
        self._run_tick()

    def _set_status_labels(self, state_key: str, header_key: str, color: str) -> None:
        self._status_key = state_key
        self._header_key = header_key
        self.state_label.configure(text=self.t(state_key))
        self.header_state.configure(text=self.t(header_key), fg=color)
        self.state_dot.itemconfigure(self._dot_item, fill=color)

    def _result_detail(self, result: dict) -> str:
        audio = " + ".join(label_for_audio(a, self.lang) for a in (result.get("audio") or []))
        if not audio:
            audio = self.t("无声音")
        return (
            f"{result.get('width', 1280)}×{result.get('height', 720)} · "
            f"{audio} · {format_bytes(result.get('size', 0))}"
        )

    # --------------------------------------------------------------- settings
    def _apply_settings(self) -> None:
        settings = self.settings
        target = find_monitor(self._monitors, settings.monitor_device) if self._monitors else None
        if target is not None:
            for label, monitor in self._monitor_by_label.items():
                if monitor.device == target.device:
                    self.monitor_var.set(label)
                    break
        self.output_var.set(str(settings.resolved_output_dir))
        self.start_segment.set(settings.start_mode)
        self.end_segment.set(settings.end_mode)
        self.audio_segment.set(settings.audio_source)
        self._refresh_audio_combo()
        self._select_mic_by_name(settings.mic_device)
        self._update_audio_controls()
        self._update_mode_rows()
        self._set_controls_enabled(True)
        self._update_disk_free()
        if not self._ffmpeg_ready:
            self._set_footer("未找到 FFmpeg，无法录制。", ACCENT)
        else:
            self._set_footer("就绪 · 设置好后点击开始", MUTED)

    def _header_ffmpeg_text(self) -> str:
        if not self._ffmpeg_ready:
            return "FFmpeg 未就绪"
        version = self._ffmpeg_version
        if "version" in version:
            try:
                token = version.split("version", 1)[1].strip().split()[0]
                token = token.split("-")[0]
                return f"FFmpeg {token}"
            except Exception:
                pass
        return "FFmpeg 就绪"

    def _sync_settings_from_ui(self) -> AppSettings:
        settings = self.settings
        monitor = self._current_monitor()
        if monitor is not None:
            settings.monitor_device = monitor.device
        settings.audio_source = self.audio_segment.get()
        mic = self._current_mic()
        settings.mic_device = mic.name if mic else ""
        settings.capture_cursor = bool(self.cursor_var.get())
        settings.noise_reduction = bool(self.noise_var.get())
        settings.minimize_on_schedule = bool(self.minimize_var.get())
        settings.keep_awake = bool(self.keep_awake_var.get())
        settings.output_dir = self.output_var.get().strip()
        settings.prefix = safe_filename(self.prefix_var.get() or "Recording")
        settings.language = self.lang
        settings.start_mode = self.start_segment.get()
        settings.end_mode = self.end_segment.get()
        try:
            duration = parse_clock(self.duration_var.get())
            if 5 <= duration <= 24 * 3600 - 1:
                settings.duration_seconds = duration
        except ValueError:
            pass
        settings.window_geometry = self._last_geometry
        settings.next_rounds = [round_plan.to_dict() for round_plan in self._next_rounds]
        return settings

    def _save_settings(self) -> None:
        if not self._ui_ready and not self._closing:
            return
        try:
            self._sync_settings_from_ui().save()
        except Exception:
            _LOG.exception("Saving settings failed")

    # ---------------------------------------------------------- monitor/audio
    def _refresh_monitor_combo(self) -> None:
        try:
            self._monitors = list_monitors()
        except Exception:
            _LOG.exception("Monitor refresh failed")
        self._monitor_options = monitor_choices(self._monitors) if self._monitors else []
        self._monitor_by_label = {self._monitor_label(m): m for m in self._monitor_options}
        values = list(self._monitor_by_label)
        self.monitor_combo.configure(values=values)
        current = self.monitor_var.get()
        if current not in self._monitor_by_label:
            target = None
            if self.settings.monitor_device:
                target = find_monitor(self._monitors, self.settings.monitor_device)
            if target is None and self._monitors:
                target = find_monitor(self._monitors, "")
            for label, monitor in self._monitor_by_label.items():
                if target is not None and monitor.device == target.device:
                    self.monitor_var.set(label)
                    break
            else:
                if values:
                    self.monitor_var.set(values[0])
        self._update_monitor_note()

    def _update_monitor_note(self) -> None:
        monitor = self._current_monitor()
        if monitor is None:
            self.monitor_note.configure(text=self.t("未检测到显示器"))
            return
        self.monitor_note.configure(
            text=self.t(
                "源画面 {w}×{h}  →  缩放至 1280×720，保持比例，必要时自动加黑边",
                w=monitor.width,
                h=monitor.height,
            )
        )

    def _monitor_label(self, monitor: MonitorInfo) -> str:
        if monitor.device == "__all__":
            base = "全部显示器" if self.lang == "zh" else "All displays"
        else:
            base = f"显示器 {monitor.index + 1}" if self.lang == "zh" else f"Display {monitor.index + 1}"
        label = f"{base}  {monitor.width}×{monitor.height}"
        if monitor.primary:
            label += " · 主屏" if self.lang == "zh" else " · Main"
        return label

    def _current_monitor(self) -> MonitorInfo | None:
        label = self.monitor_var.get()
        if label in self._monitor_by_label:
            return self._monitor_by_label[label]
        if self._monitor_options:
            return self._monitor_options[0]
        return None

    def _mic_label(self, device) -> str:
        if device.is_default:
            return device.name + ("（默认）" if self.lang == "zh" else " (Default)")
        return device.name

    def _device_rate_text(self, device) -> str:
        return f"{device.sample_rate / 1000:.1f} kHz · {device.channels} ch"

    def _refresh_audio_combo(self) -> None:
        inv = self._audio_inventory
        self._mic_by_label = {self._mic_label(device): device for device in inv.microphones}
        values = list(self._mic_by_label) or ["未检测到麦克风"]
        self.mic_combo.configure(values=values)
        current = self._current_mic()
        if current is None:
            self.mic_var.set(values[0])
        else:
            for label, device in self._mic_by_label.items():
                if device.index == current.index:
                    self.mic_var.set(label)
                    break
            else:
                self.mic_var.set(values[0])
        has_system = inv.system is not None
        has_mic = bool(inv.microphones)
        self.audio_segment.set_option_enabled(AUDIO_SYSTEM, has_system)
        self.audio_segment.set_option_enabled(AUDIO_MIC, has_mic)
        self.audio_segment.set_option_enabled(AUDIO_BOTH, has_system and has_mic)
        self.audio_segment.set_option_enabled(AUDIO_NONE, True)
        source = self.audio_segment.get()
        if source == AUDIO_SYSTEM and not has_system:
            source = AUDIO_NONE
        elif source == AUDIO_MIC and not has_mic:
            source = AUDIO_SYSTEM if has_system else AUDIO_NONE
        elif source == AUDIO_BOTH and not (has_system and has_mic):
            source = AUDIO_SYSTEM if has_system else (AUDIO_MIC if has_mic else AUDIO_NONE)
        self.audio_segment.set(source)
        self.audio_var.set(source)
        self._update_audio_controls()

    def _refresh_audio_devices(self) -> None:
        if not self._controls_enabled:
            return
        self._set_footer("正在刷新音频设备…", MUTED)
        self.update_idletasks()
        try:
            self._audio_inventory = list_audio_devices()
        except Exception:
            _LOG.exception("Audio refresh failed")
            self._audio_inventory = AudioInventory(None, [], error="音频设备检测失败")
        self._refresh_audio_combo()
        self._set_footer("音频设备已刷新", MUTED)

    def _refresh_monitors(self) -> None:
        if not self._controls_enabled:
            return
        self._set_footer("正在刷新显示器…", MUTED)
        self.update_idletasks()
        self._refresh_monitor_combo()
        self._set_footer("显示器列表已刷新", MUTED)

    def _select_mic_by_name(self, name: str) -> None:
        mic = find_microphone(self._audio_inventory, name)
        if mic is None:
            return
        for label, device in self._mic_by_label.items():
            if device.index == mic.index and device.name == mic.name:
                self.mic_var.set(label)
                return
        if self._mic_by_label:
            self.mic_var.set(next(iter(self._mic_by_label)))

    def _current_mic(self):
        label = self.mic_var.get()
        if label in self._mic_by_label:
            return self._mic_by_label[label]
        if self._audio_inventory.default_mic is not None:
            return self._audio_inventory.default_mic
        return self._audio_inventory.microphones[0] if self._audio_inventory.microphones else None

    def _on_audio_source_changed(self, value: str) -> None:
        self.audio_var.set(value)
        self._update_audio_controls()
        self._update_preview()

    def _update_audio_controls(self) -> None:
        if not hasattr(self, "audio_segment"):
            return
        enabled = self._controls_enabled
        source = self.audio_segment.get()
        mic_needed = source in (AUDIO_MIC, AUDIO_BOTH)
        has_mic = bool(self._audio_inventory.microphones)
        self.audio_segment.set_enabled(enabled)
        self.audio_segment.set_option_enabled(AUDIO_SYSTEM, self._audio_inventory.system is not None)
        self.audio_segment.set_option_enabled(AUDIO_MIC, has_mic)
        self.audio_segment.set_option_enabled(
            AUDIO_BOTH, self._audio_inventory.system is not None and has_mic
        )
        self.audio_segment.set_option_enabled(AUDIO_NONE, True)
        if enabled and mic_needed and has_mic:
            self.mic_combo.configure(state="readonly")
            self.noise_check.configure(state="normal")
        else:
            self.mic_combo.configure(state="disabled")
            self.noise_check.configure(state="disabled")
        descriptions = {
            AUDIO_SYSTEM: "录制电脑正在播放的声音（WASAPI 回环），适合网课、会议和视频。",
            AUDIO_MIC: "录制所选麦克风，适合讲解和口播。",
            AUDIO_BOTH: "系统声音与麦克风会分别录制并自动混合。",
            AUDIO_NONE: "只录制画面，不包含任何声音。",
        }
        inv = self._audio_inventory
        if inv.error:
            self.audio_note.configure(text=self.t(inv.error), fg=ACCENT)
        elif not inv.system and not inv.microphones:
            self.audio_note.configure(text=self.t("未检测到可用的声音设备。"), fg=YELLOW)
        elif not inv.system:
            self.audio_note.configure(text=self.t("未检测到系统声音输出，可录制麦克风。"), fg=YELLOW)
        elif not inv.microphones:
            self.audio_note.configure(text=self.t("未检测到麦克风，可录制系统声音。"), fg=FAINT)
        else:
            self.audio_note.configure(text=self.t(descriptions.get(source, "")), fg=FAINT)
        source_labels = set()
        if source in (AUDIO_SYSTEM, AUDIO_BOTH):
            source_labels.add("系统声音")
        if source in (AUDIO_MIC, AUDIO_BOTH):
            source_labels.add("麦克风")
        for label in self.meter_canvases:
            self.meter_labels[label].configure(
                text=self.t(label), fg=TEXT if label in source_labels else FAINT
            )
        if self._engine.state != STATE_RECORDING:
            for label, canvas in self.meter_canvases.items():
                canvas.delete("all")
                self.meter_values[label].configure(text="—")

    # ------------------------------------------------------------- schedule UI
    def _update_mode_rows(self) -> None:
        if not hasattr(self, "start_when"):
            return
        if self.start_segment.get() == START_SCHEDULE:
            self.start_when.grid()
        else:
            self.start_when.grid_remove()
        end_mode = self.end_segment.get()
        if end_mode == END_DURATION:
            self.duration_row.grid()
            self.end_at_row.grid_remove()
        elif end_mode == END_AT:
            self.duration_row.grid_remove()
            self.end_at_row.grid()
        else:
            self.duration_row.grid_remove()
            self.end_at_row.grid_remove()
        self._update_preview()

    def _on_start_mode_changed(self, value: str) -> None:
        if value == START_SCHEDULE:
            try:
                start = parse_datetime(self.start_date_var.get(), self.start_time_var.get())
                if start <= datetime.now() + timedelta(seconds=2):
                    self._quick_start(5, update_only=True)
            except ValueError:
                self._quick_start(5, update_only=True)
        elif value == START_NOW and self.end_segment.get() == END_AT:
            now = datetime.now()
            try:
                end = parse_datetime(self.end_date_var.get(), self.end_time_var.get())
                if end <= now + timedelta(seconds=2):
                    self._set_end_from(now + timedelta(seconds=self._duration_seconds()))
            except ValueError:
                self._set_end_from(now + timedelta(seconds=self._duration_seconds()))
        self._update_mode_rows()
        self._update_primary()

    def _on_end_mode_changed(self, value: str) -> None:
        if value == END_AT:
            try:
                end = parse_datetime(self.end_date_var.get(), self.end_time_var.get())
                base = self._base_start_for_end()
                if end <= base + timedelta(seconds=2):
                    self._set_end_from(base + timedelta(seconds=self._duration_seconds()))
            except ValueError:
                base = self._base_start_for_end()
                self._set_end_from(base + timedelta(seconds=self._duration_seconds()))
        self._update_mode_rows()
        self._update_primary()

    def _base_start_for_end(self) -> datetime:
        if self.start_segment.get() == START_SCHEDULE:
            try:
                return parse_datetime(self.start_date_var.get(), self.start_time_var.get())
            except ValueError:
                return datetime.now()
        return datetime.now()

    def _duration_seconds(self) -> int:
        try:
            seconds = parse_clock(self.duration_var.get())
            return max(5, seconds)
        except ValueError:
            return 30 * 60

    def _set_end_from(self, value: datetime) -> None:
        self._updating = True
        try:
            self.end_date_var.set(value.strftime("%Y-%m-%d"))
            self.end_time_var.set(value.strftime("%H:%M:%S"))
        finally:
            self._updating = False

    def _quick_start(self, minutes: int, *, update_only: bool = False) -> None:
        target = (datetime.now() + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        if target <= datetime.now():
            target += timedelta(minutes=1)
        self._updating = True
        try:
            self.start_date_var.set(target.strftime("%Y-%m-%d"))
            self.start_time_var.set(target.strftime("%H:%M:%S"))
            if not update_only:
                self.start_segment.set(START_SCHEDULE)
                self._update_mode_rows()
        finally:
            self._updating = False
        self._update_preview()
        self._update_primary()

    def _quick_duration(self, minutes: int) -> None:
        self._updating = True
        try:
            self.end_segment.set(END_DURATION)
            self.duration_var.set(format_hms(minutes * 60))
        finally:
            self._updating = False
        self._update_mode_rows()
        self._update_primary()

    def _schedule_values(self) -> dict:
        now = datetime.now()
        start_mode = self.start_segment.get()
        if start_mode == START_SCHEDULE:
            start = parse_datetime(self.start_date_var.get(), self.start_time_var.get())
            if start <= now + timedelta(seconds=2):
                raise ValueError("开始时间需要晚于当前时间至少 3 秒。")
        else:
            start = now
        end_mode = self.end_segment.get()
        if end_mode == END_DURATION:
            seconds = parse_clock(self.duration_var.get())
            if seconds < 5:
                raise ValueError("录制时长至少为 5 秒。")
            if seconds > 24 * 3600 - 1:
                raise ValueError("录制时长不能超过 24 小时。")
            stop_at = start + timedelta(seconds=seconds)
        elif end_mode == END_AT:
            stop_at = parse_datetime(self.end_date_var.get(), self.end_time_var.get())
            if stop_at <= start + timedelta(seconds=2):
                raise ValueError("结束时间需要晚于开始时间。")
            if stop_at <= now:
                raise ValueError("结束时间已经过去，请重新设置。")
        else:
            stop_at = None
        return {
            "start": start,
            "stop_at": stop_at,
            "start_mode": start_mode,
            "end_mode": end_mode,
        }

    def _update_preview(self) -> None:
        if not self._ui_ready or not hasattr(self, "preview_label"):
            return
        try:
            data = self._schedule_values()
        except ValueError as exc:
            self.preview_label.configure(text=f"⚠  {self.t(str(exc))}", fg=YELLOW)
            if self._engine.state == STATE_IDLE and not self._armed:
                self._update_primary()
            return
        start = data["start"]
        text = (
            self.t("将于 {when} 开始，", when=self._fmt_dt(start))
            if data["start_mode"] == START_SCHEDULE
            else self.t("点击开始后立即录制，")
        )
        stop = data["stop_at"]
        if stop is None:
            text += self.t("需要手动停止。")
        elif data["end_mode"] == END_AT:
            text += self.t("于 {when} 自动结束。", when=self._fmt_dt(stop))
        elif data["start_mode"] == START_SCHEDULE:
            text += self.t(
                "录制 {duration} 后自动结束（约 {end}）。",
                duration=format_hms((stop - start).total_seconds()),
                end=stop.strftime("%H:%M:%S"),
            )
        else:
            text += self.t(
                "录制 {duration} 后自动结束。",
                duration=format_hms((stop - start).total_seconds()),
            )
        text += self.t(
            "\n画面 1280×720 · 30 FPS · {audio}",
            audio=self.t(AUDIO_LABELS.get(self.audio_segment.get(), "")),
        )
        text += self._rounds_summary()
        self.preview_label.configure(text=text, fg=MUTED)
        if self._engine.state == STATE_IDLE and not self._armed:
            self._update_primary()

    def _round_time(self, value: datetime) -> str:
        if value.date() == datetime.now().date():
            return value.strftime("%H:%M:%S")
        return self._fmt_dt(value, seconds=True)

    def _rounds_summary(self) -> str:
        if not self._next_rounds:
            return ""
        first = self._next_rounds[0]
        if len(self._next_rounds) == 1:
            return self.t(
                "\n下一轮：{start}–{end}",
                start=self._round_time(first.start),
                end=self._round_time(first.end),
            )
        return self.t(
            "\n下一轮：{start}–{end}（另有 {n} 轮）",
            start=self._round_time(first.start),
            end=self._round_time(first.end),
            n=len(self._next_rounds) - 1,
        )

    def _build_config(self, stop_at: datetime | None) -> RecordingConfig:
        monitor = self._current_monitor()
        if monitor is None:
            raise ValueError("没有可用的显示器。")
        raw_output = self.output_var.get().strip()
        if not raw_output:
            raise ValueError("请选择保存文件夹。")
        output = Path(os.path.expandvars(os.path.expanduser(raw_output)))
        try:
            output.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise ValueError(f"无法创建保存文件夹：{exc}") from exc
        mic = self._current_mic()
        return RecordingConfig(
            monitor=monitor,
            output_dir=output,
            prefix=safe_filename(self.prefix_var.get() or "Recording"),
            audio_source=self.audio_segment.get(),
            mic_device_name=mic.name if mic else "",
            capture_cursor=bool(self.cursor_var.get()),
            noise_reduction=bool(self.noise_var.get()),
            stop_at=stop_at,
        )

    # ------------------------------------------------------------- start/stop
    def _on_primary(self) -> None:
        if self._closing:
            return
        state = self._engine.state
        if self._armed:
            self._disarm("已取消定时录制")
            return
        if state in (STATE_RECORDING, STATE_PAUSED):
            self._set_footer("正在停止并保存 MP4…", YELLOW)
            self._engine.stop()
            return
        if state in (STATE_STARTING, STATE_PAUSING, STATE_RESUMING):
            self._set_footer("正在取消启动…", YELLOW)
            self._engine.stop()
            return
        if state == STATE_STOPPING:
            return
        if not self._ffmpeg_ready:
            messagebox.showerror(APP_NAME, self._ffmpeg_error, parent=self)
            return
        try:
            data = self._schedule_values()
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, localize_message(str(exc), self.lang), parent=self)
            return
        try:
            config = self._build_config(data["stop_at"])
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, localize_message(str(exc), self.lang), parent=self)
            return
        if data["start_mode"] == START_NOW:
            self._recording_was_scheduled = False
            self._reset_for_new_recording()
            self._active_stop_at = config.stop_at
            self._hide_result()
            self._save_settings()
            try:
                self._engine.start(config)
            except Exception as exc:
                messagebox.showerror(
                    APP_NAME, self.t("无法开始录制：{exc}", exc=exc), parent=self
                )
                return
            self._set_controls_enabled(False)
            self._set_footer("正在启动录屏…", YELLOW)
        else:
            self._scheduled_start = data["start"]
            self._scheduled_stop = data["stop_at"]
            self._scheduled_config = config
            self._recording_was_scheduled = True
            self._armed = True
            self._set_controls_enabled(False)
            self._update_primary()
            if self.keep_awake_var.get():
                set_sleep_block(True, keep_display=True)
            self._save_settings()
            self._set_footer(
                "已安排：{when} 开始", BLUE, when=self._fmt_dt(self._scheduled_start)
            )
            self._update_preview()

    def _reset_for_new_recording(self) -> None:
        self._last_result = None
        self._recording_started_at = None
        self._active_stop_at = None
        self._clear_warnings()

    def _begin_scheduled_recording(self, *, from_round: bool = False) -> None:
        if not self._armed or self._scheduled_config is None or self._scheduled_start is None:
            return
        now = datetime.now()
        if self._scheduled_stop is not None and now >= self._scheduled_stop:
            start = self._scheduled_start
            self._disarm("计划时间已过，未开始录制")
            messagebox.showwarning(
                APP_NAME,
                self.t("计划录制时间 {when} 已过，本次未开始录制。", when=self._fmt_dt(start)),
                parent=self,
            )
            return
        config = self._scheduled_config
        self._armed = False
        self._reset_for_new_recording()
        self._active_stop_at = config.stop_at
        self._recording_was_scheduled = True
        self._hide_result()
        self._scheduled_start = None
        self._scheduled_stop = None
        self._scheduled_config = None
        if self.keep_awake_var.get():
            set_sleep_block(True, keep_display=True)
        self._set_footer("定时时间已到，正在启动录屏…", YELLOW)
        try:
            self._engine.start(config)
        except Exception as exc:
            if self.keep_awake_var.get():
                set_sleep_block(False)
            self._set_controls_enabled(True)
            self._update_primary()
            messagebox.showerror(
                APP_NAME, self.t("无法开始定时录制：{exc}", exc=exc), parent=self
            )
            return
        self._set_controls_enabled(False)
        self._update_primary()

    def _disarm(self, message: str = "已取消定时录制") -> None:
        self._armed = False
        self._scheduled_start = None
        self._scheduled_stop = None
        self._scheduled_config = None
        if not self._engine.is_busy:
            set_sleep_block(False)
            self._set_controls_enabled(True)
        self._normalize_schedule_after_run()
        self._update_primary()
        self._update_preview()
        self._set_footer(message, MUTED)

    def _normalize_schedule_after_run(self) -> None:
        now = datetime.now()
        if self.start_segment.get() == START_SCHEDULE:
            try:
                start = parse_datetime(self.start_date_var.get(), self.start_time_var.get())
                if start <= now + timedelta(seconds=2):
                    self._quick_start(max(1, self.settings.start_offset_minutes), update_only=True)
            except ValueError:
                self._quick_start(max(1, self.settings.start_offset_minutes), update_only=True)
        if self.end_segment.get() == END_AT:
            base = self._base_start_for_end()
            try:
                end = parse_datetime(self.end_date_var.get(), self.end_time_var.get())
                if end <= base + timedelta(seconds=2):
                    self._set_end_from(base + timedelta(seconds=self._duration_seconds()))
            except ValueError:
                self._set_end_from(base + timedelta(seconds=self._duration_seconds()))
        self._update_preview()

    # ------------------------------------------------------------- pause/resume
    def _on_pause(self) -> None:
        state = self._engine.state
        if state == STATE_RECORDING:
            self._engine.pause()
            self._set_footer("正在暂停录制…", YELLOW)
            self.pause_btn.configure(text=self.t("暂停中…"), state="disabled")
        elif state == STATE_PAUSED:
            self._engine.resume()
            self._set_footer("正在继续录制…", YELLOW)
            self.pause_btn.configure(text=self.t("继续中…"), state="disabled")

    def _update_pause_button(self) -> None:
        state = self._engine.state
        if state in (STATE_RECORDING, STATE_PAUSED):
            if not self.pause_btn.winfo_manager():
                self.pause_btn.pack(fill="x", pady=(3, 2), before=self.warning_label)
            if state == STATE_RECORDING:
                self.pause_btn.configure(
                    text=self.t("暂停"), state="normal", bg=CARD2, fg=TEXT
                )
            else:
                self.pause_btn.configure(
                    text=self.t("继续"), state="normal", bg=CARD2, fg=GREEN
                )
        elif state in (STATE_PAUSING, STATE_RESUMING):
            if not self.pause_btn.winfo_manager():
                self.pause_btn.pack(fill="x", pady=(3, 2), before=self.warning_label)
            self.pause_btn.configure(
                text=self.t("暂停中…") if state == STATE_PAUSING else self.t("继续中…"),
                state="disabled",
                bg=CARD2,
                fg=FAINT,
            )
        else:
            if self.pause_btn.winfo_manager():
                self.pause_btn.pack_forget()
            self.pause_btn.configure(text=self.t("暂停"))

    # ------------------------------------------------------------------- state
    def _apply_engine_state(self, state: str) -> None:
        if self._closing:
            return
        if state == STATE_STARTING:
            self._set_status_labels("正在启动", "●  正在启动", YELLOW)
            self.timer_label.configure(text="—:—:—")
            self.detail_label.configure(text=self.t("正在准备画面和声音，请稍候…"))
            self._progress_start()
            self._set_controls_enabled(False)
        elif state == STATE_RECORDING:
            self._set_status_labels("录制中", "●  录制中", ACCENT)
            self._progress_stop()
        elif state == STATE_PAUSING:
            self._set_status_labels("正在暂停", "●  已暂停", YELLOW)
            self.detail_label.configure(text=self.t("暂停中…"))
            self._progress_start()
            self._set_controls_enabled(False)
        elif state == STATE_PAUSED:
            self._set_status_labels("已暂停", "●  已暂停", YELLOW)
            self._progress_stop()
            self._set_controls_enabled(False)
        elif state == STATE_RESUMING:
            self._set_status_labels("正在继续", "●  录制中", YELLOW)
            self.detail_label.configure(text=self.t("继续中…"))
            self._progress_start()
            self._set_controls_enabled(False)
        elif state == STATE_STOPPING:
            self._set_status_labels("正在保存", "●  正在保存", YELLOW)
            self.timer_label.configure(text=self.t("保存中…"))
            self.detail_label.configure(text=self.t("正在封装视频和音频，请不要关闭窗口…"))
            self._progress_start()
        else:
            self._set_status_labels("待机", "●  待机", MUTED)
            self.timer_label.configure(text="00:00:00")
            self.detail_label.configure(text=self.t("设置开始和结束时间，或立即录制。"))
            self._progress_stop()
            if not self._armed:
                self._set_controls_enabled(True)
        self._update_pause_button()
        self._update_primary()

    def _progress_start(self) -> None:
        if not self.progress_mode:
            if not self.progress.winfo_manager():
                self.progress.pack(fill="x", pady=(0, 1), before=self.warning_label)
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.progress_mode = True

    def _progress_stop(self) -> None:
        if self.progress_mode:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.progress_mode = False
        if self.progress.winfo_manager():
            self.progress.pack_forget()

    def _update_primary(self) -> None:
        if not hasattr(self, "primary_btn"):
            return
        if self._closing:
            self._set_primary("正在退出…", CARD2, "disabled")
            return
        if not self._ffmpeg_ready:
            self._set_primary("缺少 FFmpeg（查看诊断）", CARD2, "disabled")
            return
        state = self._engine.state
        if self._armed:
            self._set_primary("取消定时录制", CARD3, "normal")
        elif state == STATE_STARTING:
            self._set_primary("取消启动", CARD3, "normal")
        elif state in (STATE_RECORDING, STATE_PAUSED):
            self._set_primary("■  停止并保存", ACCENT, "normal")
        elif state == STATE_PAUSING:
            self._set_primary("暂停中…", CARD2, "disabled")
        elif state == STATE_RESUMING:
            self._set_primary("继续中…", CARD2, "disabled")
        elif state == STATE_STOPPING:
            self._set_primary("正在生成 MP4…", CARD2, "disabled")
        else:
            if self.start_segment.get() == START_SCHEDULE:
                self._set_primary("⏱  安排定时录制", ACCENT, "normal")
            else:
                self._set_primary("●  立即开始录制", ACCENT, "normal")

    def _set_primary(self, text: str, bg: str, state: str) -> None:
        self.primary_btn.configure(
            text=self.t(text),
            bg=bg,
            activebackground=ACCENT_DARK if bg == ACCENT else CARD3,
            state=state,
            cursor="hand2" if state == "normal" else "arrow",
        )

    # ---------------------------------------------------------------- controls
    def _set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = enabled
        normal_widgets = [
            self.cursor_check,
            self.minimize_check,
            self.keep_awake_check,
            self.noise_check,
            self.output_entry,
            self.prefix_entry,
            self.start_date_entry,
            self.start_time_entry,
            self.end_date_entry,
            self.end_time_entry,
            self.duration_entry,
        ]
        normal_widgets.extend(self.start_quick_buttons)
        normal_widgets.extend(self.duration_quick_buttons)
        button_widgets = [self.monitor_refresh_btn, self.audio_refresh_btn, self.browse_btn]
        for widget in normal_widgets:
            try:
                widget.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass
        for widget in button_widgets:
            try:
                widget.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass
        try:
            self.monitor_combo.configure(state="readonly" if enabled else "disabled")
        except tk.TclError:
            pass
        self.start_segment.set_enabled(enabled)
        self.end_segment.set_enabled(enabled)
        self._update_audio_controls()
        self._update_primary()

    def _hide_result(self, keep: bool = False) -> None:
        if not keep:
            self.result_frame.pack_forget()
            self.after(50, lambda: self._body_canvas.yview_moveto(0.0))

    # ------------------------------------------------------------------- ticks
    def _tick(self) -> None:
        if not self._alive:
            return
        try:
            self._run_tick()
        except tk.TclError:
            return
        except Exception:
            _LOG.exception("UI tick failed")
        self.after(200, self._tick)

    def _run_tick(self) -> None:
        now = datetime.now()
        state = self._engine.state
        if self._armed and self._scheduled_start is not None:
            remaining = (self._scheduled_start - now).total_seconds()
            if remaining <= 0:
                if self._scheduled_stop is not None and now >= self._scheduled_stop:
                    start = self._scheduled_start
                    self._disarm("计划时间已过，未开始录制")
                    messagebox.showwarning(
                        APP_NAME,
                        self.t("计划录制时间 {when} 已过，本次未开始录制。", when=self._fmt_dt(start)),
                        parent=self,
                    )
                else:
                    self._begin_scheduled_recording()
            else:
                self._set_status_labels("等待定时开始", "●  已安排", BLUE)
                self.timer_label.configure(text=format_hms(remaining + 0.999))
                detail = self.t("将在 {when} 自动开始", when=self._fmt_dt(self._scheduled_start))
                if self._scheduled_stop:
                    detail += self.t("，{when} 自动结束", when=self._fmt_dt(self._scheduled_stop))
                self.detail_label.configure(text=detail)
                self.auto_label.configure(
                    text=self.t("至 {time}", time=self._scheduled_stop.strftime("%H:%M:%S"))
                    if self._scheduled_stop
                    else ""
                )
                self._progress_stop()
                self._set_controls_enabled(False)
                self._update_primary()
        elif state == STATE_RECORDING:
            elapsed = self._engine.snapshot().get("elapsed", 0.0)
            self.timer_label.configure(text=format_hms(elapsed))
            self._set_status_labels("录制中", "●  录制中", ACCENT)
            if self._active_stop_at is not None:
                remain = max(0, int((self._active_stop_at - now).total_seconds()))
                detail = self.t(
                    "自动结束 {time}  ·  剩余 {remaining}",
                    time=self._active_stop_at.strftime("%H:%M:%S"),
                    remaining=format_hms(remain),
                )
                self.auto_label.configure(
                    text=self.t("至 {time}", time=self._active_stop_at.strftime("%H:%M:%S"))
                )
            else:
                detail = self.t("手动停止 · 点击上方按钮或按 F9 保存")
                self.auto_label.configure(text=self.t("手动"))
            self.detail_label.configure(text=detail)
            self._update_levels()
            self._progress_stop()
        elif state == STATE_PAUSED:
            elapsed = self._engine.snapshot().get("elapsed", 0.0)
            self.timer_label.configure(text=format_hms(elapsed))
            self._set_status_labels("已暂停", "●  已暂停", YELLOW)
            detail = self.t("已暂停 · 点击继续或停止并保存")
            if self._active_stop_at is not None:
                detail += " · " + self.t(
                    "至 {time}", time=self._active_stop_at.strftime("%H:%M:%S")
                )
            self.detail_label.configure(text=detail)
            self.auto_label.configure(text=self.t("已暂停"))
            self._clear_levels()
            self._progress_stop()
        elif state == STATE_PAUSING:
            self.timer_label.configure(text="—:—:—")
            self.detail_label.configure(text=self.t("暂停中…"))
        elif state == STATE_RESUMING:
            self.timer_label.configure(text="—:—:—")
            self.detail_label.configure(text=self.t("继续中…"))
        elif state == STATE_STOPPING:
            self.timer_label.configure(text=self.t("保存中…"))
        elif not self._armed:
            if self._last_result:
                self._set_status_labels("已保存", "●  已保存", GREEN)
                self.timer_label.configure(text=format_hms(self._last_result.get("duration", 0)))
                self.detail_label.configure(text=self._result_detail(self._last_result))
                self.auto_label.configure(text="")
            else:
                self._set_status_labels("待机", "●  待机", MUTED)
                self.timer_label.configure(text="00:00:00")
                self.auto_label.configure(text="")
        if now.timestamp() - self._last_disk_check > 10:
            self._last_disk_check = now.timestamp()
            self._update_disk_free()

    def _update_levels(self) -> None:
        if not hasattr(self, "meter_canvases"):
            return
        levels = self._engine.get_levels() if self._engine.state == STATE_RECORDING else {}
        source = self.audio_segment.get()
        active_labels = set()
        if source in (AUDIO_SYSTEM, AUDIO_BOTH):
            active_labels.add("系统声音")
        if source in (AUDIO_MIC, AUDIO_BOTH):
            active_labels.add("麦克风")
        for label, canvas in self.meter_canvases.items():
            db = levels.get(label)
            if db is None or label not in active_labels:
                canvas.delete("all")
                if not levels:
                    self.meter_values[label].configure(text="—")
                continue
            width = max(10, canvas.winfo_width())
            height = max(6, canvas.winfo_height())
            fraction = max(0.0, min(1.0, (db + 60.0) / 60.0))
            canvas.delete("all")
            fill = BLUE if label == "系统声音" else GREEN
            canvas.create_rectangle(0, 0, max(2, int(width * fraction)), height, fill=fill, outline="")
            self.meter_values[label].configure(text=f"{db:.0f} dB" if db > -59.5 else "—")

    def _clear_levels(self) -> None:
        for label, canvas in self.meter_canvases.items():
            canvas.delete("all")
            self.meter_values[label].configure(text="—")

    # ------------------------------------------------------------------ events
    def _poll_events(self) -> None:
        if not self._alive:
            return
        try:
            for _ in range(80):
                event = self._engine.events.get_nowait()
                self._handle_engine_event(event)
                if not self._alive:
                    return
        except queue.Empty:
            pass
        except Exception:
            _LOG.exception("Event handling failed")
        self.after(100, self._poll_events)

    def _handle_engine_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "state":
            state = event.get("state", STATE_IDLE)
            self._apply_engine_state(state)
        elif kind == "started":
            try:
                self._recording_started_at = datetime.fromisoformat(event["started_at"])
            except Exception:
                self._recording_started_at = datetime.now()
            stop_at = event.get("stop_at")
            self._active_stop_at = datetime.fromisoformat(stop_at) if stop_at else None
            monitor = self._monitor_label(self._current_monitor()) if self._current_monitor() else ""
            audio = " + ".join(
                label_for_audio(a, self.lang) for a in (event.get("audio") or [])
            ) or self.t("无声音")
            self.detail_label.configure(
                text=f"{monitor}\n{audio} · 1280×720 · 30 FPS\n"
                + self.t("自动结束：")
                + (
                    self._active_stop_at.strftime("%H:%M:%S")
                    if self._active_stop_at
                    else self.t("手动")
                )
            )
            self._set_status_labels("录制中", "●  录制中", ACCENT)
            self._update_pause_button()
            self._progress_stop()
            self._set_footer("录制中 · 按 F9 停止，F10 暂停", ACCENT)
            for warning in event.get("warnings") or []:
                self._append_warning(warning)
            if self._recording_was_scheduled and self.minimize_var.get():
                self.after(700, self._maybe_minimize)
        elif kind == "paused":
            self._set_status_labels("已暂停", "●  已暂停", YELLOW)
            self.detail_label.configure(text=self.t("已暂停 · 点击继续或停止并保存"))
            self._update_pause_button()
            self._progress_stop()
            self._clear_levels()
            self._set_footer("已暂停 · 计时已冻结", YELLOW)
        elif kind == "resumed":
            self._set_status_labels("录制中", "●  录制中", ACCENT)
            self._update_pause_button()
            self._progress_stop()
            self._set_footer("录制中 · 按 F9 停止，F10 暂停", ACCENT)
        elif kind == "warning":
            self._append_warning(event.get("message", ""))
        elif kind == "auto_stopping":
            self._set_status_labels("正在自动结束", "●  正在保存", YELLOW)
            self.timer_label.configure(text=self.t("保存中…"))
            self.detail_label.configure(text=self.t("已到达自动结束时间，正在封装 MP4…"))
            self._progress_start()
        elif kind == "finalized":
            self._on_finalized(event)
        elif kind == "error":
            self._on_error(event)

    def _on_finalized(self, event: dict) -> None:
        self._last_result = event
        self._recording_started_at = None
        self._active_stop_at = None
        self._progress_stop()
        self._set_controls_enabled(True)
        self._normalize_schedule_after_run()
        self._set_status_labels("已保存", "●  已保存", GREEN)
        self.timer_label.configure(text=format_hms(event.get("duration", 0)))
        self.detail_label.configure(text=self._result_detail(event))
        self.auto_label.configure(text="")
        path = Path(event.get("path", ""))
        self.result_path.configure(text=str(path))
        self.result_frame.pack(fill="x")
        self.after(120, lambda: self._body_canvas.yview_moveto(1.0))
        self._set_footer("已保存：{name}", GREEN, name=path.name)
        if self.keep_awake_var.get():
            set_sleep_block(False)
        self._update_pause_button()
        self._update_primary()
        self._save_settings()
        try:
            self.bell()
        except Exception:
            pass
        if self._closing:
            self._finish_close()
            return
        if self._next_rounds:
            self.after(700, self._activate_next_round)

    def _on_error(self, event: dict) -> None:
        message = localize_message(event.get("message", "未知错误"), self.lang)
        _LOG.error("Recording error: %s", message)
        self._progress_stop()
        self._recording_started_at = None
        self._active_stop_at = None
        self._last_result = None
        self._set_controls_enabled(True)
        self._normalize_schedule_after_run()
        self._set_status_labels("录制失败", "●  录制失败", ACCENT)
        self.timer_label.configure(text="—:—:—")
        session = event.get("session_dir")
        detail = message
        if session:
            detail += "\n" + self.t("临时文件：{path}", path=session)
        self.detail_label.configure(text=detail)
        self._set_footer("录制失败，请查看诊断信息。", ACCENT)
        if self.keep_awake_var.get():
            set_sleep_block(False)
        self._update_pause_button()
        self._update_primary()
        if not self._closing:
            self.deiconify()
            self.lift()
            messagebox.showerror(APP_NAME, message, parent=self)
        if self._closing:
            self._finish_close()

    def _append_warning(self, message: str) -> None:
        if not message:
            return
        if message not in self._warning_sources:
            self._warning_sources.append(message)
        self._refresh_warnings()
        self._set_footer(localize_message(message, self.lang), YELLOW)

    def _clear_warnings(self) -> None:
        self._warning_sources = []
        self.warning_label.configure(text="")

    def _refresh_warnings(self) -> None:
        lines = [f"⚠  {localize_message(m, self.lang)}" for m in self._warning_sources[-3:]]
        self.warning_label.configure(text="\n".join(lines))

    # ------------------------------------------------------------------ rounds
    def _open_add_round(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self.t("添加下一轮"))
        dialog.configure(bg=BG)
        dialog.attributes("-alpha", 1.0)
        dialog.transient(self)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=18)
        tk.Label(
            body,
            text=self.t("添加下一轮"),
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text=self.t("设置下一轮的自动开始和自动结束时间。当前录制结束后会自动接续。"),
            bg=BG,
            fg=MUTED,
            font=FONT_SMALL,
            wraplength=390,
            justify="left",
        ).pack(anchor="w", pady=(5, 12))
        defaults = self._round_defaults()
        start_date = tk.StringVar(value=defaults[0].strftime("%Y-%m-%d"))
        start_time = tk.StringVar(value=defaults[0].strftime("%H:%M:%S"))
        end_date = tk.StringVar(value=defaults[1].strftime("%Y-%m-%d"))
        end_time = tk.StringVar(value=defaults[1].strftime("%H:%M:%S"))
        fields = tk.Frame(body, bg=BG)
        fields.pack(fill="x")
        self._dialog_date_row(fields, "下一轮开始", start_date, start_time)
        self._dialog_date_row(fields, "下一轮结束", end_date, end_time)
        if self._next_rounds:
            tk.Label(
                body,
                text=self.t("已排队 {n} 轮", n=len(self._next_rounds)),
                bg=BG,
                fg=FAINT,
                font=FONT_TINY,
            ).pack(anchor="w", pady=(8, 0))
        error_label = tk.Label(body, text="", bg=BG, fg=ACCENT, font=FONT_TINY, wraplength=390)
        error_label.pack(anchor="w", pady=(8, 0))
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(14, 0))

        def add() -> None:
            try:
                start = parse_datetime(start_date.get(), start_time.get())
                end = parse_datetime(end_date.get(), end_time.get())
                self._validate_round(start, end)
            except ValueError as exc:
                error_label.configure(text=self.t(str(exc)))
                return
            self._next_rounds.append(RoundPlan(start=start, end=end))
            self._save_settings()
            self._update_preview()
            dialog.destroy()
            if self._armed or self._engine.state in (STATE_RECORDING, STATE_PAUSED):
                self._set_footer(
                    "已添加下一轮：{start} → {end}",
                    GREEN,
                    start=self._fmt_dt(start, seconds=False),
                    end=self._fmt_dt(end, seconds=False),
                )
            elif self._engine.state == STATE_IDLE:
                try:
                    self._schedule_values()
                except ValueError:
                    self._set_footer(
                        "已添加下一轮；请先安排当前任务，之后会自动接续。",
                        GREEN,
                    )
                else:
                    # The form is task #1: arm it now, without disturbing the
                    # queued round that was just appended.
                    self._set_footer(
                        "已添加下一轮；当前任务将先开始，随后自动接续。",
                        GREEN,
                    )
                    self.after(100, self._on_primary)

        def clear_all() -> None:
            if not self._next_rounds:
                return
            if not messagebox.askyesno(APP_NAME, self.t("确定清空全部下一轮设置吗？"), parent=dialog):
                return
            self._next_rounds.clear()
            self._save_settings()
            self._update_preview()
            dialog.destroy()
            self._set_footer("已清空下一轮设置。", MUTED)

        self._dialog_button(buttons, "添加", add, accent=True).pack(side="left")
        self._dialog_button(buttons, "取消", dialog.destroy).pack(side="left", padx=(6, 0))
        if self._next_rounds:
            self._dialog_button(buttons, "清空全部下一轮", clear_all).pack(side="right")
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + 100
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.focus_force()

    def _round_defaults(self) -> tuple[datetime, datetime]:
        base = datetime.now() + timedelta(minutes=10)
        if self._next_rounds:
            base = max(base, self._next_rounds[-1].end + timedelta(minutes=10))
        current_end = self._expected_current_end()
        if current_end is not None:
            base = max(base, current_end + timedelta(minutes=10))
        base = base.replace(second=0, microsecond=0)
        return base, base + timedelta(seconds=self._duration_seconds())

    def _expected_current_end(self) -> datetime | None:
        if self._engine.state in (STATE_RECORDING, STATE_PAUSED, STATE_PAUSING, STATE_RESUMING):
            return self._active_stop_at
        if self._armed:
            return self._scheduled_stop
        # The schedule form itself is the first task until it is armed.  Use its
        # end time so a "+" round is queued after the first task instead of
        # silently replacing it.
        try:
            return self._schedule_values()["stop_at"]
        except ValueError:
            return None

    def _validate_round(self, start: datetime, end: datetime) -> None:
        now = datetime.now()
        if start <= now + timedelta(seconds=2):
            raise ValueError("下一轮开始时间需要晚于当前时间。")
        if end < start + timedelta(seconds=5):
            raise ValueError("下一轮结束时间需要晚于开始时间。")
        current_end = self._expected_current_end()
        if current_end is not None and start <= current_end:
            raise ValueError("下一轮开始时间需要晚于当前录制结束时间。")
        if self._next_rounds and start <= self._next_rounds[-1].end:
            raise ValueError("下一轮开始时间需要晚于上一轮结束时间。")

    def _activate_next_round(self) -> bool:
        if self._closing or self._armed or self._engine.is_busy:
            return False
        now = datetime.now()
        while self._next_rounds:
            plan = self._next_rounds.pop(0)
            if plan.end <= now:
                continue
            try:
                config = self._build_config(plan.end)
            except ValueError as exc:
                self._next_rounds.insert(0, plan)
                messagebox.showwarning(APP_NAME, localize_message(str(exc), self.lang), parent=self)
                self._save_settings()
                return False
            self._scheduled_start = plan.start
            self._scheduled_stop = plan.end
            self._scheduled_config = config
            self._recording_was_scheduled = True
            self._armed = True
            self._updating = True
            try:
                self.start_segment.set(START_SCHEDULE)
                self.start_date_var.set(plan.start.strftime("%Y-%m-%d"))
                self.start_time_var.set(plan.start.strftime("%H:%M:%S"))
                self.end_segment.set(END_AT)
                self.end_date_var.set(plan.end.strftime("%Y-%m-%d"))
                self.end_time_var.set(plan.end.strftime("%H:%M:%S"))
            finally:
                self._updating = False
            self._update_mode_rows()
            self._set_controls_enabled(False)
            self._update_primary()
            self._update_preview()
            self._save_settings()
            if self.keep_awake_var.get():
                set_sleep_block(True, keep_display=True)
            self._set_footer("已安排：{when} 开始", BLUE, when=self._fmt_dt(plan.start))
            if plan.start <= now:
                self._begin_scheduled_recording(from_round=True)
            return True
        self._save_settings()
        self._update_preview()
        return False

    def _dialog_date_row(self, parent, label: str, date_var, time_var) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=self.t(label), bg=BG, fg=MUTED, font=FONT_SMALL, width=15, anchor="w").pack(
            side="left"
        )
        entry = tk.Entry(
            row,
            textvariable=date_var,
            width=12,
            bg=CARD2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=FONT_SMALL,
        )
        entry.pack(side="left")
        tk.Entry(
            row,
            textvariable=time_var,
            width=10,
            bg=CARD2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=FONT_SMALL,
        ).pack(side="left", padx=(7, 0))

    def _dialog_button(self, parent, text: str, command, *, accent: bool = False) -> tk.Button:
        return tk.Button(
            parent,
            text=self.t(text),
            command=command,
            bg=ACCENT if accent else CARD2,
            fg="#ffffff" if accent else MUTED,
            activebackground=ACCENT_DARK if accent else CARD3,
            activeforeground="#ffffff" if accent else TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_SMALL,
            padx=12,
            pady=6,
            cursor="hand2",
        )

    # ------------------------------------------------------------------- menu
    def _rebuild_menu(self) -> None:
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=CARD,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="#ffffff",
            bd=0,
            font=FONT_SMALL,
        )
        menu.add_command(label=self.t("联系作者"), command=self._show_contact)
        menu.add_command(label=self.t("捐赠"), command=self._show_donate)
        self._main_menu = menu

    def _show_main_menu(self) -> None:
        try:
            x = self.menu_btn.winfo_rootx()
            y = self.menu_btn.winfo_rooty() + self.menu_btn.winfo_height()
            self._main_menu.tk_popup(x, y)
        finally:
            try:
                self._main_menu.grab_release()
            except Exception:
                pass

    def _show_contact(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self.t("联系作者 · RecordOnTime"))
        dialog.configure(bg=BG)
        dialog.attributes("-alpha", 1.0)
        dialog.transient(self)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(
            body, text=self.t("联系作者"), bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 13, "bold")
        ).pack(anchor="w")
        tk.Label(
            body, text=self.t("作者邮箱："), bg=BG, fg=MUTED, font=FONT_SMALL
        ).pack(anchor="w", pady=(12, 3))
        email_box = tk.Frame(body, bg=CARD2, highlightbackground=BORDER, highlightthickness=1)
        email_box.pack(fill="x")
        tk.Label(
            email_box,
            text="linan138@gmail.com",
            bg=CARD2,
            fg=TEXT,
            font=("Consolas", 11),
            anchor="w",
            padx=9,
            pady=7,
        ).pack(fill="x")

        def copy_email() -> None:
            self.clipboard_clear()
            self.clipboard_append("linan138@gmail.com")
            self.update()
            self._set_footer("已复制邮箱地址。", GREEN)

        def send_email() -> None:
            try:
                webbrowser.open("mailto:linan138@gmail.com")
            except Exception:
                pass

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(16, 0))
        self._dialog_button(buttons, "复制邮箱", copy_email, accent=True).pack(side="left")
        self._dialog_button(buttons, "发送邮件", send_email).pack(side="left", padx=(6, 0))
        self._dialog_button(buttons, "关闭", dialog.destroy).pack(side="right")
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + 130
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.focus_force()

    def _show_donate(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self.t("支持 RecordOnTime"))
        dialog.configure(bg=BG)
        dialog.attributes("-alpha", 1.0)
        dialog.transient(self)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)
        tk.Label(
            body, text=self.t("支持 RecordOnTime"), bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 13, "bold")
        ).pack(anchor="w")
        tk.Label(
            body,
            text=self.t("如果这个工具帮到了你，欢迎请作者喝杯咖啡。"),
            bg=BG,
            fg=MUTED,
            font=FONT_SMALL,
            wraplength=390,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        image_path = resource_path("assets/alipay.png")
        try:
            self._donate_image = tk.PhotoImage(file=str(image_path))
            tk.Label(body, image=self._donate_image, bg=BG).pack(pady=(2, 4))
        except Exception:
            tk.Label(body, text=self.t("二维码图片缺失。"), bg=BG, fg=YELLOW, font=FONT_SMALL).pack(
                pady=20
            )
        tk.Label(
            body, text=self.t("使用支付宝扫码捐赠"), bg=BG, fg=MUTED, font=FONT_SMALL
        ).pack(anchor="center", pady=(2, 8))
        self._dialog_button(body, "关闭", dialog.destroy).pack(anchor="center")
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + 40
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.focus_force()

    # ------------------------------------------------------------------- close
    def _on_close(self) -> None:
        if self._engine.is_busy:
            if not messagebox.askyesno(
                APP_NAME,
                self.t("当前正在录制。\n\n停止录制、保存 MP4 后退出吗？"),
                parent=self,
            ):
                return
            self._closing = True
            self._save_settings()
            self._set_controls_enabled(False)
            self._update_primary()
            self._set_footer("正在停止并保存，完成后将自动退出…", YELLOW)
            self._set_status_labels("正在保存并退出", "●  正在保存", YELLOW)
            self.timer_label.configure(text=self.t("保存中…"))
            self._progress_start()
            self._engine.stop()
            self._close_deadline_id = self.after(120000, self._force_close)
            return
        if self._armed:
            if not messagebox.askyesno(APP_NAME, self.t("定时录制尚未开始，取消计划并退出吗？"), parent=self):
                return
            self._disarm()
        self._exit_now()

    def _finish_close(self) -> None:
        if not self._alive:
            return
        if self._close_deadline_id is not None:
            try:
                self.after_cancel(self._close_deadline_id)
            except Exception:
                pass
            self._close_deadline_id = None
        self._exit_now()

    def _force_close(self) -> None:
        if not self._alive or not self._closing:
            return
        _LOG.warning("Close timeout reached; exiting while finalisation may still run")
        try:
            self._engine.close(timeout=2.0)
        except Exception:
            pass
        self._exit_now()

    def _exit_now(self) -> None:
        if not self._alive:
            return
        self._alive = False
        try:
            self._save_settings()
        except Exception:
            pass
        try:
            set_sleep_block(False)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ misc
    def _browse_output(self) -> None:
        current = Path(os.path.expandvars(os.path.expanduser(self.output_var.get().strip() or "")))
        initial = current if current.exists() else Path.home()
        chosen = filedialog.askdirectory(
            parent=self,
            title=self.t("选择保存文件夹"),
            initialdir=str(initial),
            mustexist=False,
        )
        if chosen:
            self.output_var.set(str(Path(chosen)))
            self._update_disk_free()

    def _update_disk_free(self) -> None:
        path = Path(os.path.expandvars(os.path.expanduser(self.output_var.get().strip() or ".")))
        try:
            free = shutil.disk_usage(path if path.exists() else path.parent).free
            self.disk_label.configure(
                text=self.t("可用 {size}", size=format_bytes(free)),
                fg=YELLOW if free < 1024**3 else FAINT,
            )
        except Exception:
            self.disk_label.configure(text=self.t("文件夹不可用"), fg=YELLOW)

    def _open_output_folder(self) -> None:
        path = Path(os.path.expandvars(os.path.expanduser(self.output_var.get().strip() or ".")))
        try:
            path.mkdir(parents=True, exist_ok=True)
            open_folder(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, self.t("无法打开文件夹：{exc}", exc=exc), parent=self)

    def _open_result_folder(self) -> None:
        if self._last_result:
            path = Path(self._last_result.get("path", ""))
            try:
                open_folder(path.parent if path.exists() else path.parent)
            except Exception as exc:
                messagebox.showerror(APP_NAME, self.t("无法打开文件夹：{exc}", exc=exc), parent=self)

    def _play_result(self) -> None:
        if self._last_result:
            path = Path(self._last_result.get("path", ""))
            if path.exists():
                play_file(path)
            else:
                messagebox.showwarning(APP_NAME, self.t("视频文件不存在。"), parent=self)

    def _set_footer(self, text: str, color: str = MUTED, **kwargs) -> None:
        self._footer_source = (text, color, kwargs)
        self.footer_status.configure(text=self.t(text, **kwargs), fg=color)

    def _refresh_footer(self) -> None:
        if self._footer_source:
            text, color, kwargs = self._footer_source
            self.footer_status.configure(text=self.t(text, **kwargs), fg=color)
        else:
            self.footer_status.configure(text=self.t("就绪"), fg=MUTED)

    def _on_wheel(self, event) -> None:
        if not self._alive:
            return
        if self._pointer_in_body():
            delta = -1 if event.delta > 0 else 1
            self._body_canvas.yview_scroll(delta * 2, "units")

    def _pointer_in_body(self) -> bool:
        try:
            widget = self.winfo_containing(*self.winfo_pointerxy())
        except Exception:
            return False
        while widget is not None:
            if widget is self._body_outer:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _startup_notice(self) -> None:
        if not self._ffmpeg_ready and self._alive:
            messagebox.showerror(APP_NAME, self._ffmpeg_error, parent=self)
        if self._audio_inventory.error:
            self._append_warning(self._audio_inventory.error)

    def _show_diagnostics(self) -> None:
        window = tk.Toplevel(self)
        window.title(self.t("诊断信息"))
        window.configure(bg=BG)
        window.attributes("-alpha", 1.0)
        window.geometry("780x560")
        window.transient(self)
        text = tk.Text(
            window,
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=16,
            pady=14,
            font=("Consolas", 10),
            wrap="word",
        )
        text.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        lines = [
            f"{APP_NAME} v{APP_VERSION}",
            f"Python: {sys.version.split()[0]}  ·  {sys.platform}",
            f"{self.t('程序目录')}: {resource_path('.')}",
            "",
            f"FFmpeg: {self._ffmpeg_path or self.t('未找到')}",
            f"{self.t('版本')}: {self._ffmpeg_version}",
            f"{self.t('错误')}: {self._ffmpeg_error or self.t('未设置')}",
            "",
            f"{self.t('显示器')}:",
        ]
        for monitor in self._monitors:
            lines.append(
                f"  [{monitor.index}] {self._monitor_label(monitor)}  设备={monitor.device}  "
                f"区域=({monitor.left},{monitor.top}) {monitor.width}x{monitor.height}"
            )
        lines.extend(["", f"{self.t('音频设备')}:"])
        if self._audio_inventory.system:
            dev = self._audio_inventory.system
            lines.append(f"  {self.t('系统回环')}: [{dev.index}] {dev.name}  {self._device_rate_text(dev)}")
        else:
            lines.append(f"  {self.t('系统回环')}: {self.t('未找到')}")
        for dev in self._audio_inventory.microphones:
            lines.append(
                f"  {self.t('麦克风')}: [{dev.index}] {self._mic_label(dev)}  {self._device_rate_text(dev)}"
                f"  {self.t('默认') if dev.is_default else ''}"
            )
        lines.extend(
            [
                "",
                f"{self.t('设置目录')}: {data_dir()}",
                f"{self.t('日志目录')}: {log_dir()}",
                f"{self.t('保存目录')}: {self.output_var.get()}",
                f"{self.t('引擎状态')}: {self._engine.state}",
                f"{self.t('定时开始')}: {self._scheduled_start or self.t('未安排')}",
                f"{self.t('自动结束')}: {self._active_stop_at or self._scheduled_stop or self.t('未设置')}",
                "",
                self.t("若录制失败，请把以上信息连同日志目录中的文件一起反馈。"),
            ]
        )
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        bar = tk.Frame(window, bg=BG)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        copy_btn = self._small_button(
            bar,
            "复制到剪贴板",
            lambda: (
                self.clipboard_clear(),
                self.clipboard_append("\n".join(lines)),
                self.update(),
            ),
        )
        copy_btn.pack(side="left")
        close_btn = self._small_button(bar, "关闭", window.destroy)
        close_btn.pack(side="right")

    def _bind_events(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F9>", self._on_hotkey)
        self.bind("<F10>", self._on_pause_hotkey)
        self.bind("<Escape>", self._on_escape)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", self._on_configure)
        for var in (
            self.start_date_var,
            self.start_time_var,
            self.end_date_var,
            self.end_time_var,
            self.duration_var,
        ):
            var.trace_add("write", lambda *_: self._on_schedule_field_changed())

    def _on_schedule_field_changed(self) -> None:
        if self._updating or not self._ui_ready:
            return
        self._update_preview()

    def _on_hotkey(self, _event=None):
        if self._closing:
            return
        state = self._engine.state
        if state in (STATE_RECORDING, STATE_PAUSED):
            self._engine.stop()
            return
        if state in (STATE_STARTING, STATE_PAUSING, STATE_RESUMING):
            self._engine.stop()
            return
        if state == STATE_IDLE and not self._armed:
            self._on_primary()
        return "break"

    def _on_pause_hotkey(self, _event=None):
        if self._closing:
            return "break"
        if self._engine.state in (STATE_RECORDING, STATE_PAUSED):
            self._on_pause()
        return "break"

    def _on_escape(self, _event=None):
        if self._armed and not self._closing:
            self._disarm()
        return "break"

    def _on_content_configure(self, _event=None) -> None:
        if not hasattr(self, "_body_canvas"):
            return
        self._body_canvas.configure(scrollregion=self._body_canvas.bbox("all"))
        self._update_scrollbar()

    def _on_canvas_configure(self, event) -> None:
        self._body_canvas.itemconfigure(self._body_window, width=event.width)
        self._update_scrollbar()

    def _update_scrollbar(self) -> None:
        if not hasattr(self, "_body_scrollbar"):
            return
        try:
            bbox = self._body_canvas.bbox("all")
            content_height = (bbox[3] - bbox[1]) if bbox else 0
            canvas_height = self._body_canvas.winfo_height()
            if content_height > canvas_height + 2:
                self._body_scrollbar.grid()
            else:
                self._body_scrollbar.grid_remove()
                self._body_canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _on_configure(self, event) -> None:
        if event.widget is self:
            try:
                if self.wm_state() == "normal":
                    self._last_geometry = f"{self.winfo_width()}x{self.winfo_height()}"
            except Exception:
                pass

    def _maybe_minimize(self) -> None:
        if (
            self._alive
            and not self._closing
            and self._engine.state == STATE_RECORDING
            and self._recording_was_scheduled
            and self.minimize_var.get()
        ):
            self.iconify()

    def _report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        _LOG.error("UI callback failed", exc_info=(exc_type, exc_value, exc_tb))
        try:
            messagebox.showerror(
                APP_NAME,
                self.t("界面发生错误：\n{exc}\n\n详细信息已写入日志。", exc=exc_value),
                parent=self,
            )
        except Exception:
            pass
