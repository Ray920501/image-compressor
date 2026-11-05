# core/stats.py
"""统计报告导出（吸收自旧版 _save_statistics，并补全为可用的 JSON 报告）。"""

import json
import os
from datetime import datetime
from typing import Dict, Iterable, Optional

from .models import ImageResult, TaskSettings


def _settings_payload(settings: TaskSettings) -> Dict:
    return {
        "mode": settings.mode.value,
        "input_dir": settings.input_dir,
        "output_dir": settings.output_dir,
        "recursive": settings.recursive,
        "preserve_structure": settings.preserve_structure,
        "max_width": settings.max_width,
        "max_height": settings.max_height,
        "output_format": settings.output_format.value,
        "convert_format": settings.convert_format.value,
        "reencode_always": settings.reencode_always,
        "keep_original_if_larger": settings.keep_original_if_larger,
        "jpeg_quality": settings.jpeg_quality,
        "png_compression": settings.png_compression,
        "webp_quality": settings.webp_quality,
        "adaptive_quality": settings.adaptive_quality,
        "background_color": settings.background_color,
        "strip_metadata": settings.strip_metadata,
        "upscale_factor": settings.upscale_factor,
        "sharpen_sigma": settings.sharpen_sigma,
        "delete_original": settings.delete_original,
        "batch_mode": settings.batch_mode.value,
        "batch_count": settings.batch_count,
        "batch_size_mb": settings.batch_size_mb,
        "use_cache": settings.use_cache,
    }


def export_statistics(
    results: Iterable[ImageResult],
    settings: TaskSettings,
    summary: Dict,
    base_dir: Optional[str] = None,
) -> Optional[str]:
    """写出 JSON 统计报告，返回文件路径（失败返回 None）。"""
    results = list(results)
    target_dir = base_dir or settings.output_dir or settings.input_dir
    if not target_dir or not os.path.isdir(target_dir):
        return None

    path = os.path.join(
        target_dir,
        f"图片处理统计_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )

    files = []
    for result in results:
        files.append(
            {
                "file": result.file_path,
                "target": result.target_path,
                "status": "skipped" if result.skipped else ("ok" if result.success else "failed"),
                "reason": result.skip_reason or result.error_message,
                "original_bytes": result.original_size,
                "output_bytes": result.output_size,
                "original_size": list(result.original_resolution),
                "output_size": list(result.output_resolution),
                "format": result.new_format,
                "quality": result.quality_used,
                "elapsed_ms": round(result.elapsed_ms, 1),
            }
        )

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "settings": _settings_payload(settings),
        "summary": summary,
        "file_count": len(files),
        "files": files,
    }

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return path
    except OSError:
        return None
