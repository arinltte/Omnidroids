# SPDX-License-Identifier: AGPL-3.0-or-later
"""ensure_models — the launcher's first-run provisioning contract.

`scripts/ensure_models.py` is what makes the one-click launcher work on a fresh
clone: it checks the two role models and the local embedder, downloads what is
missing, and writes `models/registry.json` with both roles assigned. Hermetic:
temp directories, the HuggingFace download stubbed, no network.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ensure_models.py"

_spec = importlib.util.spec_from_file_location("ensure_models", SCRIPT)
ensure_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ensure_models)


def _stub_download(monkeypatch, names):
    """Replace the HuggingFace snapshot with a stub weights file per model."""
    def fake_download(spec, models_dir):
        target = models_dir / spec["name"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "model-00001-of-00002.safetensors").write_bytes(b"stub")
        return target

    monkeypatch.setattr(ensure_models, "download_model", fake_download)
    return names


def _with_weights(models_dir, name):
    path = models_dir / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "model.safetensors").write_bytes(b"stub")


def test_check_reports_missing_models(tmp_path, capsys):
    code = ensure_models.main(["--check", "--models-dir", str(tmp_path)])
    assert code == 1
    out = capsys.readouterr().out
    assert "LFM3-1.7B-MLX-4bit" in out
    assert "Ornith-1.5-9B-MLX-6bit" in out
    assert "role not assigned" in out


def test_provision_writes_registry_with_both_roles(tmp_path, monkeypatch, capsys):
    _stub_download(monkeypatch, None)
    monkeypatch.setattr(ensure_models, "ensure_embedder", lambda: True)
    code = ensure_models.main(["--provision", "--models-dir", str(tmp_path)])
    assert code == 0

    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert registry["roles"] == {
        "small": "LFM3-1.7B-MLX-4bit",
        "big": "Ornith-1.5-9B-MLX-6bit",
    }
    for name in ("LFM3-1.7B-MLX-4bit", "Ornith-1.5-9B-MLX-6bit"):
        assert (tmp_path / name / "model-00001-of-00002.safetensors").is_file()
        entry = registry["models"][name]
        assert entry["backend"] == "mlx"
        assert entry["repo_id"].startswith(("LiquidAI/", "ornith-ai/"))
        assert entry["size_bytes"] > 0

    # and the same machine then reads as fully provisioned
    assert ensure_models.main(["--check", "--models-dir", str(tmp_path)]) == 0
    capsys.readouterr()


def test_check_passes_when_weights_and_registry_present(tmp_path):
    _with_weights(tmp_path, "LFM3-1.7B-MLX-4bit")
    _with_weights(tmp_path, "Ornith-1.5-9B-MLX-6bit")
    (tmp_path / "registry.json").write_text(json.dumps({
        "models": {},
        "roles": {"small": "LFM3-1.7B-MLX-4bit", "big": "Ornith-1.5-9B-MLX-6bit"},
    }), encoding="utf-8")
    assert ensure_models.main(["--check", "--models-dir", str(tmp_path)]) == 0


def test_registered_model_with_vanished_weights_is_missing(tmp_path):
    _with_weights(tmp_path, "LFM3-1.7B-MLX-4bit")
    (tmp_path / "registry.json").write_text(json.dumps({
        "models": {"Ornith-1.5-9B-MLX-6bit": {"name": "Ornith-1.5-9B-MLX-6bit"}},
        "roles": {"small": "LFM3-1.7B-MLX-4bit", "big": "Ornith-1.5-9B-MLX-6bit"},
    }), encoding="utf-8")
    # the big model is registered and assigned, but its weights are gone
    assert ensure_models.main(["--check", "--models-dir", str(tmp_path)]) == 1


def test_provision_preserves_other_registered_models(tmp_path, monkeypatch):
    _stub_download(monkeypatch, None)
    monkeypatch.setattr(ensure_models, "ensure_embedder", lambda: False)
    _with_weights(tmp_path, "Some-Custom-GGUF")   # a model the user added later
    (tmp_path / "registry.json").write_text(json.dumps({
        "models": {"Some-Custom-GGUF": {"name": "Some-Custom-GGUF",
                                        "repo_id": "someone/Some-Custom-GGUF",
                                        "backend": "gguf"}},
        "roles": {},
    }), encoding="utf-8")
    assert ensure_models.main(["--provision", "--models-dir", str(tmp_path)]) == 0
    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert registry["models"]["Some-Custom-GGUF"]["backend"] == "gguf"
    assert registry["roles"] == {
        "small": "LFM3-1.7B-MLX-4bit",
        "big": "Ornith-1.5-9B-MLX-6bit",
    }


def test_provision_failure_reports_the_platform_reason(tmp_path, monkeypatch, capsys):
    def boom(spec, models_dir):
        raise RuntimeError("hf down: 503 from the hub")

    monkeypatch.setattr(ensure_models, "download_model", boom)
    code = ensure_models.main(["--provision", "--models-dir", str(tmp_path)])
    assert code == 2
    out = capsys.readouterr().out
    assert "FAILED to provision" in out
    assert "503 from the hub" in out