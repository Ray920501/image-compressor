# gui/app.py
"""主窗口：合并后的图片工具箱。

界面结构：
    输入/输出目录 → 模式标签页（压缩 / 转换 / 放大）→ 批处理与跳过
    → 开始/暂停/取消 → 进度 → 日志

线程模型：
    Tk 主线程只负责界面；处理在后台线程 + 进程池中进行，
    所有跨线程日志/进度/结果都通过队列回传，避免直接操作 Tk 控件。
"""

import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from queue import Empty, Queue
from typing import List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from core import (
    APP_TITLE,
    BatchMode,
    ImageResult,
    OperationMode,
    OutputFormat,
    Preferences,
    ProcessedCache,
    SkipPolicy,
    TaskSettings,
    collect_images,
    commit_results,
    export_statistics,
    human_duration,
    human_size,
    predict_target_path,
    process_single_image,
    read_image_size,
    recommend_workers,
    split_batches,
    total_size,
)
from core.constants import (
    DEFAULT_BATCH_COUNT,
    DEFAULT_BATCH_SIZE_MB,
    DEFAULT_SKIP_THRESHOLD_KB,
    UPSCALE_DEFAULT_OUTPUT,
)
from core.utils import cleanup_orphan_temps, open_folder

from .panels import CompressPanel, ConvertPanel, UpscalePanel

_MODE_LABELS = {
    OperationMode.COMPRESS: "压缩 / 缩放",
    OperationMode.CONVERT: "格式转换",
    OperationMode.UPSCALE: "高清放大",
}

_LOG_LINE_LIMIT = 8000


# ---------------- 参数记忆辅助 ----------------
def _safe_int(data, key: str, default: int, minimum=None, maximum=None) -> int:
    try:
        value = int(data.get(key, default))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _safe_bool(data, key: str, default: bool) -> bool:
    return bool(data.get(key, default))


def _safe_choice(data, key: str, default: str, allowed) -> str:
    value = data.get(key, default)
    return value if value in allowed else default


