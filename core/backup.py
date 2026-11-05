# core/backup.py
"""覆盖/删除前的原图备份。

Windows 本地磁盘：调用 shell 的 ``SHFileOperationW`` 把原图移入**回收站**
（可通过资源管理器还原，不占额外空间）。
网络盘、可移动盘或回收站不可用时：退化为在图片旁边创建 ``.originals`` 目录，
用 ``copy2`` 保存一份原图，并记录日志。

备份失败时调用方应放弃该文件的提交，避免原图在无备份的情况下被覆盖。
"""

import ctypes
import os
import shutil
import stat
import sys
import time
import uuid
from typing import Callable, Optional

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
DRIVE_FIXED = 3

BACKUP_DIR_NAME = ".originals"

if sys.platform == "win32":
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    try:
        _SHFileOperationW = ctypes.windll.shell32.SHFileOperationW
        _SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        _SHFileOperationW.restype = ctypes.c_int
    except Exception:  # pragma: no cover - 极少数环境没有 shell32
        _SHFileOperationW = None
else:  # pragma: no cover
    _SHFILEOPSTRUCTW = None
    _SHFileOperationW = None


def _is_fixed_drive(path: str) -> bool:
    """只有本地固定磁盘才有可靠的回收站；网络/可移动盘直接走目录备份。"""
    if sys.platform != "win32":
        return False
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return False
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_FIXED
    except Exception:
        return False


def recycle_file(path: str) -> bool:
    """把文件移入回收站，成功返回 True。"""
    if sys.platform != "win32" or _SHFileOperationW is None:
        return False
    if not os.path.exists(path) or not _is_fixed_drive(path):
        return False

    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass

    try:
        operation = _SHFILEOPSTRUCTW()
        operation.hwnd = None
        operation.wFunc = FO_DELETE
        # pFrom 需要以双 NUL 结尾；ctypes 会再补一个，因此这里加一个 "\0"
        operation.pFrom = os.path.abspath(path) + "\0"
        operation.pTo = None
        operation.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION
                            | FOF_SILENT | FOF_NOERRORUI)
        result = _SHFileOperationW(ctypes.byref(operation))
        return (result == 0
                and not operation.fAnyOperationsAborted
                and not os.path.exists(path))
    except Exception:
        return False


def _copy_to_backup_folder(path: str) -> Optional[str]:
    """复制到同目录 .originals，返回备份路径。"""
    try:
        folder = os.path.join(os.path.dirname(os.path.abspath(path)), BACKUP_DIR_NAME)
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(path))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(folder, f"{stem}_{stamp}_{uuid.uuid4().hex[:4]}{ext}")
        shutil.copy2(path, dest)
        return dest
    except Exception:
        return None


def backup_original(
    path: str,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """备份原图：优先回收站，失败则复制到 .originals；都失败返回 False。"""
    if not os.path.exists(path):
        return True

    name = os.path.basename(path)
    if recycle_file(path):
        if log:
            log(f"[备份] 原图已移入回收站: {name}")
        return True

    dest = _copy_to_backup_folder(path)
    if dest:
        if log:
            log(f"[备份] 原图已复制到: {dest}")
        return True

    if log:
        log(f"[警告] 无法备份原图（{name}），为安全起见跳过该文件")
    return False
