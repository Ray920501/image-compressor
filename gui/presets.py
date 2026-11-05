# gui/presets.py
"""内置参数预设（首次启动自动写入，之后可自行修改/删除）。

三套预设对应真实照片验收报告中的推荐：
  * 归档 4K    —— 保留原格式、固定 Q85，适合长期保存；
  * 分享 2K    —— 转 JPEG、自适应质量，兼容性最好；
  * 社交 1080p —— 转 JPEG、自适应质量，体积最小。

预设只覆盖参数，不改动输入/输出文件夹。
"""

from core.enums import OutputFormat


def _compress_state(**overrides) -> dict:
    state = {
        "width": 3840,
        "height": 2160,
        "jpeg_quality": 85,
        "png_compression": 6,
        "webp_quality": 80,
        "format": OutputFormat.SMART.value,
        "adaptive": False,
        "reencode": True,
        "keep_larger": False,
        "strip": True,
        "background": "#FFFFFF",
        "delete_original": False,
    }
    state.update(overrides)
    return state


def _full_state(compress: dict) -> dict:
    return {
        "mode": "compress",
        "recursive": True,
        "preserve": True,
        "batch_mode": "count",
        "batch_count": 50,
        "batch_size_mb": 500,
        "skip_larger": False,
        "skip_larger_kb": 500,
        "skip_smaller": False,
        "skip_smaller_kb": 10,
        "skip_recent": False,
        "recent_hours": 1,
        "skip_min_dim": False,
        "min_width": 100,
        "min_height": 100,
        "use_cache": True,
        "save_stats": False,
        "workers": 0,
        "backup": True,
        "compress": compress,
        "convert": {
            "format": OutputFormat.JPEG.value,
            "jpeg_quality": 85,
            "webp_quality": 80,
            "png_compression": 6,
            "background": "#FFFFFF",
            "delete_original": False,
            "strip": True,
        },
        "upscale": {
            "factor": 2.0,
            "sharpen": 0.8,
            "delete_original": False,
            "strip": True,
        },
    }


BUILTIN_PRESETS = {
    "归档 4K": _full_state(_compress_state(
        width=3840,
        height=2160,
        format=OutputFormat.KEEP.value,
        adaptive=False,
        keep_larger=True,
    )),
    "分享 2K": _full_state(_compress_state(
        width=2560,
        height=1440,
        format=OutputFormat.JPEG.value,
        adaptive=True,
        keep_larger=True,
    )),
    "社交 1080p": _full_state(_compress_state(
        width=1920,
        height=1080,
        format=OutputFormat.JPEG.value,
        adaptive=True,
        keep_larger=True,
    )),
}
