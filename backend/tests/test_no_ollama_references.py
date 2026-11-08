# SPDX-License-Identifier: AGPL-3.0-or-later
"""B5 (#14) guard: no functional Ollama references remain in the backend.

Scans ``app/`` for the references B5 must eliminate — the Ollama port, the
nomic-embed default, the runner class, the ad-hoc tag plumbing, and Ollama
imports. Historical mentions in prose comments are allowed; anything the code
could actually execute is not.

The second half guards the same boundary by SHAPE: no value the code can execute
— an ``os.environ`` read's default in ``app/``, or an assignment in
``.env.example`` — may be an Ollama-era ``model:tag``. The models this repo runs
are directory names that carry no tag (``LFM3-1.7B-MLX-4bit``,
``Snowflake/snowflake-arctic-embed-s``).
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


# Ollama's ``model:tag`` naming — the runtime removed in B5 (#14). ``:latest``
# and a version tag (``:v1.5``, ``:8b``) are Ollama's forms; the /v1 proxy and
# the Model Manager identify a model by its directory name, so a tagged default
# can only be a leftover.
OLLAMA_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*:(latest|v?\d[A-Za-z0-9._-]*)$")

# Every ``os.environ.get('NAME', 'default')`` / ``os.getenv`` call that supplies
# a default: the default value is executed at import time.
_ENV_DEFAULT = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*['\"]([A-Z0-9_]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)"""
)

# A ``NAME=value`` line in .env.example (a commented example stays a comment).
_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(\S+)\s*$")


def _env_defaults(text):
    """``(line_no, variable, default)`` for every env read that supplies a default."""

    for match in _ENV_DEFAULT.finditer(text):
        yield text.count("\n", 0, match.start()) + 1, match.group(1), match.group(2)


def test_no_ollama_era_tag_as_an_executed_default():
    """#22 hygiene: an Ollama-era tag must not come back as a default.

    ``Config.LLM_MODEL_NAME``'s default used to be ``lfm3:latest`` — an Ollama
    tag from the runtime B5 (#14) removed. The running default is the `small`
    role's model id ``LFM3-1.7B-MLX-4bit``, assigned in the Model Manager.

    Only values the code can execute are checked: an ``os.environ`` read's
    default in ``app/`` and a live assignment in ``.env.example`` (which a
    contributor copies straight into ``.env``). A prose comment is not executed,
    so this guard leaves the FORBIDDEN scan's prose-comment allowance intact and
    adds no pattern to it."""

    offenders = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for line_no, name, default in _env_defaults(text):
            if OLLAMA_TAG.match(default):
                offenders.append(
                    f"{path.relative_to(APP_DIR.parent)}:{line_no}: {name} defaults to "
                    f"the Ollama tag {default!r}"
                )

    example = APP_DIR.parent.parent / ".env.example"
    for line_no, line in enumerate(
        example.read_text(encoding="utf-8").splitlines(), start=1
    ):
        assignment = _ENV_ASSIGNMENT.match(line)
        if assignment and OLLAMA_TAG.match(assignment.group(2)):
            offenders.append(
                f"{example.relative_to(APP_DIR.parent.parent)}:{line_no}: "
                f"{assignment.group(1)} is the Ollama tag {assignment.group(2)!r}"
            )

    assert not offenders, "Ollama-era tags still reachable as defaults:\n" + "\n".join(
        offenders
    )


def test_runtime_backends_are_only_mlx_and_gguf():
    from app.models.runtime import make_runner
    from app.models.store import ModelStore
    import inspect
    sig = inspect.signature(make_runner)
    assert "ollama_tag" not in sig.parameters
    src = inspect.getsource(make_runner)
    assert "ollama" not in src.lower()
