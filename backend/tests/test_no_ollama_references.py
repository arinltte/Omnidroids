# SPDX-License-Identifier: AGPL-3.0-or-later
"""B5 (#14) guard: no functional Ollama references remain in the backend.

Scans ``app/`` for the references B5 must eliminate — the Ollama port, the
nomic-embed default, the runner class, the ad-hoc tag plumbing, and Ollama
imports. Historical mentions in prose comments are allowed; anything the code
could actually execute is not.
"""

import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# (pattern, what it would mean if present)
FORBIDDEN = [
    (r"11434", "the Ollama default port"),
    (r"nomic-embed", "the nomic-embed embedding default"),
    (r"OllamaRunner", "the Ollama runner class"),
    (r"ollama_tag", "the Ollama ad-hoc tag plumbing"),
    (r"keep_alive", "the Ollama eviction call"),
    (r"get_graphiti_zep_client\s*\(", "unrelated—kept as a sanity non-match"),
]


def _py_files():
    return sorted(APP_DIR.rglob("*.py"))


def test_no_functional_ollama_references_in_app():
    offenders = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for pattern, meaning in FORBIDDEN[:-1]:  # last tuple is a sanity non-match
            for m in re.finditer(pattern, text, flags=re.IGNORECASE):
                line_no = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{line_no}: {meaning}")
    assert not offenders, "Ollama references still reachable:\n" + "\n".join(offenders)


def test_no_ollama_imports():
    for path in _py_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "ollama" in stripped.lower():
                pytest.fail(f"{path.name}: Ollama import survived: {stripped}")


def test_runtime_backends_are_only_mlx_and_gguf():
    from app.models.runtime import make_runner
    from app.models.store import ModelStore
    import inspect
    sig = inspect.signature(make_runner)
    assert "ollama_tag" not in sig.parameters
    src = inspect.getsource(make_runner)
    assert "ollama" not in src.lower()
