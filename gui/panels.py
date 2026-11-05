# gui/panels.py
"""三种处理模式的参数面板（压缩 / 格式转换 / 高清放大）。

每个面板只负责“构建控件 + 把控件值写回 TaskSettings + 校验”，
主窗口负责扫描、批处理、日志与提交。
"""

import re
import tkinter as tk
from tkinter import colorchooser, ttk

from core.constants import (
    DEFAULT_BACKGROUND,
    DEFAULT_JPEG_QUALITY,
    DEFAULT_PNG_COMPRESSION,
    DEFAULT_SHARPEN_SIGMA,
    DEFAULT_UPSCALE_FACTOR,
    DEFAULT_WEBP_QUALITY,
    SIZE_PRESETS,
)
from core.enums import OutputFormat
from core.models import TaskSettings


# ---------------- 状态读写辅助（用于参数记忆/预设） ----------------
def _var_int(variable, default: int) -> int:
    try:
        return int(variable.get())
    except (tk.TclError, ValueError):
        return default


def _var_float(variable, default: float) -> float:
    try:
        return float(variable.get())
    except (tk.TclError, ValueError):
        return default


def _safe_int(data, key: str, default: int,
              minimum=None, maximum=None) -> int:
    try:
        value = int(data.get(key, default))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _safe_float(data, key: str, default: float,
                minimum=None, maximum=None) -> float:
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _safe_bool(data, key: str, default: bool) -> bool:
    value = data.get(key, default)
    return bool(value)


def _safe_choice(data, key: str, default: str, allowed) -> str:
    value = data.get(key, default)
    return value if value in allowed else default


def _safe_color(data, key: str, default: str) -> str:
    value = data.get(key, default)
    if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value.strip()):
        return value.strip().upper()
    return default


class BasePanel(ttk.Frame):
    """参数面板基类。"""

    title = ""

    def __init__(self, master):
        super().__init__(master, padding=(14, 12))
        self.columnconfigure(1, weight=1)
        self._build()

    # 子类实现
    def _build(self) -> None:
        raise NotImplementedError

    def apply(self, settings: TaskSettings) -> None:
        """把界面参数写入 settings。"""
        raise NotImplementedError

    def validate(self):
        """返回 (是否通过, 错误提示)。"""
        return True, ""

    def state(self) -> dict:
        """导出界面状态（用于参数记忆/预设）。"""
        return {}

    def load_state(self, data: dict) -> None:
        """从参数记忆/预设恢复界面状态。"""
        return

    # ---------- 控件辅助 ----------
    def _check(self, parent, text, variable, row, column=0, columnspan=1, padx=0):
        box = ttk.Checkbutton(parent, text=text, variable=variable)
        box.grid(row=row, column=column, columnspan=columnspan,
                 sticky=tk.W, pady=2, padx=padx)
        return box

    def _label(self, parent, text, row, column=0, sticky=tk.W, padx=0):
        label = ttk.Label(parent, text=text)
        label.grid(row=row, column=column, sticky=sticky, pady=2, padx=padx)
        return label


