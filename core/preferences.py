# core/preferences.py
"""参数记忆与预设。

保存两样东西：
  * last     —— 上一次使用的完整参数（含三个面板），启动时自动恢复；
  * presets  —— 用户命名的预设（如“4K 归档”“1080P 分享”）。

文件为 JSON，位于用户主目录（``~/.image_toolkit_settings.json``）。
所有读取都做类型保护，文件损坏或版本不符时静默回退到默认值。
"""

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Dict, Optional

from .constants import MAX_PRESETS, SETTINGS_FILE, SETTINGS_VERSION


class Preferences:
    """参数记忆/预设的读写。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path(SETTINGS_FILE)
        self._last: Dict = {}
        self._presets: Dict[str, Dict] = {}
        self.load()

    # ---------- 读写 ----------
    def load(self) -> None:
        try:
            if not self.path.exists():
                return
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return

        if not isinstance(payload, dict) or payload.get("version") != SETTINGS_VERSION:
            return

        last = payload.get("last")
        if isinstance(last, dict):
            self._last = last

        presets = payload.get("presets")
        if isinstance(presets, dict):
            cleaned = {}
            for name, state in presets.items():
                if isinstance(name, str) and name.strip() and isinstance(state, dict):
                    cleaned[name] = state
            self._presets = cleaned

    def save(self) -> bool:
        try:
            payload = {
                "version": SETTINGS_VERSION,
                "last": self._last,
                "presets": self._presets,
            }
            os.makedirs(self.path.parent, exist_ok=True)
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False

    # ---------- 上次使用 ----------
    @property
    def last(self) -> Dict:
        return deepcopy(self._last)

    def set_last(self, state: Dict) -> None:
        self._last = deepcopy(state)
        self.save()

    # ---------- 预设 ----------
    @property
    def preset_names(self):
        return sorted(self._presets.keys())

    def get_preset(self, name: str) -> Optional[Dict]:
        state = self._presets.get(name)
        return deepcopy(state) if isinstance(state, dict) else None

    def set_preset(self, name: str, state: Dict) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        if name not in self._presets and len(self._presets) >= MAX_PRESETS:
            return False
        self._presets[name] = deepcopy(state)
        self.save()
        return True

    def delete_preset(self, name: str) -> bool:
        if name in self._presets:
            del self._presets[name]
            self.save()
            return True
        return False