class ImageToolApp:
    """图片工具箱主窗口。"""

    def __init__(self, root: tk.Tk, preferences_path: Optional[str] = None):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1080x920")
        self.root.minsize(960, 760)

        # ---------------- 状态 ----------------
        self.image_files: List[str] = []
        self._scanned_dir = ""
        self.is_processing = False
        self.cancel_event = threading.Event()
        self.running_event = threading.Event()
        self.running_event.set()

        self.success_count = 0
        self.error_count = 0
        self.skipped_count = 0
        self._last_output_dir = ""
        self.skipped_details: List[str] = []
        self._overwrite_confirmed = False

        self.queue: Queue = Queue()
        self.cache = ProcessedCache()
        self.prefs = Preferences(path=preferences_path) if preferences_path else Preferences()
        try:
            self.cache.load()
        except Exception:
            pass
        self._default_state: dict = {}
        self._restored_last = False

        self._build_style()
        self._build_widgets()
        self._bind_events()

        # 记录一份“默认参数”，供“恢复默认”使用
        self._default_state = self._collect_state()

        # 恢复上次使用的参数
        last_state = self.prefs.last
        if last_state:
            try:
                self._apply_state(last_state)
                self._restored_last = True
            except Exception:
                self._restored_last = False
        self._refresh_preset_box()

        self._poll_queue()

        self._log(f"{APP_TITLE} 已就绪")
        if self._restored_last:
            self._log("已恢复上次使用的参数（可通过“参数预设”保存多套配置）")
        self._log("使用步骤：选择输入文件夹 → 选择模式与参数 → 开始处理")

    # =================================================================
    # 界面构建
    # =================================================================
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")  # Windows 原生主题；其他系统自动退回默认
        except tk.TclError:
            pass
        default_font = ("Microsoft YaHei UI", 10)
        for name in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton",
                     "TEntry", "TSpinbox", "TCombobox", "TLabelframe.Label"):
            try:
                style.configure(name, font=default_font)
            except tk.TclError:
                pass

    def _build_widgets(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        self._build_directories(container)

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.X, pady=(2, 6))

        self.compress_panel = CompressPanel(self.notebook)
        self.convert_panel = ConvertPanel(self.notebook)
        self.upscale_panel = UpscalePanel(self.notebook)
        self.notebook.add(self.compress_panel, text="压缩 / 缩放")
        self.notebook.add(self.convert_panel, text="格式转换")
        self.notebook.add(self.upscale_panel, text="高清放大")

        self._build_advanced(container)
        self._build_controls(container)
        self._build_progress_and_log(container)

    def _build_directories(self, parent) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=(0, 4))

        row_input = ttk.Frame(frame)
        row_input.pack(fill=tk.X, pady=2)
        ttk.Label(row_input, text="输入文件夹:").pack(side=tk.LEFT)
        self.input_dir_var = tk.StringVar()
        ttk.Entry(row_input, textvariable=self.input_dir_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.browse_input_btn = ttk.Button(row_input, text="浏览...",
                                           command=self._browse_input)
        self.browse_input_btn.pack(side=tk.LEFT)
        self.scan_btn = ttk.Button(row_input, text="扫描文件", command=self._scan_files)
        self.scan_btn.pack(side=tk.LEFT, padx=(6, 0))

        row_output = ttk.Frame(frame)
        row_output.pack(fill=tk.X, pady=2)
        ttk.Label(row_output, text="输出文件夹:").pack(side=tk.LEFT)
        self.output_dir_var = tk.StringVar()
        ttk.Entry(row_output, textvariable=self.output_dir_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.browse_output_btn = ttk.Button(row_output, text="浏览...",
                                            command=self._browse_output)
        self.browse_output_btn.pack(side=tk.LEFT)
        self.preserve_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_output, text="保持目录结构",
                        variable=self.preserve_var).pack(side=tk.LEFT, padx=(10, 0))
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_output, text="包含子文件夹",
                        variable=self.recursive_var).pack(side=tk.LEFT, padx=(8, 0))

        row_preset = ttk.Frame(frame)
        row_preset.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row_preset, text="参数预设:").pack(side=tk.LEFT)
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(row_preset, textvariable=self.preset_var,
                                       width=26, state="readonly", values=[])
        self.preset_box.pack(side=tk.LEFT, padx=6)
        self.preset_box.bind("<<ComboboxSelected>>", self._apply_selected_preset)
        ttk.Button(row_preset, text="保存为预设...",
                   command=self._save_preset_ui).pack(side=tk.LEFT)
        ttk.Button(row_preset, text="删除",
                   command=self._delete_preset_ui).pack(side=tk.LEFT, padx=4)
        ttk.Button(row_preset, text="恢复默认",
                   command=self._reset_defaults_ui).pack(side=tk.LEFT)

        ttk.Label(
            frame,
            text="输出留空时：压缩覆盖原图 / 放大写入“输入目录\\高清放大输出” / 转换写入原图同目录",
            foreground="gray",
        ).pack(anchor=tk.W, pady=(2, 0))

    def _build_advanced(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="批处理与跳过", padding=(10, 6))
        frame.pack(fill=tk.X, pady=(0, 6))

        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="分批方式:").pack(side=tk.LEFT)
        self.batch_mode_var = tk.StringVar(value="count")
        ttk.Radiobutton(row1, text="按张数", value="count",
                        variable=self.batch_mode_var).pack(side=tk.LEFT, padx=(8, 2))
        self.batch_count_var = tk.IntVar(value=DEFAULT_BATCH_COUNT)
        ttk.Spinbox(row1, from_=1, to=9999, textvariable=self.batch_count_var,
                    width=6).pack(side=tk.LEFT)
        ttk.Label(row1, text="张").pack(side=tk.LEFT, padx=(2, 10))
        ttk.Radiobutton(row1, text="按总大小", value="size",
                        variable=self.batch_mode_var).pack(side=tk.LEFT, padx=(4, 2))
        self.batch_size_var = tk.IntVar(value=DEFAULT_BATCH_SIZE_MB)
        ttk.Spinbox(row1, from_=1, to=99999, textvariable=self.batch_size_var,
                    width=6).pack(side=tk.LEFT)
        ttk.Label(row1, text="MB").pack(side=tk.LEFT, padx=(2, 12))

        ttk.Label(row1, text="进程数:").pack(side=tk.LEFT)
        self.workers_var = tk.IntVar(value=0)
        ttk.Spinbox(row1, from_=0, to=32, textvariable=self.workers_var,
                    width=4).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(row1, text="(0 = 自动)").pack(side=tk.LEFT)

        ttk.Button(row1, text="清空缓存", command=self._clear_cache_ui).pack(side=tk.RIGHT)

        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="跳过:").pack(side=tk.LEFT)
        self.skip_larger_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="大于", variable=self.skip_larger_var).pack(
            side=tk.LEFT, padx=(8, 2))
        self.skip_larger_kb_var = tk.IntVar(value=DEFAULT_SKIP_THRESHOLD_KB)
        ttk.Spinbox(row2, from_=1, to=999999, textvariable=self.skip_larger_kb_var,
                    width=7).pack(side=tk.LEFT)
        ttk.Label(row2, text="KB").pack(side=tk.LEFT, padx=(2, 8))

        self.skip_smaller_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="小于", variable=self.skip_smaller_var).pack(
            side=tk.LEFT, padx=(2, 2))
        self.skip_smaller_kb_var = tk.IntVar(value=10)
        ttk.Spinbox(row2, from_=1, to=999999, textvariable=self.skip_smaller_kb_var,
                    width=7).pack(side=tk.LEFT)
        ttk.Label(row2, text="KB").pack(side=tk.LEFT, padx=(2, 10))

        self.skip_recent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="最近", variable=self.skip_recent_var).pack(
            side=tk.LEFT, padx=(2, 2))
        self.recent_hours_var = tk.IntVar(value=1)
        ttk.Spinbox(row2, from_=1, to=720, textvariable=self.recent_hours_var,
                    width=5).pack(side=tk.LEFT)
        ttk.Label(row2, text="小时内修改").pack(side=tk.LEFT, padx=(2, 10))

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, pady=2)
        self.skip_min_dim_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="跳过尺寸过小：宽 <",
                        variable=self.skip_min_dim_var).pack(side=tk.LEFT)
        self.min_width_var = tk.IntVar(value=100)
        ttk.Spinbox(row3, from_=0, to=20000, textvariable=self.min_width_var,
                    width=6).pack(side=tk.LEFT, padx=(2, 2))
        ttk.Label(row3, text="或 高 <").pack(side=tk.LEFT)
        self.min_height_var = tk.IntVar(value=100)
        ttk.Spinbox(row3, from_=0, to=20000, textvariable=self.min_height_var,
                    width=6).pack(side=tk.LEFT, padx=(2, 2))
        ttk.Label(row3, text="像素（0 = 不检查）").pack(side=tk.LEFT, padx=(2, 12))

        self.use_cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="跳过未变化的文件（缓存）",
                        variable=self.use_cache_var).pack(side=tk.LEFT, padx=(6, 0))
        self.save_stats_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="保存 JSON 统计",
                        variable=self.save_stats_var).pack(side=tk.LEFT, padx=(10, 0))

        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="覆盖前原图移入回收站",
                        variable=self.backup_var).pack(side=tk.LEFT, padx=(10, 0))

    def _build_controls(self, parent) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=(2, 4))

        self.start_btn = ttk.Button(frame, text="开始处理", command=self._start,
                                    width=14)
        self.start_btn.pack(side=tk.LEFT)
        self.pause_btn = ttk.Button(frame, text="暂停", command=self._toggle_pause,
                                    state=tk.DISABLED, width=8)
        self.pause_btn.pack(side=tk.LEFT, padx=6)
        self.cancel_btn = ttk.Button(frame, text="取消", command=self._cancel,
                                     state=tk.DISABLED, width=8)
        self.cancel_btn.pack(side=tk.LEFT)

        self.open_output_btn = ttk.Button(frame, text="打开输出目录",
                                          command=self._open_output_dir,
                                          state=tk.DISABLED)
        self.open_output_btn.pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(frame, text="清理临时文件",
                   command=self._cleanup_temp_files_ui).pack(side=tk.LEFT, padx=6)
        self.skipped_btn = ttk.Button(frame, text="查看跳过",
                                      command=self._show_skipped_files,
                                      state=tk.DISABLED)
        self.skipped_btn.pack(side=tk.LEFT)

    def _build_progress_and_log(self, parent) -> None:
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(parent, variable=self.progress_var,
                                            maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(4, 2))

        self.status_label = ttk.Label(parent, text="就绪")
        self.status_label.pack(anchor=tk.W)

        log_frame = ttk.LabelFrame(parent, text="日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 2))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, height=12, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.stats_label = ttk.Label(parent, text="", foreground="#555555")
        self.stats_label.pack(anchor=tk.W, pady=(2, 0))

    def _bind_events(self) -> None:
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # =================================================================
    # 基础操作
    # =================================================================
    def _current_mode(self) -> OperationMode:
        selection = self.notebook.select()
        if not selection:
            return OperationMode.COMPRESS
        index = self.notebook.index(selection)
        return (OperationMode.COMPRESS, OperationMode.CONVERT,
                OperationMode.UPSCALE)[index]

    def _current_panel(self):
        selection = self.notebook.select()
        index = self.notebook.index(selection) if selection else 0
        return (self.compress_panel, self.convert_panel, self.upscale_panel)[index]

    def _on_tab_changed(self, _event=None) -> None:
        try:
            label = _MODE_LABELS[self._current_mode()]
        except tk.TclError:
            return
        self.status_label.config(text=f"当前模式：{label}")

    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="选择输入文件夹")
        if path:
            self.input_dir_var.set(path)
            self._scanned_dir = ""

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出文件夹（留空则按默认规则输出）")
        if path:
            self.output_dir_var.set(path)

    def _scan_files(self) -> None:
        input_dir = self.input_dir_var.get().strip()
        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showwarning("警告", "请先选择有效的输入文件夹")
            return

        self.status_label.config(text="正在扫描文件...")
        self.root.update_idletasks()

        self.image_files = collect_images(input_dir, self.recursive_var.get())
        self._scanned_dir = os.path.normcase(os.path.abspath(input_dir))
        size = total_size(self.image_files)

        self._log(f"扫描完成: {len(self.image_files)} 张图片，"
                  f"共 {human_size(size)}")
        if not self.image_files:
            self._log("提示：该文件夹中没有支持的图片格式 "
                      "(jpg/png/webp/bmp/tif/gif/heic)")

        self._update_stats_label()
        self.status_label.config(text="就绪")
        self.progress_var.set(0)

        # 顺手清理超过 24 小时的残留临时文件
        try:
            cleanup_orphan_temps([input_dir], log=self._log)
        except Exception:
            pass

    def _ensure_files(self) -> bool:
        input_dir = self.input_dir_var.get().strip()
        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showwarning("警告", "请先选择有效的输入文件夹")
            return False
        current = os.path.normcase(os.path.abspath(input_dir))
        if not self.image_files or self._scanned_dir != current:
            self._scan_files()
        if not self.image_files:
            messagebox.showwarning("提示", "没有找到可处理的图片")
            return False
        return True

    # =================================================================
    # 开始 / 暂停 / 取消
    # =================================================================
    def _collect_settings(self) -> TaskSettings:
        mode = self._current_mode()
        settings = TaskSettings(
            mode=mode,
            input_dir=self.input_dir_var.get().strip(),
            output_dir=self.output_dir_var.get().strip(),
            recursive=bool(self.recursive_var.get()),
            preserve_structure=bool(self.preserve_var.get()),
            skip_policy=SkipPolicy(
                skip_larger=bool(self.skip_larger_var.get()),
                larger_threshold_kb=int(self.skip_larger_kb_var.get()),
                skip_smaller=bool(self.skip_smaller_var.get()),
                smaller_threshold_kb=int(self.skip_smaller_kb_var.get()),
                skip_recent=bool(self.skip_recent_var.get()),
                recent_hours=int(self.recent_hours_var.get()),
                min_width=(int(self.min_width_var.get())
                           if self.skip_min_dim_var.get() else 0),
                min_height=(int(self.min_height_var.get())
                            if self.skip_min_dim_var.get() else 0),
            ),
            batch_mode=(BatchMode.BY_SIZE if self.batch_mode_var.get() == "size"
                        else BatchMode.BY_COUNT),
            batch_count=int(self.batch_count_var.get()),
            batch_size_mb=int(self.batch_size_var.get()),
            workers=int(self.workers_var.get()),
            use_cache=bool(self.use_cache_var.get()),
            save_statistics=bool(self.save_stats_var.get()),
            backup_original=bool(self.backup_var.get()),
        )

        self._current_panel().apply(settings)
        return settings

    # =================================================================
    # 参数记忆 / 预设
    # =================================================================
    @staticmethod
    def _int_value(variable, default: int) -> int:
        try:
            return int(variable.get())
        except (tk.TclError, ValueError):
            return default

    def _collect_state(self) -> dict:
        """把当前全部界面参数收集为可序列化的字典。"""
        return {
            "mode": self._current_mode().value,
            "input_dir": self.input_dir_var.get(),
            "output_dir": self.output_dir_var.get(),
            "recursive": bool(self.recursive_var.get()),
            "preserve": bool(self.preserve_var.get()),
            "batch_mode": self.batch_mode_var.get(),
            "batch_count": self._int_value(self.batch_count_var, DEFAULT_BATCH_COUNT),
            "batch_size_mb": self._int_value(self.batch_size_var, DEFAULT_BATCH_SIZE_MB),
            "skip_larger": bool(self.skip_larger_var.get()),
            "skip_larger_kb": self._int_value(self.skip_larger_kb_var,
                                              DEFAULT_SKIP_THRESHOLD_KB),
            "skip_smaller": bool(self.skip_smaller_var.get()),
            "skip_smaller_kb": self._int_value(self.skip_smaller_kb_var, 10),
            "skip_recent": bool(self.skip_recent_var.get()),
            "recent_hours": self._int_value(self.recent_hours_var, 1),
            "skip_min_dim": bool(self.skip_min_dim_var.get()),
            "min_width": self._int_value(self.min_width_var, 100),
            "min_height": self._int_value(self.min_height_var, 100),
            "use_cache": bool(self.use_cache_var.get()),
            "save_stats": bool(self.save_stats_var.get()),
            "workers": self._int_value(self.workers_var, 0),
            "backup": bool(self.backup_var.get()),
            "compress": self.compress_panel.state(),
            "convert": self.convert_panel.state(),
            "upscale": self.upscale_panel.state(),
        }

    def _apply_state(self, state: dict) -> None:
        """应用参数字典（对缺失/非法值做保护）。"""
        if not isinstance(state, dict):
            return

        for key, variable in (("input_dir", self.input_dir_var),
                              ("output_dir", self.output_dir_var)):
            value = state.get(key)
            if isinstance(value, str):
                variable.set(value)

        self.recursive_var.set(_safe_bool(state, "recursive", True))
        self.preserve_var.set(_safe_bool(state, "preserve", True))
        self.batch_mode_var.set(_safe_choice(state, "batch_mode", "count",
                                             {"count", "size"}))
        self.batch_count_var.set(_safe_int(state, "batch_count",
                                           DEFAULT_BATCH_COUNT, 1, 9999))
        self.batch_size_var.set(_safe_int(state, "batch_size_mb",
                                          DEFAULT_BATCH_SIZE_MB, 1, 99999))

        self.skip_larger_var.set(_safe_bool(state, "skip_larger", False))
        self.skip_larger_kb_var.set(_safe_int(state, "skip_larger_kb",
                                              DEFAULT_SKIP_THRESHOLD_KB, 1, 999999))
        self.skip_smaller_var.set(_safe_bool(state, "skip_smaller", False))
        self.skip_smaller_kb_var.set(_safe_int(state, "skip_smaller_kb", 10, 1, 999999))
        self.skip_recent_var.set(_safe_bool(state, "skip_recent", False))
        self.recent_hours_var.set(_safe_int(state, "recent_hours", 1, 1, 720))
        self.skip_min_dim_var.set(_safe_bool(state, "skip_min_dim", False))
        self.min_width_var.set(_safe_int(state, "min_width", 100, 0, 20000))
        self.min_height_var.set(_safe_int(state, "min_height", 100, 0, 20000))

        self.use_cache_var.set(_safe_bool(state, "use_cache", True))
        self.save_stats_var.set(_safe_bool(state, "save_stats", False))
        self.workers_var.set(_safe_int(state, "workers", 0, 0, 32))
        self.backup_var.set(_safe_bool(state, "backup", True))

        for key, panel in (("compress", self.compress_panel),
                           ("convert", self.convert_panel),
                           ("upscale", self.upscale_panel)):
            data = state.get(key)
            if isinstance(data, dict):
                try:
                    panel.load_state(data)
                except Exception:
                    continue

        try:
            mode = OperationMode(state.get("mode", OperationMode.COMPRESS.value))
            index = {OperationMode.COMPRESS: 0,
                     OperationMode.CONVERT: 1,
                     OperationMode.UPSCALE: 2}[mode]
            self.notebook.select(index)
        except Exception:
            pass

    def _save_preferences(self) -> None:
        try:
            self.prefs.set_last(self._collect_state())
        except Exception:
            pass

    def _refresh_preset_box(self) -> None:
        names = self.prefs.preset_names
        self.preset_box.configure(values=names)
        if self.preset_var.get() not in names:
            self.preset_var.set("")

    def _apply_selected_preset(self, _event=None) -> None:
        name = self.preset_var.get().strip()
        if not name:
            return
        state = self.prefs.get_preset(name)
        if not state:
            messagebox.showwarning("预设不存在", f"预设“{name}”已不存在。")
            self._refresh_preset_box()
            return
        self._apply_state(state)
        self.status_label.config(text=f"已应用预设：{name}")
        self._log(f"已应用参数预设: {name}")

    def _save_preset_ui(self) -> None:
        name = simpledialog.askstring(
            "保存预设", "请输入预设名称（例如：4K 归档 / 1080P 分享）：",
            parent=self.root,
        )
        if not name or not name.strip():
            return
        name = name.strip()

        if name in self.prefs.preset_names:
            if not messagebox.askyesno("覆盖预设", f"预设“{name}”已存在，是否覆盖？"):
                return

        if self.prefs.set_preset(name, self._collect_state()):
            self._refresh_preset_box()
            self.preset_var.set(name)
            self._log(f"已保存参数预设: {name}")
            messagebox.showinfo("已保存", f"预设“{name}”已保存。")
        else:
            messagebox.showwarning("保存失败", "预设数量已达上限或名称无效。")

    def _delete_preset_ui(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showinfo("提示", "请先在下拉框中选择要删除的预设。")
            return
        if not messagebox.askyesno("删除预设", f"确定删除预设“{name}”吗？"):
            return
        self.prefs.delete_preset(name)
        self._refresh_preset_box()
        self._log(f"已删除参数预设: {name}")

    def _reset_defaults_ui(self) -> None:
        if not messagebox.askyesno(
            "恢复默认",
            "将三个模式的参数恢复为默认值（输入/输出文件夹保留）。\n继续吗？",
        ):
            return
        state = {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in self._default_state.items()
        }
        state["input_dir"] = self.input_dir_var.get()
        state["output_dir"] = self.output_dir_var.get()
        self._apply_state(state)
        self.preset_var.set("")
        self._log("已恢复默认参数")

    def _start(self) -> None:
        if self.is_processing:
            return
        if not self._ensure_files():
            return

        try:
            settings = self._collect_settings()
        except (tk.TclError, ValueError):
            messagebox.showerror("参数错误", "有参数不是有效数字，请检查后重试")
            return

        ok, message = self._current_panel().validate()
        if not ok:
            messagebox.showerror("参数错误", message)
            return

        if settings.delete_original:
            confirmed = messagebox.askyesno(
                "确认删除原文件",
                "已勾选“成功后删除原文件”。\n"
                "处理成功并写入新文件后，原始图片将被永久删除，无法恢复。\n\n"
                "确定继续吗？",
                icon="warning",
            )
            if not confirmed:
                return

        if (settings.mode is OperationMode.COMPRESS
                and not settings.output_dir
                and not self._overwrite_confirmed):
            if not messagebox.askyesno(
                "确认覆盖原图",
                "输出文件夹为空，处理结果将直接覆盖原图。\n\n"
                "程序会先写临时文件再原子替换，异常中断不会损坏原图；"
                "但仍建议先备份重要照片。\n\n是否继续？",
                icon="warning",
            ):
                return
            self._overwrite_confirmed = True

        # 记住本次参数，下次启动自动恢复
        self._save_preferences()

        self.is_processing = True
        self.cancel_event.clear()
        self.running_event.set()

        self.start_btn.config(state=tk.DISABLED)
        self.scan_btn.config(state=tk.DISABLED)
        self.browse_input_btn.config(state=tk.DISABLED)
        self.browse_output_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="暂停")
        self.cancel_btn.config(state=tk.NORMAL)
        self.open_output_btn.config(state=tk.DISABLED)
        self.skipped_btn.config(state=tk.DISABLED)
        self.skipped_details.clear()

        self.success_count = 0
        self.error_count = 0
        self.skipped_count = 0
        self.progress_var.set(0)
        self.status_label.config(text="处理中...")
        self._update_stats_label()

        self._log("")
        self._log("─" * 26 + f" 开始{_MODE_LABELS[settings.mode]} " + "─" * 26)
        self._log(f"输入: {settings.input_dir}")
        if settings.output_dir:
            self._log(f"输出: {settings.output_dir}")
        elif settings.mode is OperationMode.UPSCALE:
            self._log(f"输出: {os.path.join(settings.input_dir, UPSCALE_DEFAULT_OUTPUT)}"
                      "（自动）")
        elif settings.mode is OperationMode.CONVERT:
            self._log("输出: 与原图同目录")
        else:
            self._log("输出: 覆盖原图（临时文件写入后原子替换）")

        worker = threading.Thread(target=self._run_task, args=(settings,), daemon=True)
        worker.start()

    def _toggle_pause(self) -> None:
        if not self.is_processing:
            return
        if self.running_event.is_set():
            self.running_event.clear()
            self.pause_btn.config(text="继续")
            self._log("⏸ 已暂停（当前批次处理完后暂停提交）")
        else:
            self.running_event.set()
            self.pause_btn.config(text="暂停")
            self._log("▶ 继续处理")

    def _cancel(self) -> None:
        if not self.is_processing:
            return
        self.cancel_event.set()
        self.running_event.set()
        self.cancel_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.DISABLED)
        self._log("⏹ 正在取消，等待当前批次收尾...")

    # =================================================================
    # 后台任务
    # =================================================================
    def _emit(self, kind: str, *payload) -> None:
        self.queue.put((kind,) + payload)

    def _run_task(self, settings: TaskSettings) -> None:
        """后台线程入口：任何异常都必须回传 done，避免界面永久锁死。"""
        started = time.perf_counter()
        try:
            summary = self._execute_task(settings, started)
        except Exception as exc:  # noqa: BLE001 - 兜底，防止线程静默退出
            self._emit("log", f"✗ 处理线程异常: {exc}")
            summary = self._build_summary(settings, started, [], 0, 0, 0, False)
            summary["stats_path"] = ""
        self._emit("done", summary)

    def _execute_task(self, settings: TaskSettings, started: float) -> dict:
        skipped = 0
        results: List[ImageResult] = []
        cancelled = False
        committed = 0
        commit_failed = 0
        stats_path: Optional[str] = None

        # ---------- 1. 过滤（跳过策略 + 缓存）----------
        to_process: List[str] = []
        signatures = {}
        min_width = max(0, int(settings.skip_policy.min_width))
        min_height = max(0, int(settings.skip_policy.min_height))

        for path in list(self.image_files):
            if self.cancel_event.is_set():
                break

            skip, reason = settings.skip_policy.should_skip(path)

            if not skip and (min_width or min_height):
                dimensions = read_image_size(path)
                if dimensions and (dimensions[0] < min_width
                                   or dimensions[1] < min_height):
                    skip = True
                    reason = f"尺寸过小（{dimensions[0]}×{dimensions[1]}）"

            if not skip and settings.use_cache:
                ext = os.path.splitext(path)[1].lower()
                signature = signatures.get(ext)
                if signature is None:
                    signature = settings.cache_signature(ext)
                    signatures[ext] = signature
                if self.cache.is_processed(path, signature):
                    skip, reason = True, "已处理过（缓存）"

            if skip:
                skipped += 1
                self._emit("skip", f"⊙ {os.path.basename(path)}: {reason}")
            else:
                to_process.append(path)

        if self.cancel_event.is_set():
            return self._build_summary(settings, started, results, skipped, 0, 0, True)

        if not to_process:
            self._emit("log", "没有需要处理的图片")
            return self._build_summary(settings, started, results, skipped, 0, 0, False)

        # 预判输出名冲突（如 a.png 与 a.webp 同时转换为 a.jpg）并提前改名
        overrides = {}
        predictable = (settings.mode is not OperationMode.COMPRESS
                       or settings.output_format is not OutputFormat.SMART)
        if predictable:
            used = {}
            for path in to_process:
                predicted = predict_target_path(path, settings)
                if not predicted:
                    continue
                key = os.path.normcase(os.path.abspath(predicted))
                if key in used:
                    stem, ext = os.path.splitext(predicted)
                    suffix = os.path.splitext(path)[1].lstrip(".").lower() or "copy"
                    candidate = f"{stem} ({suffix}){ext}"
                    index = 2
                    while os.path.normcase(os.path.abspath(candidate)) in used:
                        candidate = f"{stem} ({suffix}-{index}){ext}"
                        index += 1
                    overrides[path] = candidate
                    used[os.path.normcase(os.path.abspath(candidate))] = path
                    self._emit("log", f"⚠️ 输出名冲突，改名输出: {os.path.basename(candidate)}")
                else:
                    used[key] = path

        # ---------- 2. 进程池处理 ----------
        average = total_size(to_process) / max(1, len(to_process))
        workers = int(settings.workers) if int(settings.workers) > 0 else \
            recommend_workers(len(to_process), average)
        workers = max(1, min(workers, len(to_process)))

        # 控制每个进程内的 libvips 线程数，避免“进程数 × CPU 线程数”超订。
        # 该环境变量在 spawn 新进程时被继承，因此必须在使用进程池前设置。
        cpu = os.cpu_count() or 4
        os.environ["VIPS_CONCURRENCY"] = str(max(1, cpu // workers))

        self._emit("log", f"共 {len(to_process)} 张待处理，使用 {workers} 个进程并行"
                          f"（每进程 libvips 线程 {os.environ['VIPS_CONCURRENCY']}）")

        total = len(to_process)
        completed = 0
        executor = ProcessPoolExecutor(max_workers=workers)
        try:
            for batch in split_batches(to_process, settings):
                if self.cancel_event.is_set():
                    cancelled = True
                    break

                while not self.running_event.is_set():
                    if self.cancel_event.is_set():
                        break
                    time.sleep(0.1)
                if self.cancel_event.is_set():
                    cancelled = True
                    break

                futures = {
                    executor.submit(process_single_image, path, settings,
                                    overrides.get(path)): path
                    for path in batch
                }

                stop_batch = False
                for future in as_completed(futures):
                    path = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001 - 保留异常信息给用户
                        result = self._failed_result(path, exc)

                    if self.cancel_event.is_set() and not stop_batch:
                        stop_batch = True
                        self._emit("log", "⏹ 已取消，丢弃当前批次结果并清理临时文件")

                    if stop_batch:
                        self._safe_remove(result.tmp_path)
                        continue

                    results.append(result)
                    completed += 1
                    self._emit("result", result)
                    self._emit(
                        "progress",
                        completed / total * 100.0,
                        f"{_MODE_LABELS[settings.mode]} {completed}/{total}",
                    )

                if stop_batch:
                    cancelled = True
                    break
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        # ---------- 3. 取消：清理未提交的临时文件 ----------
        if cancelled:
            discarded = sum(1 for r in results if r.success and not r.skipped)
            for result in results:
                self._safe_remove(result.tmp_path)
            results = []
            if discarded:
                self._emit("log", f"已丢弃 {discarded} 个未提交的处理结果")

        # ---------- 4. 提交 ----------
        if not cancelled and results:
            self._emit("status", "正在提交文件...")
            committed, commit_failed = commit_results(
                results, log=lambda message: self._emit("log", message),
                backup=settings.backup_original)

        # ---------- 5. 更新缓存 ----------
        if not cancelled and settings.use_cache:
            for result in results:
                if result.success and not result.skipped and os.path.exists(result.file_path):
                    ext = os.path.splitext(result.file_path)[1].lower()
                    signature = signatures.get(ext) or settings.cache_signature(ext)
                    self.cache.mark(result.file_path, signature, result.target_path)
            self.cache.save()

        # ---------- 6. 统计报告 ----------
        summary = self._build_summary(settings, started, results, skipped,
                                      committed, commit_failed, cancelled)
        if not cancelled and settings.save_statistics and results:
            base_dir = summary.get("output_dir") or settings.input_dir
            stats_path = export_statistics(results, settings, summary, base_dir=base_dir)
            if stats_path:
                self._emit("log", f"统计报告: {stats_path}")

        summary["stats_path"] = stats_path or ""
        return summary

    def _failed_result(self, path: str, exc: Exception) -> ImageResult:
        return ImageResult(
            file_path=path,
            original_size=0,
            output_size=0,
            original_resolution=(0, 0),
            output_resolution=(0, 0),
            format_changed=False,
            new_format="",
            success=False,
            error_message=f"处理异常: {exc}",
        )

    @staticmethod
    def _safe_remove(path: str) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _build_summary(self, settings: TaskSettings, started: float,
                       results: List[ImageResult], skipped: int,
                       committed: int, commit_failed: int,
                       cancelled: bool) -> dict:
        success = sum(1 for r in results if r.success and not r.skipped)
        failed = sum(1 for r in results if not r.success)
        skipped_results = sum(1 for r in results if r.skipped)
        bytes_in = sum(r.original_size for r in results if r.success and not r.skipped)
        bytes_out = sum(r.output_size for r in results if r.success and not r.skipped)

        if settings.output_dir:
            output_dir = settings.output_dir
        elif settings.mode is OperationMode.UPSCALE:
            output_dir = os.path.join(settings.input_dir, UPSCALE_DEFAULT_OUTPUT)
        elif settings.mode is OperationMode.CONVERT:
            output_dir = settings.input_dir
        else:
            output_dir = ""

        return {
            "mode": _MODE_LABELS[settings.mode],
            "total": len(results) + skipped,
            "success": success,
            "failed": failed,
            "skipped": skipped + skipped_results,
            "committed": committed,
            "commit_failed": commit_failed,
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
            "elapsed": time.perf_counter() - started,
            "cancelled": cancelled,
            "output_dir": output_dir,
            "stats_path": "",
        }

    # =================================================================
    # 队列轮询（Tk 主线程）
    # =================================================================
    def _poll_queue(self) -> None:
        while True:
            try:
                message = self.queue.get_nowait()
            except Empty:
                break
            kind = message[0]
            payload = message[1:]

            if kind == "log":
                self._append_log(payload[0])
            elif kind == "skip":
                self.skipped_count += 1
                self.skipped_details.append(payload[0])
                self._append_log(payload[0])
                self._update_stats_label()
            elif kind == "result":
                self._handle_result(payload[0])
            elif kind == "progress":
                self.progress_var.set(payload[0])
                if len(payload) > 1:
                    self.status_label.config(text=payload[1])
            elif kind == "status":
                self.status_label.config(text=payload[0])
            elif kind == "done":
                self._on_done(payload[0])

        self.root.after(100, self._poll_queue)

    def _handle_result(self, result: ImageResult) -> None:
        if result.skipped:
            self.skipped_count += 1
            self.skipped_details.append(self._format_result(result))
        elif result.success:
            self.success_count += 1
        else:
            self.error_count += 1

        self._append_log(self._format_result(result))
        self._update_stats_label()

    @staticmethod
    def _format_result(result: ImageResult) -> str:
        name = os.path.basename(result.file_path)
        if result.skipped:
            return f"⊙ {name}: {result.skip_reason}"
        if not result.success:
            return f"✗ {name}: {result.error_message}"

        old_w, old_h = result.original_resolution
        new_w, new_h = result.output_resolution
        delta = result.output_size - result.original_size
        percent = abs(result.reduction_percent)
        arrow = "↓" if delta <= 0 else "↑"
        if result.output_size == 0:
            percent_text = ""
        else:
            percent_text = f"  {arrow}{percent:.1f}%"
        format_text = f"  {result.new_format.upper()}" if result.format_changed else ""
        quality_text = f"  Q{result.quality_used}" if result.quality_used else ""

        return (
            f"✓ {name}  ({old_w}×{old_h} → {new_w}×{new_h})  "
            f"{human_size(result.original_size)} → {human_size(result.output_size)}"
            f"{percent_text}{format_text}{quality_text}  {result.elapsed_ms:.0f}ms"
        )

    def _on_done(self, summary: dict) -> None:
        self.is_processing = False
        self.running_event.set()
        self.start_btn.config(state=tk.NORMAL)
        self.scan_btn.config(state=tk.NORMAL)
        self.browse_input_btn.config(state=tk.NORMAL)
        self.browse_output_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="暂停")
        self.cancel_btn.config(state=tk.DISABLED)

        self._last_output_dir = summary.get("output_dir") or ""
        if self._last_output_dir and os.path.isdir(self._last_output_dir):
            self.open_output_btn.config(state=tk.NORMAL)
        if self.skipped_details:
            self.skipped_btn.config(state=tk.NORMAL)

        cancelled = summary.get("cancelled")
        if cancelled:
            self.status_label.config(text="已取消")
        else:
            self.progress_var.set(100)
            self.status_label.config(text="完成")

        self._update_stats_label()
        self._append_log("")
        self._append_log("─" * 30)
        if cancelled:
            self._append_log("已取消（未提交任何文件）")

        self._append_log(
            f"{summary['mode']}完成：成功 {summary['success']} | "
            f"失败 {summary['failed']} | 跳过 {summary['skipped']}"
        )

        if summary["success"]:
            saved = summary["bytes_in"] - summary["bytes_out"]
            percent = (saved / summary["bytes_in"] * 100) if summary["bytes_in"] else 0
            self._append_log(
                f"大小：{human_size(summary['bytes_in'])} → "
                f"{human_size(summary['bytes_out'])}"
                f"（{'节省' if saved >= 0 else '增加'} {human_size(abs(saved))}"
                f"{', ' + format(percent, '.1f') + '%' if summary['bytes_in'] else ''}）"
            )
        self._append_log(f"耗时：{human_duration(summary['elapsed'])}")

        if summary.get("commit_failed"):
            self._append_log(
                f"⚠️ {summary['commit_failed']} 个文件的临时文件未能提交，"
                "已保留在原位置"
            )
        if summary.get("stats_path"):
            self._append_log(f"统计报告：{summary['stats_path']}")

        self._append_log("─" * 30)

        if cancelled:
            messagebox.showinfo("已取消", "处理已取消，未提交的文件已清理。")
            return

        if summary["success"] == 0 and summary["failed"] == 0:
            messagebox.showinfo("提示", f"没有需要处理的文件（跳过 {summary['skipped']} 个）。")
            return

        if summary["failed"] or summary.get("commit_failed"):
            messagebox.showwarning(
                "处理完成（部分失败）",
                f"成功 {summary['success']} 张，失败 {summary['failed']} 张，"
                f"跳过 {summary['skipped']} 张。\n详细信息请查看日志。",
            )
        else:
            messagebox.showinfo(
                "处理完成",
                f"成功处理 {summary['success']} 张图片"
                f"（跳过 {summary['skipped']} 张），"
                f"耗时 {human_duration(summary['elapsed'])}。",
            )

    # =================================================================
    # 日志与统计
    # =================================================================
    def _log(self, message: str) -> None:
        """在主线程直接写日志。"""
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")

        # 控制文本长度，避免长时间运行后界面卡顿
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > _LOG_LINE_LIMIT:
            self.log_text.delete("1.0", f"{line_count - _LOG_LINE_LIMIT + 1000}.0")

        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _update_stats_label(self) -> None:
        self.stats_label.config(
            text=f"共 {len(self.image_files)} 张 | 成功 {self.success_count} | "
                 f"失败 {self.error_count} | 跳过 {self.skipped_count}"
        )

    # =================================================================
    # 工具操作
    # =================================================================
    def _open_output_dir(self) -> None:
        target = self._last_output_dir or self.output_dir_var.get().strip()
        if not target and self.input_dir_var.get().strip():
            if self._current_mode() is OperationMode.UPSCALE:
                target = os.path.join(self.input_dir_var.get().strip(),
                                      UPSCALE_DEFAULT_OUTPUT)
            else:
                target = self.input_dir_var.get().strip()
        if not open_folder(target):
            messagebox.showwarning("提示", "输出文件夹尚不存在或无法打开")

    def _cleanup_temp_files_ui(self) -> None:
        initial = (self.output_dir_var.get().strip()
                   or self.input_dir_var.get().strip() or None)
        directory = filedialog.askdirectory(
            title="选择要清理残留临时文件的目录", initialdir=initial)
        if not directory:
            return
        if not messagebox.askyesno(
            "确认清理",
            f"将删除该目录中所有由本工具产生的临时文件"
            f"（文件名包含 .tmp，通常是中断运行后的残留）：\n{directory}\n\n"
            "继续吗？",
        ):
            return
        removed = cleanup_orphan_temps([directory], max_age_seconds=0, log=self._log)
        messagebox.showinfo("清理完成", f"已删除 {removed} 个临时文件。")

    def _show_skipped_files(self) -> None:
        """查看本次运行被跳过的文件（移植自 DS图片.py 的“查看跳过”）。"""
        if not self.skipped_details:
            messagebox.showinfo("跳过文件", "本次没有跳过的文件。")
            return

        window = tk.Toplevel(self.root)
        window.title(f"跳过的文件（{len(self.skipped_details)} 个）")
        window.geometry("780x420")
        window.transient(self.root)

        frame = ttk.Frame(window, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)

        text = scrolledtext.ScrolledText(frame, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, "\n".join(self.skipped_details))
        text.configure(state=tk.DISABLED)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(6, 0))

        def copy_all() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(self.skipped_details))
            messagebox.showinfo("已复制", "跳过列表已复制到剪贴板。", parent=window)

        ttk.Button(buttons, text="复制列表", command=copy_all).pack(side=tk.LEFT)
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side=tk.RIGHT)

    def _clear_cache_ui(self) -> None:
        if not messagebox.askyesno(
            "清空缓存",
            "将清空“已处理文件”记录（不会删除任何图片）。\n"
            "清空后再次运行会重新处理所有图片，确定吗？",
        ):
            return
        self.cache.clear()
        self._log("已清空处理记录缓存")

    def _on_close(self) -> None:
        if self.is_processing:
            if not messagebox.askyesno("退出", "正在处理图片，确定要中断并退出吗？"):
                return
            self.cancel_event.set()
            self.running_event.set()
        self._save_preferences()
        self.root.destroy()