class CompressPanel(BasePanel):
    """压缩 / 缩放模式：移植自 DS_libvips、优化文件.py、压缩图片_2508、自适应压缩/高速。"""

    title = "压缩 / 缩放"

    def _build(self) -> None:
        root = self

        # ---- 尺寸限制 ----
        size_frame = ttk.Frame(root)
        size_frame.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=(0, 6))
        self._label(size_frame, "尺寸限制:", 0)

        self.preset_var = tk.StringVar(value=SIZE_PRESETS[-1][0])
        self.preset_box = ttk.Combobox(
            size_frame, textvariable=self.preset_var, width=18, state="readonly",
            values=[name for name, _w, _h in SIZE_PRESETS] + ["自定义"],
        )
        self.preset_box.grid(row=0, column=1, padx=(6, 12), sticky=tk.W)
        self.preset_box.bind("<<ComboboxSelected>>", self._on_preset_selected)

        ttk.Label(size_frame, text="宽").grid(row=0, column=2, padx=(6, 2))
        self.width_var = tk.IntVar(value=3840)
        self.width_spin = ttk.Spinbox(size_frame, from_=0, to=20000, increment=10,
                                      textvariable=self.width_var, width=7)
        self.width_spin.grid(row=0, column=3)

        ttk.Label(size_frame, text="高").grid(row=0, column=4, padx=(6, 2))
        self.height_var = tk.IntVar(value=2160)
        self.height_spin = ttk.Spinbox(size_frame, from_=0, to=20000, increment=10,
                                       textvariable=self.height_var, width=7)
        self.height_spin.grid(row=0, column=5)
        ttk.Label(size_frame, text="（0 = 不限制，等比缩放不放大）",
                  foreground="gray").grid(row=0, column=6, padx=(8, 0))

        self._applying_preset = False
        self.width_var.trace_add("write", self._on_size_edited)
        self.height_var.trace_add("write", self._on_size_edited)

        # ---- 质量 ----
        quality_frame = ttk.Frame(root)
        quality_frame.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 6))
        ttk.Label(quality_frame, text="质量:").pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="JPEG").pack(side=tk.LEFT, padx=(10, 2))
        self.jpeg_var = tk.IntVar(value=DEFAULT_JPEG_QUALITY)
        ttk.Spinbox(quality_frame, from_=1, to=100, textvariable=self.jpeg_var,
                    width=5).pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="PNG 压缩").pack(side=tk.LEFT, padx=(12, 2))
        self.png_var = tk.IntVar(value=DEFAULT_PNG_COMPRESSION)
        ttk.Spinbox(quality_frame, from_=0, to=9, textvariable=self.png_var,
                    width=3).pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="WebP").pack(side=tk.LEFT, padx=(12, 2))
        self.webp_var = tk.IntVar(value=DEFAULT_WEBP_QUALITY)
        ttk.Spinbox(quality_frame, from_=1, to=100, textvariable=self.webp_var,
                    width=5).pack(side=tk.LEFT)

        # ---- 输出格式 ----
        format_frame = ttk.Frame(root)
        format_frame.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(0, 6))
        ttk.Label(format_frame, text="输出格式:").pack(side=tk.LEFT)
        self.format_var = tk.StringVar(value=OutputFormat.KEEP.value)
        for text, value in (
            ("保留原格式", OutputFormat.KEEP.value),
            ("JPEG", OutputFormat.JPEG.value),
            ("PNG", OutputFormat.PNG.value),
            ("WebP", OutputFormat.WEBP.value),
            ("智能选择", OutputFormat.SMART.value),
        ):
            ttk.Radiobutton(format_frame, text=text, value=value,
                            variable=self.format_var).pack(side=tk.LEFT, padx=(8, 0))

        # ---- 选项 ----
        options = ttk.Frame(root)
        options.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(0, 6))
        self.adaptive_var = tk.BooleanVar(value=False)
        self._check(options, "自适应质量", self.adaptive_var, 0, 0)
        self.reencode_var = tk.BooleanVar(value=True)
        self._check(options, "尺寸/格式未变也重新编码", self.reencode_var, 0, 1, padx=(16, 0))
        self.strip_var = tk.BooleanVar(value=True)
        self._check(options, "清除元数据 (EXIF/ICC)", self.strip_var, 0, 2, padx=(16, 0))
        self.keep_larger_var = tk.BooleanVar(value=False)
        self._check(options, "结果更大时保留原图", self.keep_larger_var, 0, 3, padx=(16, 0))

        # ---- 透明背景 ----
        bg_frame = ttk.Frame(root)
        bg_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(0, 6))
        ttk.Label(bg_frame, text="透明区域背景色:").pack(side=tk.LEFT)
        self.bg_var = tk.StringVar(value=DEFAULT_BACKGROUND)
        self.bg_button = ttk.Button(bg_frame, textvariable=self.bg_var, width=10,
                                    command=self._pick_color)
        self.bg_button.pack(side=tk.LEFT, padx=6)
        ttk.Label(bg_frame, text="（透明图片转 JPEG 时使用）",
                  foreground="gray").pack(side=tk.LEFT)

        # ---- 删除原文件 ----
        self.delete_var = tk.BooleanVar(value=False)
        self._check(root, "成功后删除原文件（输出到其他目录时生效）", self.delete_var, 5)

        ttk.Label(
            root,
            text="提示：输出文件夹留空 = 覆盖原图；勾选“自适应质量”会按格式与像素数微调质量。",
            foreground="gray",
        ).grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

    # ---------- 事件 ----------
    def _on_preset_selected(self, _event=None) -> None:
        label = self.preset_var.get()
        for name, width, height in SIZE_PRESETS:
            if name == label:
                self._applying_preset = True
                try:
                    self.width_var.set(width)
                    self.height_var.set(height)
                finally:
                    self._applying_preset = False
                return

    def _on_size_edited(self, *_args) -> None:
        if self._applying_preset:
            return
        self._sync_preset_label()

    def _sync_preset_label(self) -> None:
        try:
            current = (self.width_var.get(), self.height_var.get())
        except tk.TclError:
            return
        for name, width, height in SIZE_PRESETS:
            if (width, height) == current:
                self.preset_var.set(name)
                return
        self.preset_var.set("自定义")

    def _pick_color(self) -> None:
        _rgb, hex_color = colorchooser.askcolor(color=self.bg_var.get(),
                                                title="选择透明区域背景色")
        if hex_color:
            self.bg_var.set(hex_color.upper())

    # ---------- 收集 ----------
    def apply(self, settings: TaskSettings) -> None:
        settings.max_width = max(0, int(self.width_var.get()))
        settings.max_height = max(0, int(self.height_var.get()))
        settings.jpeg_quality = int(self.jpeg_var.get())
        settings.png_compression = int(self.png_var.get())
        settings.webp_quality = int(self.webp_var.get())
        settings.output_format = OutputFormat(self.format_var.get())
        settings.adaptive_quality = bool(self.adaptive_var.get())
        settings.reencode_always = bool(self.reencode_var.get())
        settings.keep_original_if_larger = bool(self.keep_larger_var.get())
        settings.strip_metadata = bool(self.strip_var.get())
        settings.background_color = self.bg_var.get()
        settings.delete_original = bool(self.delete_var.get())

    def validate(self):
        try:
            quality = int(self.jpeg_var.get())
            if not 1 <= quality <= 100:
                return False, "JPEG 质量必须在 1~100 之间"
            if not 0 <= int(self.png_var.get()) <= 9:
                return False, "PNG 压缩级别必须在 0~9 之间"
            if not 1 <= int(self.webp_var.get()) <= 100:
                return False, "WebP 质量必须在 1~100 之间"
            width, height = int(self.width_var.get()), int(self.height_var.get())
            if width < 0 or height < 0:
                return False, "宽高不能为负数"
        except (tk.TclError, ValueError):
            return False, "尺寸或质量参数不是有效数字"
        return True, ""

    # ---------- 参数记忆 ----------
    def state(self) -> dict:
        return {
            "width": _var_int(self.width_var, 3840),
            "height": _var_int(self.height_var, 2160),
            "jpeg_quality": _var_int(self.jpeg_var, DEFAULT_JPEG_QUALITY),
            "png_compression": _var_int(self.png_var, DEFAULT_PNG_COMPRESSION),
            "webp_quality": _var_int(self.webp_var, DEFAULT_WEBP_QUALITY),
            "format": self.format_var.get(),
            "adaptive": bool(self.adaptive_var.get()),
            "reencode": bool(self.reencode_var.get()),
            "keep_larger": bool(self.keep_larger_var.get()),
            "strip": bool(self.strip_var.get()),
            "background": self.bg_var.get(),
            "delete_original": bool(self.delete_var.get()),
        }

    def load_state(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self._applying_preset = True
        try:
            self.width_var.set(_safe_int(data, "width", 3840, 0, 20000))
            self.height_var.set(_safe_int(data, "height", 2160, 0, 20000))
            self.jpeg_var.set(_safe_int(data, "jpeg_quality", DEFAULT_JPEG_QUALITY, 1, 100))
            self.png_var.set(_safe_int(data, "png_compression", DEFAULT_PNG_COMPRESSION, 0, 9))
            self.webp_var.set(_safe_int(data, "webp_quality", DEFAULT_WEBP_QUALITY, 1, 100))
            self.format_var.set(_safe_choice(
                data, "format", OutputFormat.KEEP.value,
                {fmt.value for fmt in OutputFormat},
            ))
            self.adaptive_var.set(_safe_bool(data, "adaptive", False))
            self.reencode_var.set(_safe_bool(data, "reencode", True))
            self.keep_larger_var.set(_safe_bool(data, "keep_larger", False))
            self.strip_var.set(_safe_bool(data, "strip", True))
            self.bg_var.set(_safe_color(data, "background", DEFAULT_BACKGROUND))
            self.delete_var.set(_safe_bool(data, "delete_original", False))
        finally:
            self._applying_preset = False
        self._sync_preset_label()


class ConvertPanel(BasePanel):
    """格式转换模式：移植自 png转换.py、PNG 批量转 JPG .py。"""

    title = "格式转换"

    def _build(self) -> None:
        root = self

        format_frame = ttk.Frame(root)
        format_frame.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))
        ttk.Label(format_frame, text="目标格式:").pack(side=tk.LEFT)
        self.format_var = tk.StringVar(value=OutputFormat.JPEG.value)
        for text, value in (
            ("JPEG", OutputFormat.JPEG.value),
            ("PNG", OutputFormat.PNG.value),
            ("WebP", OutputFormat.WEBP.value),
        ):
            ttk.Radiobutton(format_frame, text=text, value=value,
                            variable=self.format_var).pack(side=tk.LEFT, padx=(10, 0))

        quality_frame = ttk.Frame(root)
        quality_frame.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))
        ttk.Label(quality_frame, text="质量:").pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="JPEG").pack(side=tk.LEFT, padx=(10, 2))
        self.jpeg_var = tk.IntVar(value=DEFAULT_JPEG_QUALITY)
        ttk.Spinbox(quality_frame, from_=1, to=100, textvariable=self.jpeg_var,
                    width=5).pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="WebP").pack(side=tk.LEFT, padx=(12, 2))
        self.webp_var = tk.IntVar(value=DEFAULT_WEBP_QUALITY)
        ttk.Spinbox(quality_frame, from_=1, to=100, textvariable=self.webp_var,
                    width=5).pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="PNG 压缩").pack(side=tk.LEFT, padx=(12, 2))
        self.png_var = tk.IntVar(value=DEFAULT_PNG_COMPRESSION)
        ttk.Spinbox(quality_frame, from_=0, to=9, textvariable=self.png_var,
                    width=3).pack(side=tk.LEFT)

        bg_frame = ttk.Frame(root)
        bg_frame.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))
        ttk.Label(bg_frame, text="透明区域背景色:").pack(side=tk.LEFT)
        self.bg_var = tk.StringVar(value=DEFAULT_BACKGROUND)
        ttk.Button(bg_frame, textvariable=self.bg_var, width=10,
                   command=self._pick_color).pack(side=tk.LEFT, padx=6)
        ttk.Label(bg_frame, text="（透明图片转 JPEG 时使用）",
                  foreground="gray").pack(side=tk.LEFT)

        self.delete_var = tk.BooleanVar(value=False)
        self._check(root, "转换成功后删除源文件（可恢复性差，请谨慎）", self.delete_var, 3)

        self.strip_var = tk.BooleanVar(value=True)
        self._check(root, "清除元数据 (EXIF/ICC)", self.strip_var, 4)

        ttk.Label(
            root,
            text="说明：不改变分辨率，只重新编码为目标格式；输出文件夹留空 = 与原图同目录。",
            foreground="gray",
        ).grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

    def _pick_color(self) -> None:
        _rgb, hex_color = colorchooser.askcolor(color=self.bg_var.get(),
                                                title="选择透明区域背景色")
        if hex_color:
            self.bg_var.set(hex_color.upper())

    def apply(self, settings: TaskSettings) -> None:
        settings.convert_format = OutputFormat(self.format_var.get())
        settings.jpeg_quality = int(self.jpeg_var.get())
        settings.webp_quality = int(self.webp_var.get())
        settings.png_compression = int(self.png_var.get())
        settings.background_color = self.bg_var.get()
        settings.delete_original = bool(self.delete_var.get())
        settings.strip_metadata = bool(self.strip_var.get())

    def validate(self):
        try:
            if not 1 <= int(self.jpeg_var.get()) <= 100:
                return False, "JPEG 质量必须在 1~100 之间"
            if not 1 <= int(self.webp_var.get()) <= 100:
                return False, "WebP 质量必须在 1~100 之间"
            if not 0 <= int(self.png_var.get()) <= 9:
                return False, "PNG 压缩级别必须在 0~9 之间"
        except (tk.TclError, ValueError):
            return False, "质量参数不是有效数字"
        return True, ""

    # ---------- 参数记忆 ----------
    def state(self) -> dict:
        return {
            "format": self.format_var.get(),
            "jpeg_quality": _var_int(self.jpeg_var, DEFAULT_JPEG_QUALITY),
            "webp_quality": _var_int(self.webp_var, DEFAULT_WEBP_QUALITY),
            "png_compression": _var_int(self.png_var, DEFAULT_PNG_COMPRESSION),
            "background": self.bg_var.get(),
            "delete_original": bool(self.delete_var.get()),
            "strip": bool(self.strip_var.get()),
        }

    def load_state(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.format_var.set(_safe_choice(
            data, "format", OutputFormat.JPEG.value,
            {OutputFormat.JPEG.value, OutputFormat.PNG.value, OutputFormat.WEBP.value},
        ))
        self.jpeg_var.set(_safe_int(data, "jpeg_quality", DEFAULT_JPEG_QUALITY, 1, 100))
        self.webp_var.set(_safe_int(data, "webp_quality", DEFAULT_WEBP_QUALITY, 1, 100))
        self.png_var.set(_safe_int(data, "png_compression", DEFAULT_PNG_COMPRESSION, 0, 9))
        self.bg_var.set(_safe_color(data, "background", DEFAULT_BACKGROUND))
        self.delete_var.set(_safe_bool(data, "delete_original", False))
        self.strip_var.set(_safe_bool(data, "strip", True))


class UpscalePanel(BasePanel):
    """高清放大模式：移植自 DS放大.py、放大.py。"""

    title = "高清放大"

    def _build(self) -> None:
        root = self

        factor_frame = ttk.Frame(root)
        factor_frame.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))
        ttk.Label(factor_frame, text="放大倍数:").pack(side=tk.LEFT)
        self.factor_var = tk.DoubleVar(value=DEFAULT_UPSCALE_FACTOR)
        ttk.Spinbox(factor_frame, from_=1.0, to=4.0, increment=0.1,
                    textvariable=self.factor_var, width=6).pack(side=tk.LEFT, padx=6)
        ttk.Label(factor_frame, text="倍  (1.0 ~ 4.0)").pack(side=tk.LEFT)

        sharpen_frame = ttk.Frame(root)
        sharpen_frame.grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=(0, 8))
        ttk.Label(sharpen_frame, text="锐化强度:").pack(side=tk.LEFT)
        self.sharpen_var = tk.DoubleVar(value=DEFAULT_SHARPEN_SIGMA)
        ttk.Scale(sharpen_frame, from_=0.0, to=3.0, variable=self.sharpen_var,
                  orient=tk.HORIZONTAL, length=240).pack(side=tk.LEFT, padx=8)
        self.sharpen_label = ttk.Label(sharpen_frame, text="0.8", width=5)
        self.sharpen_label.pack(side=tk.LEFT)
        ttk.Label(sharpen_frame, text="（0 = 不锐化）", foreground="gray").pack(side=tk.LEFT)
        self.sharpen_var.trace_add("write", self._refresh_sharpen_label)

        self.delete_var = tk.BooleanVar(value=False)
        self._check(root, "放大成功后删除原始图片（谨慎）", self.delete_var, 2)

        self.strip_var = tk.BooleanVar(value=True)
        self._check(root, "清除元数据 (EXIF/ICC)", self.strip_var, 3)

        ttk.Label(
            root,
            text="说明：使用 Lanczos3 插值放大并可选锐化；输出文件夹留空时自动写入"
                 "“输入目录/高清放大输出”。",
            foreground="gray",
        ).grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

    def _refresh_sharpen_label(self, *_args) -> None:
        try:
            self.sharpen_label.config(text=f"{self.sharpen_var.get():.1f}")
        except tk.TclError:
            pass

    def apply(self, settings: TaskSettings) -> None:
        settings.upscale_factor = float(self.factor_var.get())
        settings.sharpen_sigma = float(self.sharpen_var.get())
        settings.delete_original = bool(self.delete_var.get())
        settings.strip_metadata = bool(self.strip_var.get())

    def validate(self):
        try:
            factor = float(self.factor_var.get())
            if not 1.0 <= factor <= 4.0:
                return False, "放大倍数必须在 1.0~4.0 之间"
            sigma = float(self.sharpen_var.get())
            if not 0.0 <= sigma <= 3.0:
                return False, "锐化强度必须在 0~3 之间"
        except (tk.TclError, ValueError):
            return False, "放大参数不是有效数字"
        return True, ""

    # ---------- 参数记忆 ----------
    def state(self) -> dict:
        return {
            "factor": _var_float(self.factor_var, DEFAULT_UPSCALE_FACTOR),
            "sharpen": _var_float(self.sharpen_var, DEFAULT_SHARPEN_SIGMA),
            "delete_original": bool(self.delete_var.get()),
            "strip": bool(self.strip_var.get()),
        }

    def load_state(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.factor_var.set(_safe_float(data, "factor", DEFAULT_UPSCALE_FACTOR, 1.0, 4.0))
        self.sharpen_var.set(_safe_float(data, "sharpen", DEFAULT_SHARPEN_SIGMA, 0.0, 3.0))
        self.delete_var.set(_safe_bool(data, "delete_original", False))
        self.strip_var.set(_safe_bool(data, "strip", True))
