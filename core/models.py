# core/models.py
"""数据模型：处理设置、跳过策略、单张处理结果。"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Set, Tuple

from .constants import HEIC_EXTENSIONS, UPSCALE_DEFAULT_OUTPUT
from .enums import BatchMode, OperationMode, OutputFormat

# 参与缓存的设置字段（.tmp 之类的运行期参数不参与）
def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


@dataclass
class SkipPolicy:
    """扫描阶段的文件跳过策略。

    移植自旧版 SkipPolicy（大小 / 格式 / 最近修改），并补齐双阈值；
    尺寸过小检查需要读取图片头，放在 GUI 侧调用 read_image_size() 完成。
    """

    skip_larger: bool = False
    larger_threshold_kb: int = 500
    skip_smaller: bool = False
    smaller_threshold_kb: int = 10
    skip_recent: bool = False
    recent_hours: int = 1
    min_width: int = 0      # >0 时启用“尺寸过小跳过”
    min_height: int = 0
    skip_formats: Set[str] = field(default_factory=set)

    def should_skip(self, file_path: str) -> Tuple[bool, str]:
        """返回 (是否跳过, 原因)；仅做低成本检查（stat / 扩展名）。"""
        if not os.path.isfile(file_path):
            return True, "文件不存在"
        ext = os.path.splitext(file_path)[1].lower()
        if ext in self.skip_formats:
            return True, f"格式 {ext} 已排除"

        needs_stat = (self.skip_larger or self.skip_smaller or self.skip_recent)
        if not needs_stat:
            return False, ""

        try:
            stat = os.stat(file_path)
        except OSError as exc:
            return True, f"无法读取文件信息: {exc}"

        if self.skip_larger:
            threshold = max(0, self.larger_threshold_kb) * 1024
            if stat.st_size > threshold:
                return True, f"文件过大 ({stat.st_size / 1024:.0f}KB > {threshold / 1024:.0f}KB)"

        if self.skip_smaller:
            threshold = max(0, self.smaller_threshold_kb) * 1024
            if stat.st_size < threshold:
                return True, f"文件过小 ({stat.st_size / 1024:.0f}KB < {threshold / 1024:.0f}KB)"

        if self.skip_recent:
            hours = max(1, self.recent_hours)
            if time.time() - stat.st_mtime < hours * 3600:
                return True, f"最近 {hours} 小时内修改过"

        return False, ""


@dataclass
class TaskSettings:
    """一次批量任务的完整设置。

    合并了原三个工具的全部参数：
      * 压缩/缩放：max_width / max_height / output_format / 质量参数 / 自适应质量
      * 格式转换：convert_format / background_color
      * 高清放大：upscale_factor / sharpen_sigma
    """

    # 模式与目录
    mode: OperationMode = OperationMode.COMPRESS
    input_dir: str = ""
    output_dir: str = ""
    recursive: bool = True
    preserve_structure: bool = True

    # 压缩 / 缩放
    max_width: int = 3840
    max_height: int = 2160
    output_format: OutputFormat = OutputFormat.SMART
    reencode_always: bool = True     # 尺寸/格式未变化时仍然重新编码（压缩工具默认行为）
    keep_original_if_larger: bool = False  # 结果比原图大时放弃写入，保留原图

    # 格式转换
    convert_format: OutputFormat = OutputFormat.JPEG

    # 质量
    jpeg_quality: int = 85
    png_compression: int = 6
    webp_quality: int = 80
    adaptive_quality: bool = False

    # 通用图像选项
    background_color: str = "#FFFFFF"   # 透明通道压平时使用的背景色
    strip_metadata: bool = True         # 去除 EXIF / ICC 等元数据

    # 高清放大
    upscale_factor: float = 2.0
    sharpen_sigma: float = 0.8          # 0 表示不锐化

    # 文件处理
    delete_original: bool = False       # 提交成功后删除源文件
    backup_original: bool = True        # 覆盖/删除前把原图移入回收站（可还原）
    skip_policy: SkipPolicy = field(default_factory=SkipPolicy)

    # 批处理
    batch_mode: BatchMode = BatchMode.BY_COUNT
    batch_count: int = 50
    batch_size_mb: int = 500
    workers: int = 0                    # 0 = 自动

    # 附加功能
    use_cache: bool = True              # 跳过未变化的文件（按 mtime/大小/参数哈希）
    save_statistics: bool = False       # 输出 JSON 统计报告

    # ---------- 便捷方法 ----------
    def resolve_output_format(self, src_ext: str) -> OutputFormat:
        """按模式决定输出格式（不涉及 SMART 的图片特征判断）。"""
        src_ext = src_ext.lower()
        if src_ext in HEIC_EXTENSIONS:
            return OutputFormat.JPEG
        if self.mode is OperationMode.UPSCALE:
            # 放大模式保持原格式（HEIC 已在上面转为 JPEG）
            return OutputFormat.KEEP
        if self.mode is OperationMode.CONVERT:
            fmt = self.convert_format
            return OutputFormat.KEEP if fmt is OutputFormat.KEEP else fmt
        return self.output_format

    def target_root(self, image_path: str) -> Optional[str]:
        """返回输出根目录；None 表示写回源文件所在目录。"""
        if self.output_dir:
            return self.output_dir
        if self.mode is OperationMode.UPSCALE:
            base = self.input_dir or os.path.dirname(image_path)
            return os.path.join(base, UPSCALE_DEFAULT_OUTPUT)
        return None

    def cache_signature(self, src_ext: str) -> str:
        """生成与“处理结果”相关的设置指纹，用于跳过未变化的文件。"""
        payload = {
            "mode": self.mode.value,
            "ext": src_ext.lower(),
            "output_dir": os.path.normcase(os.path.abspath(self.output_dir)) if self.output_dir else "",
            "preserve_structure": bool(self.preserve_structure),
            "output_format": self.output_format.value,
            "convert_format": self.convert_format.value,
            "size": (int(self.max_width), int(self.max_height)),
            "reencode": bool(self.reencode_always),
            "quality": (int(self.jpeg_quality), int(self.webp_quality), int(self.png_compression)),
            "adaptive": bool(self.adaptive_quality),
            "background": self.background_color.upper(),
            "strip": bool(self.strip_metadata),
            "keep_larger": bool(self.keep_original_if_larger),
            "delete_original": bool(self.delete_original),
            "upscale": (_round(self.upscale_factor), _round(self.sharpen_sigma)),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()


@dataclass
class ImageResult:
    """单张图片的处理结果。"""

    file_path: str
    original_size: int
    output_size: int
    original_resolution: Tuple[int, int]
    output_resolution: Tuple[int, int]
    format_changed: bool
    new_format: str
    success: bool
    need_delete: bool = False
    tmp_path: str = ""
    target_path: str = ""
    error_message: str = ""
    elapsed_ms: float = 0.0
    skipped: bool = False
    skip_reason: str = ""
    quality_used: int = 0

    # ---------- 展示 ----------
    @property
    def saved_bytes(self) -> int:
        return max(0, self.original_size - self.output_size)

    @property
    def reduction_percent(self) -> float:
        if self.original_size <= 0:
            return 0.0
        return (1 - self.output_size / self.original_size) * 100
