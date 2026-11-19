#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ensure_models — first-run model provisioning for the one-click launcher.

The launcher (`Omnidroids.sh`) calls this so a fresh clone works with no manual
steps: it checks that the two role models and the local embedder are present,
downloads whatever is missing from HuggingFace into `models/`, and writes
`models/registry.json` with the two roles assigned. Everything stays inside the
repository folder (`HF_HOME` is exported by the launcher before this runs).

Usage:
    python scripts/ensure_models.py --check       # exit 0 when nothing is missing
    python scripts/ensure_models.py --provision   # download what is missing, write registry

Exit codes: 0 everything present/ready · 1 something missing (--check) ·
2 provisioning failed (the reason is printed verbatim).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

# The two production roles (the Model Manager UI can reassign them later).
# MLX weights, macOS/Apple-Silicon only — the .sh launcher is the macOS path.
PROVISION_MODELS: List[Dict[str, str]] = [
    {
        "name": "LFM3-1.7B-MLX-4bit",
        "repo_id": "LiquidAI/LFM3-1.7B-MLX-4bit",
        "role": "small",
        "backend": "mlx",
    },
    {
        "name": "Ornith-1.5-9B-MLX-6bit",
        "repo_id": "ornith-ai/Ornith-1.5-9B-MLX-6bit",
        "role": "big",
        "backend": "mlx",
    },
]

# The local embedding model the graph build resolves in-process.
EMBEDDER_REPO = "Snowflake/snowflake-arctic-embed-s"

# What "present on disk" means for an MLX weight directory.
WEIGHT_GLOBS = ("*.safetensors", "*.gguf")


def _log(message: str) -> None:
    print(f"[ensure-models] {message}", flush=True)


def _confine_hf_cache() -> None:
    """Keep every HuggingFace artifact inside this repository's .cache/hf.

    huggingface_hub resolves HF_HOME at import time, so this runs before the
    first hub import (module import order: the download import is lazy inside
    download_model/ensure_embedder) and only fills in what the launcher has not
    already exported."""
    cache_root = os.environ.get("HF_CACHE_DIR") or str(REPO_ROOT / ".cache" / "hf")
    os.environ.setdefault("HF_HOME", cache_root)
    os.environ.setdefault("HF_HUB_CACHE", os.path.join(cache_root, "hub"))
    os.environ.setdefault("HF_XET_CACHE", os.path.join(cache_root, "xet"))


def has_weights(model_dir: Path) -> bool:
    """True when a model directory holds at least one weights file."""
    if not model_dir.is_dir():
        return False
    return any(
        model_dir.glob(pattern) for pattern in WEIGHT_GLOBS
    )


def dir_size_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def load_registry(registry_path: Path) -> Dict:
    if not registry_path.is_file():
        return {"models": {}, "roles": {}}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"models": {}, "roles": {}}
    if not isinstance(data, dict):
        return {"models": {}, "roles": {}}
    data.setdefault("models", {})
    data.setdefault("roles", {})
    return data


def registry_entry(spec: Dict[str, str], model_dir: Path) -> Dict:
    return {
        "name": spec["name"],
        "repo_id": spec["repo_id"],
        "path": str(model_dir),
        "backend": spec["backend"],
        "size_bytes": dir_size_bytes(model_dir),
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def missing_report(models_dir: Path) -> List[Dict[str, str]]:
    """The provision list filtered to what is actually missing on disk."""
    return [
        spec for spec in PROVISION_MODELS
        if not has_weights(models_dir / spec["name"])
    ]


def download_model(spec: Dict[str, str], models_dir: Path) -> Path:
    """One HuggingFace snapshot into models/<name>. Raises on failure."""
    from huggingface_hub import snapshot_download  # imported lazily: the check path needs no hub

    target = models_dir / spec["name"]
    target.mkdir(parents=True, exist_ok=True)
    _log(f"downloading {spec['repo_id']} -> {target} (this is a one-time wait)")
    snapshot_download(
        repo_id=spec["repo_id"],
        local_dir=str(target),
    )
    if not has_weights(target):
        raise RuntimeError(
            f"{spec['repo_id']} downloaded but no weights file found in {target}"
        )
    return target


def ensure_embedder() -> bool:
    """Pre-fetch the local embedder into the confined HF cache.

    The graph build would otherwise download it mid-run; doing it here keeps the
    first simulation's timeline predictable. A failure is not fatal — the
    platform downloads on first use — so this reports and returns.
    """
    try:
        from huggingface_hub import snapshot_download

        _log(f"pre-fetching the local embedder {EMBEDDER_REPO}")
        snapshot_download(repo_id=EMBEDDER_REPO)
        return True
    except Exception as exc:  # noqa: BLE001 — reported, never fatal here
        _log(f"embedder pre-fetch skipped ({type(exc).__name__}: {exc}); "
             "the platform will fetch it on first use")
        return False


def provision(models_dir: Path, registry_path: Path) -> int:
    """Download what is missing, then write the registry with both roles."""
    registry = load_registry(registry_path)
    todo = missing_report(models_dir)
    for spec in PROVISION_MODELS:
        entry = registry["models"].get(spec["name"])
        if entry and not has_weights(models_dir / spec["name"]):
            todo.append(spec)  # a registered model whose weights vanished
    todo_ids = {spec["name"] for spec in todo}
    if not todo_ids:
        _log("both role models are already on disk")
    for spec in todo:
        try:
            target = download_model(spec, models_dir)
        except Exception as exc:  # noqa: BLE001 — the platform's own reason, verbatim
            _log(f"FAILED to provision {spec['name']}: {type(exc).__name__}: {exc}")
            return 2
        registry["models"][spec["name"]] = registry_entry(spec, target)
    # keep every model already registered (the Model Manager may hold more)
    for spec in PROVISION_MODELS:
        model_dir = models_dir / spec["name"]
        if spec["name"] not in registry["models"] and has_weights(model_dir):
            registry["models"][spec["name"]] = registry_entry(spec, model_dir)
        if has_weights(model_dir):
            registry["roles"][spec["role"]] = spec["name"]
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _log(f"registry written: {registry_path} "
         f"(roles: {json.dumps(registry['roles'], ensure_ascii=False)})")
    ensure_embedder()
    return 0


def check(models_dir: Path, registry_path: Path) -> int:
    """Report only: exit 0 when both role models have weights and a registry
    assigns both roles to present models."""
    missing = missing_report(models_dir)
    registry = load_registry(registry_path)
    unassigned = [
        role for role in ("small", "big")
        if registry["roles"].get(role) not in {
            spec["name"] for spec in PROVISION_MODELS if has_weights(models_dir / spec["name"])
        }
    ]
    if missing or unassigned:
        for spec in missing:
            _log(f"missing model weights: {spec['name']} ({spec['repo_id']})")
        for role in unassigned:
            _log(f"role not assigned to a present model: {role}")
        return 1
    _log("both role models present and assigned; the embedder will be "
         "fetched on first use")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="report only; exit 1 when something is missing")
    group.add_argument("--provision", action="store_true",
                       help="download missing models and write the registry")
    parser.add_argument("--models-dir", default=str(REPO_ROOT / "models"))
    parser.add_argument("--registry", default=None,
                        help="registry path (default: <models-dir>/registry.json)")
    args = parser.parse_args(argv)

    models_dir = Path(args.models_dir).expanduser()
    registry_path = Path(args.registry) if args.registry else models_dir / "registry.json"
    _confine_hf_cache()
    if args.check:
        return check(models_dir, registry_path)
    return provision(models_dir, registry_path)


if __name__ == "__main__":
    sys.exit(main())