# core/cache.py
"""已处理文件缓存：避免重复处理未变化的图片（吸收自“优化文件.py”的哈希记录）。

记录内容为 {绝对路径: {"h": 指纹, "t": 时间戳}}，
指纹 = md5(修改时间 + 文件大小 + 处理参数)，因此修改参数后会自动重新处理。
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .constants import CACHE_FILE, CACHE_VERSION

_MAX_RECORDS = 50_000


class ProcessedCache:
    """线程安全的处理记录缓存。"""

    def __init__(self, path: Optional[Path] = None, enabled: bool = True):
        self.path = Path(path) if path else Path(CACHE_FILE)
        self.enabled = enabled
        self._records: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._loaded = False

    # ---------- 读写 ----------
    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.enabled:
            return
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION:
                    records = payload.get("records")
                    if isinstance(records, dict):
                        self._records = records
        except Exception:
            self._records = {}

    def save(self) -> None:
        if not self.enabled or not self._dirty:
            return
        try:
            self._prune()
            payload = {"version": CACHE_VERSION, "records": self._records}
            tmp = str(self.path) + ".tmp"
            os.makedirs(self.path.parent, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._dirty = True
        self.save()

    # ---------- 指纹 ----------
    @staticmethod
    def file_fingerprint(file_path: str, signature: str) -> Optional[str]:
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        raw = f"{stat.st_mtime_ns}:{stat.st_size}:{signature}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    # ---------- 查询 / 记录 ----------
    def is_processed(self, file_path: str, signature: str) -> bool:
        if not self.enabled:
            return False
        key = os.path.normcase(os.path.abspath(file_path))
        fingerprint = self.file_fingerprint(file_path, signature)
        if fingerprint is None:
            return False
        with self._lock:
            record = self._records.get(key)
        if not record or record.get("h") != fingerprint:
            return False

        # 若记录过输出文件，确认它仍然存在；否则视为未处理（例如输出目录被删）
        target = record.get("d")
        if target and not os.path.exists(target):
            return False
        return True

    def mark(self, file_path: str, signature: str,
             target_path: Optional[str] = None) -> None:
        """处理成功后记录当前文件状态（覆盖模式下读取的是新文件）。"""
        if not self.enabled:
            return
        key = os.path.normcase(os.path.abspath(file_path))
        fingerprint = self.file_fingerprint(file_path, signature)
        if fingerprint is None:
            return
        with self._lock:
            record = {"h": fingerprint, "t": int(time.time())}
            if target_path:
                record["d"] = os.path.normcase(os.path.abspath(target_path))
            self._records[key] = record
            self._dirty = True

    # ---------- 维护 ----------
    def _prune(self) -> None:
        if len(self._records) <= _MAX_RECORDS:
            return
        ordered = sorted(self._records.items(), key=lambda item: item[1].get("t", 0))
        drop = len(self._records) - _MAX_RECORDS
        for key, _value in ordered[:drop]:
            self._records.pop(key, None)

    def __len__(self) -> int:
        return len(self._records)
