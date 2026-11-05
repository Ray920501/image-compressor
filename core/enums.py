# core/enums.py
"""全局枚举定义。"""

from enum import Enum


class OutputFormat(Enum):
    """输出格式。"""

    KEEP = "keep"      # 保持原格式
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"
    SMART = "smart"    # 按图片特征自动选择（仅压缩模式）


class BatchMode(Enum):
    """分批方式。"""

    BY_COUNT = "count"   # 按张数
    BY_SIZE = "size"     # 按总大小(MB)


class OperationMode(Enum):
    """处理模式（三个合并进来的工具）。"""

    COMPRESS = "compress"   # 压缩 / 缩放（DS_libvips、优化文件、压缩图片_2508、自适应压缩）
    CONVERT = "convert"     # 格式转换（PNG 批量转 JPG、png转换）
    UPSCALE = "upscale"     # 高清放大（DS放大、放大）
