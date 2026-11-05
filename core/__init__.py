# core/__init__.py
"""图片工具箱核心模块。"""

from .cache import ProcessedCache
from .constants import (
    APP_NAME,
    APP_TITLE,
    APP_VERSION,
    SIZE_PRESETS,
    SUPPORTED_EXTENSIONS,
)
from .enums import BatchMode, OperationMode, OutputFormat
from .models import ImageResult, SkipPolicy, TaskSettings
from .processor import (
    calculate_target_size,
    commit_results,
    load_image,
    predict_target_path,
    process_single_image,
    read_image_size,
    resolve_target_path,
)
from .stats import export_statistics
from .utils import (
    collect_images,
    human_duration,
    human_size,
    parse_color,
    recommend_workers,
    split_batches,
    total_size,
)

__all__ = [
    "APP_NAME",
    "APP_TITLE",
    "APP_VERSION",
    "BatchMode",
    "ImageResult",
    "OperationMode",
    "OutputFormat",
    "ProcessedCache",
    "SIZE_PRESETS",
    "SUPPORTED_EXTENSIONS",
    "SkipPolicy",
    "TaskSettings",
    "calculate_target_size",
    "collect_images",
    "commit_results",
    "export_statistics",
    "human_duration",
    "human_size",
    "load_image",
    "parse_color",
    "predict_target_path",
    "process_single_image",
    "read_image_size",
    "recommend_workers",
    "resolve_target_path",
    "split_batches",
    "total_size",
]
