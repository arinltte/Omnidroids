# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry guardrail — every assigned role points at a real, loadable model (finding 25).

The suite's other registry tests (``test_step_model_assignment.py:_write_registry``)
point their fixture ``path``s at directories that do not exist, so "the role names a
model whose weights are on disk" was untested — the deletion/deregistration class
that #54 guarded by hand (``scratch/fix54_keeper_loadcheck_*.json``) and the
``small``-role swap proved with two more hand-written scripts
(``scratch/check_small_role.py``, ``scratch/verify_small_role_serving.py``).

This is the check that retires the hand-written proof. It is still deterministic:
no weights are ever loaded — the guard asserts the disk preconditions a load needs
(registered entry, existing non-empty weights tree, a resolvable backend) plus the
``slot_for_model`` round-trip. When the real (git-ignored) ``models/registry.json``
is present it runs against the live registry, which is what catches a stale role on
this machine; a clean checkout runs the same invariants against a fixture whose
"weights" are stub files.
"""

import json
from pathlib import Path

import pytest

from app.config import Config
from app.models.manager import ModelManager
from app.models.runtime import detect_backend
from app.models.store import ModelStore


def _stub_weights(root: Path, name: str) -> Path:
    """A minimal mlx-layout weights tree: enough files for ``detect_backend`` to
    resolve 'mlx' and for a size walk to be non-zero. No real weights are read."""

    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text('{"architectures": ["stub"]}', encoding="utf-8")
    (d / "model.safetensors").write_bytes(b"\x00" * 1024)
    return d


def _fixture_store(tmp_path, *, roles, models, backend="mlx") -> ModelStore:
    for name in models:
        _stub_weights(tmp_path, name)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "models": {
            name: {
                "name": name,
                "repo_id": f"repo/{name}",
                "path": str(tmp_path / name),
                "backend": backend,
                "size_bytes": 1024,
            }
            for name in models
        },
        "roles": roles,
    }), encoding="utf-8")
    return ModelStore(str(path))


@pytest.fixture(params=["real", "fixture"])
def store(request, tmp_path):
    """The real registry when ``models/`` is present, else the stub fixture."""

    if request.param == "real":
        registry = Path(Config.MODELS_REGISTRY_PATH)
        if not registry.exists():
            pytest.skip("no local models/registry.json (git-ignored)")
        return ModelStore(str(registry))
    return _fixture_store(
        tmp_path,
        roles={"small": "stub-small", "big": "stub-big"},
        models=("stub-small", "stub-big"),
    )


def _assert_role_points_at_a_loadable_model(store: ModelStore, slot: str) -> None:
    """One role's row of the guard. An unassigned role is legal (embed is unset
    on this machine); an assigned one must survive the whole chain."""

    assigned = store.get_roles().get(slot)
    if assigned is None:
        return
    from app.models.store import dir_size_bytes

    assert store.model_for_role(slot) == assigned, (
        f"roles.{slot} -> {assigned!r} is not an installed registry model"
    )
    entry = store.get_model(assigned)
    assert entry is not None, f"{assigned!r} is not listed in the registry"
    path = Path(entry["path"])
    assert path.is_dir(), f"{assigned!r} weights path is missing: {path}"
    assert dir_size_bytes(str(path)) > 0, f"{assigned!r} weights tree is empty: {path}"
    assert detect_backend(str(path)) == entry.get("backend"), (
        f"{assigned!r} weights tree does not match its registered backend "
        f"{entry.get('backend')!r} (resolved {detect_backend(str(path))!r})"
    )
    manager = ModelManager(store=store)
    assert manager.slot_for_model(assigned) == slot, (
        f"slot_for_model({assigned!r}) lost the {slot!r} assignment"
    )


def test_every_assigned_role_points_at_an_installed_loadable_model(store):
    """The guard itself: real registry on this machine, stub fixture anywhere."""

    for slot in ("small", "big", "embed"):
        _assert_role_points_at_a_loadable_model(store, slot)


# -- the guard catches the class #54 guarded by hand -------------------------
def test_a_role_whose_model_left_the_registry_fails_the_guard(tmp_path):
    store = _fixture_store(tmp_path, roles={"small": "gone", "big": "stub-big"},
                           models=("stub-big",))

    with pytest.raises(AssertionError, match="not an installed registry model"):
        _assert_role_points_at_a_loadable_model(store, "small")


def test_a_role_whose_weights_tree_was_deleted_fails_the_guard(tmp_path):
    store = _fixture_store(tmp_path, roles={"small": "stub-small", "big": "stub-big"},
                           models=("stub-small", "stub-big"))
    for f in (tmp_path / "stub-small").iterdir():
        f.unlink()

    with pytest.raises(AssertionError, match="weights tree is empty"):
        _assert_role_points_at_a_loadable_model(store, "small")


def test_a_role_whose_weights_do_not_match_the_registered_backend_fails_the_guard(tmp_path):
    """A listed-but-wrong layout ('gguf' label over an mlx tree) is the load-path
    trap; the backend leg of the guard catches it before any load is attempted."""

    store = _fixture_store(tmp_path, roles={"small": "stub-small", "big": "stub-big"},
                           models=("stub-small", "stub-big"), backend="gguf")

    with pytest.raises(AssertionError, match="does not match its registered backend"):
        _assert_role_points_at_a_loadable_model(store, "small")
