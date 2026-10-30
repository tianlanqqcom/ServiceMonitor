"""跨平台数据目录与设置持久化。"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .models import DEFAULT_RETRY_RESET_AFTER

APP_NAME = "ServiceMonitor"
ENV_HOME = "SERVICEMONITOR_HOME"

# dataclass 在 `from __future__ import annotations` 下把类型存成字符串，需要自己映射
_COERCIONS = {"float": float, "int": int, "bool": bool}


def data_dir() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        root = Path(override).expanduser()
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        root = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        root = Path(base) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return data_dir() / "monitor.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


CLOSE_ACTION_TRAY = "tray"
CLOSE_ACTION_QUIT = "quit"
DEFAULT_FONT_POINT_SIZE = 10.0
MIN_FONT_POINT = 7.0
MAX_FONT_POINT = 24.0


def clamp_point_size(value) -> float:
    """字号只能落在可读区间，脏数据回退到默认值。"""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return DEFAULT_FONT_POINT_SIZE
    return max(MIN_FONT_POINT, min(MAX_FONT_POINT, size))


@dataclass
class Settings:
    sample_interval: float = 2.0
    gpu_enabled: bool = True
    gpu_interval: float = 5.0
    stop_on_exit: bool = True
    close_action: str = CLOSE_ACTION_TRAY
    font_family: str = ""
    font_point_size: float = DEFAULT_FONT_POINT_SIZE
    stop_grace: float = 5.0
    retention_days: int = 0
    default_restart_delay: float = 5.0
    restart_reset_after: float = DEFAULT_RETRY_RESET_AFTER
    max_chart_points: int = 240
    output_page_size: int = 2000

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        defaults = cls()
        try:
            stored = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        values = {}
        for f in fields(cls):
            raw = stored.get(f.name, getattr(defaults, f.name))
            caster = _COERCIONS.get(f.type if isinstance(f.type, str) else "")
            try:
                values[f.name] = caster(raw) if caster else raw
            except (TypeError, ValueError):
                values[f.name] = getattr(defaults, f.name)
        return cls(**values)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


class SettingsStore:
    """设置读写：GUI 线程与后台线程共享同一份内存副本。"""

    def __init__(self, path: Path | None = None):
        self._lock = threading.RLock()
        self._path = path or settings_path()
        self._settings = Settings.load(self._path)

    @property
    def values(self) -> Settings:
        with self._lock:
            return self._settings

    def update(self, **changes) -> Settings:
        with self._lock:
            for key, value in changes.items():
                if hasattr(self._settings, key):
                    setattr(self._settings, key, value)
            self._write()
            return self._settings

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(self._settings.to_json(), encoding="utf-8")
        os.replace(tmp, self._path)
