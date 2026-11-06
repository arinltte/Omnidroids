"""Installed-model registry + role assignments (lightweight JSON, git-ignored).

Everything local lives under ``Config.MODELS_DIR`` (the Omnidroids ``models/``
folder). This registry records *what* is installed, *which* files, and which
*slo* (small/big/embed) each model is assigned to. It deliberately stores no
weights — only paths + metadata.

Roles:
    small — parallel worker model (ingestion, personas, fan-out)
    big   — heavier model for report synthesis + Q&A (sequential phases)
    embed — local embedding model
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("omnidroids.models.store")

ROLES = ("small", "big", "embed")


def sanitize_name(repo_id: str) -> str:
    """Turn a HF repo id / path into a safe folder name."""
    name = (repo_id or "").strip().rstrip("/")
    name = name.split("/")[-1] if "/" in name else name
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in name) or "model"


class ModelStore:
    def __init__(self, path: Optional[str] = None):
        self._path = path or Config.MODELS_REGISTRY_PATH
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {"models": {}, "roles": {}}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._data.setdefault("models", {})
                self._data.setdefault("roles", {})
                self._data["models"] = raw.get("models", {}) or {}
                self._data["roles"] = raw.get("roles", {}) or {}
        except Exception as exc:
            logger.warning("model registry unreadable (%s); starting fresh", exc)
            self._data = {"models": {}, "roles": {}}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # -- models ------------------------------------------------------------
    def list_models(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._data["models"].values()]

    def get_model(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            m = self._data["models"].get(name)
            return dict(m) if m else None

    def add_model(
        self, *, name: str, repo_id: str, path: str, backend: str,
        size_bytes: int = 0,
    ) -> Dict[str, Any]:
        entry = {
            "name": name,
            "repo_id": repo_id,
            "path": path,
            "backend": backend,
            "size_bytes": int(size_bytes or 0),
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with self._lock:
            self._data["models"][name] = entry
            self._save()
        # Best-effort disk size if not supplied.
        if not entry["size_bytes"]:
            entry["size_bytes"] = dir_size_bytes(path)
            with self._lock:
                self._save()
        return dict(entry)

    def remove_model(self, name: str) -> bool:
        with self._lock:
            existed = name in self._data["models"]
            self._data["models"].pop(name, None)
            for role in list(self._data["roles"].keys()):
                if self._data["roles"].get(role) == name:
                    self._data["roles"][role] = None
            self._save()
        return existed

    # -- roles -------------------------------------------------------------
    def get_roles(self) -> Dict[str, Optional[str]]:
        with self._lock:
            return {r: self._data["roles"].get(r) for r in ROLES}

    def model_for_role(self, role: str) -> Optional[str]:
        with self._lock:
            name = self._data["roles"].get(role)
            if name and name in self._data["models"]:
                return name
            return None

    def set_role(self, role: str, name: Optional[str]) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r} (expected one of {ROLES})")
        with self._lock:
            if name is not None and name not in self._data["models"]:
                raise ValueError(f"model {name!r} is not installed")
            self._data["roles"][role] = name
            self._save()


def dir_size_bytes(path: str) -> int:
    total = 0
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        pass
    return total
