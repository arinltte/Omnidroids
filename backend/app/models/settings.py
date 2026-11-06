# SPDX-License-Identifier: AGPL-3.0-or-later
"""Model Manager settings — concurrency, RAM budget, idle auto-unload (B2, #11).

Persisted to ``models/settings.json`` (git-ignored, machine-local — same policy
as the registry). The manager reads these at every acquire/idle tick, so a
change made in the Model Manager takes effect without a backend restart.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("omnidroids.models.settings")

# key -> (low, high) inclusive bounds
_RANGES = {
    "concurrency": (1, 16),          # max co-resident models
    "ram_budget_gb": (1.0, 64.0),    # weights+KV envelope for co-residency
    "idle_unload_seconds": (5, 3600),  # idle safety-net before hard unload
}
_INT_KEYS = {"concurrency", "idle_unload_seconds"}


class SettingsError(ValueError):
    """Invalid settings payload (surfaced verbatim to the API caller)."""


def _defaults() -> Dict[str, Any]:
    return {
        "concurrency": max(_RANGES["concurrency"][0],
                           int(getattr(Config, "MODEL_MAX_CO_RESIDENT", 4))),
        "ram_budget_gb": float(Config.MODEL_RAM_BUDGET_GB),
        "idle_unload_seconds": int(Config.MODEL_IDLE_UNLOAD_SECONDS),
    }


def _validate(key: str, value: Any) -> Any:
    if key not in _RANGES:
        raise SettingsError(f"unknown setting {key!r} (expected one of {sorted(_RANGES)})")
    lo, hi = _RANGES[key]
    if key in _INT_KEYS:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{key} must be an integer (got {value!r})")
        if not (lo <= value <= hi):
            raise SettingsError(f"{key} must be within [{lo}, {hi}] (got {value})")
        return int(value)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{key} must be a number (got {value!r})") from None
    if isinstance(value, bool) or not (lo <= num <= hi):
        raise SettingsError(f"{key} must be within [{lo}, {hi}] GB (got {value})")
    return num


class ModelSettings:
    """Thread-safe JSON settings store; ``update`` merges + validates + persists."""

    def __init__(self, path: Optional[str] = None):
        self._path = path or Config.MODEL_SETTINGS_PATH
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = _defaults()
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                merged = _defaults()
                for k, v in (raw or {}).items():
                    try:
                        merged[k] = _validate(k, v)
                    except SettingsError:
                        logger.warning("ignoring invalid persisted setting %s=%r", k, v)
                self._data = merged
        except Exception as exc:
            logger.warning("settings unreadable (%s); using defaults", exc)
            self._data = _defaults()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def update(self, patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        values = dict(patch or {})
        with self._lock:
            new = dict(self._data)
            for k, v in values.items():
                new[k] = _validate(k, v)  # validates BEFORE anything is written
            self._data = new
            self._save()
            logger.info("model settings updated: %s", new)
            return dict(new)


_SETTINGS: Optional[ModelSettings] = None
_SETTINGS_LOCK = threading.Lock()


def get_settings() -> ModelSettings:
    global _SETTINGS
    with _SETTINGS_LOCK:
        if _SETTINGS is None:
            _SETTINGS = ModelSettings()
        return _SETTINGS
