# core/constants.py
"""常量与默认配置。"""

from pathlib import Path

from .enums import OutputFormat

# ---------------- 应用信息 ----------------
APP_NAME = "图片工具箱 (libvips)"
APP_VERSION = "2.3.0"
APP_TITLE = f"{APP_NAME} v{APP_VERSION}"

# ---------------- 图片格式 ----------------
# 可读取的扩展名（libvips 直接支持，或通过 ImageMagick/FFmpeg 兜底）
SUPPORTED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif',
    '.webp', '.gif', '.heic', '.heif',
}

# libvips 可以直接写出的扩展名；不在此集合内的格式保存时回退为 PNG
SAVEABLE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.gif', '.heic', '.heif',
}

FORMAT_TO_EXT = {
    OutputFormat.JPEG: '.jpg',
    OutputFormat.PNG: '.png',
    OutputFormat.WEBP: '.webp',
}

EXT_TO_FORMAT = {
    '.jpg': OutputFormat.JPEG,
    '.jpeg': OutputFormat.JPEG,
    '.png': OutputFormat.PNG,
    '.webp': OutputFormat.WEBP,
}

# HEIC/HEIF 源文件：libvips 常缺少 HEVC 编码器，统一转成 JPEG
HEIC_EXTENSIONS = {'.heic', '.heif'}

# ---------------- 尺寸预设（宽度, 高度，0 表示不限制） ----------------
SIZE_PRESETS = [
    ("不限制", 0, 0),
    ("1080P 1920×1080", 1920, 1080),
    ("2K 2560×1440", 2560, 1440),
    ("4K 3840×2160", 3840, 2160),
]

# ---------------- 质量控制 ----------------
DEFAULT_JPEG_QUALITY = 85
DEFAULT_PNG_COMPRESSION = 6
DEFAULT_WEBP_QUALITY = 80
QUALITY_MIN, QUALITY_MAX = 1, 100

# 自适应质量：最终质量 = 基准 × 格式系数 × 分辨率系数，再限制在 [MIN, MAX]，
# 并且不超过用户设定的质量上限。
ADAPTIVE_BASE_QUALITY = 80
ADAPTIVE_QUALITY_MIN = 50
ADAPTIVE_QUALITY_MAX = 90
ADAPTIVE_LARGE_PIXELS = 15_000_000   # 1500 万像素以上 → 降质量
ADAPTIVE_SMALL_PIXELS = 2_000_000    # 200 万像素以下 → 升质量

# ---------------- 放大 ----------------
DEFAULT_UPSCALE_FACTOR = 2.0
MIN_UPSCALE_FACTOR, MAX_UPSCALE_FACTOR = 1.0, 4.0
DEFAULT_SHARPEN_SIGMA = 0.8
MAX_SHARPEN_SIGMA = 3.0
UPSCALE_DEFAULT_OUTPUT = "高清放大输出"

# ---------------- 输出目录默认值 ----------------
CONVERT_DEFAULT_OUTPUT = "converted"
DEFAULT_BACKGROUND = "#FFFFFF"

# ---------------- 批处理 ----------------
DEFAULT_BATCH_COUNT = 50
DEFAULT_BATCH_SIZE_MB = 500
DEFAULT_SKIP_THRESHOLD_KB = 500
MIN_WORKERS, MAX_WORKERS = 2, 8

# ---------------- 缓存 ----------------
CACHE_FILE = Path.home() / ".image_toolkit_cache.json"
CACHE_VERSION = 1

# ---------------- 参数记忆 ----------------
SETTINGS_FILE = Path.home() / ".image_toolkit_settings.json"
SETTINGS_VERSION = 1
MAX_PRESETS = 20

# 临时文件后缀（写入完成后再原子替换目标文件）
TMP_MARK = ".tmp"
TMP_MAX_AGE_SECONDS = 24 * 3600
