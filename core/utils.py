# core/utils.py
"""通用工具函数：文件扫描、分批、颜色解析、线程数推荐、临时文件清理。"""

import os
import subprocess
import sys
import time
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

from .constants import (
    MAX_WORKERS,
    MIN_WORKERS,
    SUPPORTED_EXTENSIONS,
    TMP_MARK,
    TMP_MAX_AGE_SECONDS,
)
from .models import TaskSettings


# ---------------- 展示 ----------------
def human_size(num_bytes: float) -> str:
    """把字节数格式化为易读字符串。"""
    num = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(num)} B"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


def human_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


# ---------------- 文件扫描 ----------------
def collect_images(root_dir: str, recursive: bool = True) -> List[str]:
    """扫描目录下的图片文件并返回排序后的路径列表。"""
    if not root_dir or not os.path.isdir(root_dir):
        return []

    files: List[str] = []
    if recursive:
        for current_root, _dirs, names in os.walk(root_dir):
            for name in names:
                if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                    files.append(os.path.join(current_root, name))
    else:
        try:
            with os.scandir(root_dir) as entries:
                for entry in entries:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in SUPPORTED_EXTENSIONS:
                        files.append(entry.path)
        except OSError:
            return []

    files.sort(key=lambda p: os.path.normcase(p))
    return files


def total_size(files: Iterable[str]) -> int:
    size = 0
    for path in files:
        try:
            size += os.path.getsize(path)
        except OSError:
            continue
    return size


# ---------------- 分批 ----------------
def split_batches(files: Sequence[str], settings: TaskSettings) -> Iterator[List[str]]:
    """按张数或按总大小把文件切成多批。"""
    if not files:
        return

    if settings.batch_mode.value == "count":
        count = max(1, int(settings.batch_count))
        for index in range(0, len(files), count):
            yield list(files[index:index + count])
        return

    max_bytes = max(1, int(settings.batch_size_mb)) * 1024 * 1024
    batch: List[str] = []
    batch_bytes = 0
    for path in files:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if batch and batch_bytes + size > max_bytes:
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(path)
        batch_bytes += size
    if batch:
        yield batch


# ---------------- 并行度 ----------------
def recommend_workers(file_count: int, avg_size_bytes: float) -> int:
    """根据 CPU 数量、文件数量与平均大小推荐进程数。

    libvips 本身是多线程的，因此进程数取 CPU 的一半左右，避免线程过度订阅；
    超大文件再额外减少进程数，控制内存峰值。
    """
    cpu = os.cpu_count() or 4
    workers = max(1, cpu // 2)
    if avg_size_bytes > 20 * 1024 * 1024:
        workers -= 2
    elif avg_size_bytes > 8 * 1024 * 1024:
        workers -= 1
    workers = max(MIN_WORKERS, min(MAX_WORKERS, workers))
    if file_count > 0:
        workers = min(workers, max(1, file_count))
    return max(1, workers)


# ---------------- 颜色 ----------------
_NAMED_COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
}


def parse_color(value: str, default: Tuple[int, int, int] = (255, 255, 255)) -> List[int]:
    """把 '#RRGGBB' / '#RGB' / 'r,g,b' / 颜色名 解析为 [r, g, b]。"""
    if not value:
        return list(default)
    text = str(value).strip()

    if text.lower() in _NAMED_COLORS:
        return list(_NAMED_COLORS[text.lower()])

    if text.startswith("#"):
        token = text[1:]
        if len(token) == 3 and all(c in "0123456789abcdefABCDEF" for c in token):
            return [int(c * 2, 16) for c in token]
        if len(token) == 6 and all(c in "0123456789abcdefABCDEF" for c in token):
            return [int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16)]

    parts = [p.strip() for p in text.replace("，", ",").split(",")]
    if len(parts) == 3:
        try:
            rgb = [max(0, min(255, int(round(float(p))))) for p in parts]
            return rgb
        except ValueError:
            pass
    return list(default)


# ---------------- 临时文件 ----------------
def is_temp_file(name: str) -> bool:
    return TMP_MARK in name.lower()


def cleanup_orphan_temps(
    directories: Iterable[str],
    max_age_seconds: int = TMP_MAX_AGE_SECONDS,
    log=None,
) -> int:
    """删除目录中超过指定时间的残留临时文件，返回删除数量。"""
    now = time.time()
    removed = 0
    seen = set()

    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        real = os.path.normcase(os.path.abspath(directory))
        if real in seen:
            continue
        seen.add(real)

        for current_root, dirs, names in os.walk(directory):
            # 跳过隐藏目录与虚拟环境，避免无谓遍历
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if not name.lower().endswith((".tmp",)) and TMP_MARK not in name.lower():
                    continue
                path = os.path.join(current_root, name)
                try:
                    if now - os.path.getmtime(path) < max_age_seconds:
                        continue
                    os.remove(path)
                    removed += 1
                except OSError:
                    continue
    if removed and log:
        log(f"清理了 {removed} 个残留临时文件")
    return removed


# ---------------- 系统集成 ----------------
def open_folder(path: str) -> bool:
    """在资源管理器中打开目录。"""
    if not path or not os.path.isdir(path):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def unique_path(path: str) -> str:
    """若目标已存在，追加 (1)、(2)… 直到不冲突。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 1
    while True:
        candidate = f"{stem} ({index}){ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1
