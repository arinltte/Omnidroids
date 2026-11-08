# SPDX-License-Identifier: AGPL-3.0-or-later
"""#40 guard: the decided backend stays behind the graph interface (ADR 0001).

The bake-off (#36 → #37 → #38) decided in #39 to **keep Graphiti + embedded
Kuzu** (ADR 0001, ``docs/adr/0001-graph-build-backend.md``). The interface rule
that decided it is ``docs/GRAPH_BAKEOFF.md`` §3 item 10: a winner must sit behind
Omnidroids' graph interface (``client.graph.*`` / ``client.batch.*``) and never
leak its SDK into services. This file is that rule as an executable scan of
``app/``, so a future backend swap cannot land "just a direct import" in a
service.

Terminology used below (one spelling each):

* **seam** — the graph-interface layer under ``app/utils/``: ``zep.py`` (the
  backend selector, ``get_zep_client``) plus ``zep_paging.py`` (its paging
  helper). The seam never changes for a backend swap.
* **adapter** — the module that knows a backend's SDK. For the decided backend
  that is ``app/utils/graphiti_zep.py`` (``GraphitiZepClient``); a swapped
  backend gets its own sibling adapter, not code inside a service.
* **interface names** — the Zep-compatible vocabulary services already speak
  (``client.graph.*`` / ``client.batch.*`` and the payload/exception types those
  calls take). Sharing them is the point of the seam, not a leak.

The boundary asserted here comes from that design, not from the current shape of
the tree: the adapter set is written out below, so adding a module to it is a
deliberate act that repeats the ADR's argument.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
BACKEND_DIR = Path(__file__).resolve().parents[1]

# The graph-interface layer: it selects a backend and pages through an interface
# client. Everything here may name a graph SDK; nothing else may.
SEAM_LAYER = (
    "app/utils/zep.py",
    "app/utils/zep_paging.py",
)

# The adapter for the DECIDED backend (ADR 0001: Graphiti + embedded Kuzu). It
# is the only module in the tree that may import a graph-backend SDK.
ADAPTER = "app/utils/graphiti_zep.py"

# Roots of the graph-backend SDKs. ``kuzu`` is listed although today it is only
# reached through ``graphiti_core.driver.kuzu_driver``: a bare ``import kuzu``
# anywhere would be the same leak. ``zep`` is the legacy Zep SDK package B5
# replaced; ``cognee``/``semantica`` are the bake-off's losing candidates. None
# of them may be imported by anything but the adapter.
BACKEND_SDKS = ("graphiti_core", "kuzu", "cognee", "semantica", "zep")

# The Zep Cloud SDK is the interface dialect (``client.graph.*`` /
# ``client.batch.*`` is its method surface), so it is allowed in the seam layer;
# outside it, a service may only carry the payload and exception TYPES those
# interface calls take — never the client constructor.
INTERFACE_SDK = "zep_cloud"
# The interface TYPES a service may still carry: the payload and exception shapes
# the client.graph.* / client.batch.* calls take. They are the interface itself
# (the seam mirrors them), which is why they are not a backend leak.
INTERFACE_TYPE_NAMES = frozenset(
    {
        "NotFoundError",
        "BatchAddItem",
        "EntityEdgeSourceTarget",
        "EntityModel",
        "EntityText",
        "EdgeModel",
    }
)

# Client constructors: a graph client may only be built by the seam selector, and
# the decided backend's engine only by its adapter.
CLIENT_CONSTRUCTORS = {"Zep", "GraphitiZepClient"}
ENGINE_CONSTRUCTORS = {"Graphiti", "KuzuDriver"}

# The services layer (step 1's drivers and the tools layer) must obtain its
# graph client through the seam's accessor.
SERVICE_LAYER = ("app/services",)
SEAM_ACCESSOR = "get_zep_client"


def _app_modules():
    """Every ``app/**/*.py`` as ``(relative module path, path)``."""

    return sorted(
        (path.relative_to(BACKEND_DIR).as_posix(), path)
        for path in APP_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module):
    """``(lineno, root, module, names)`` for every import, nested ones included.

    Absolute and relative imports are both collected: ``_ingest_record`` and
    ``_search`` import the backend SDK lazily *inside* functions, and a lazy leak
    is still a leak.
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                yield (node.lineno, root, alias.name, [])
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0] if node.level == 0 else ""
            names = [alias.name for alias in node.names]
            yield (node.lineno, root, node.module or "", names)


def _called_names(tree: ast.Module):
    """``(lineno, callee name)`` for every call: ``Name(...)`` or ``mod.Name(...)``.

    A constructor reached through a module attribute is the same constructor —
    graphiti's own examples spell ``KuzuDriver`` as
    ``graphiti_core.driver.kuzu_driver.KuzuDriver(...)`` — so the attribute
    form is yielded as well, or a service could build a graph client through an
    imported module and never appear in the boundary scan.
    """

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            yield (node.lineno, node.func.id)
        elif isinstance(node.func, ast.Attribute):
            yield (node.lineno, node.func.attr)


def test_graph_backend_sdks_are_imported_only_by_their_adapter():
    """No module outside the adapter may import a graph-backend SDK.

    ``app/utils/zep.py`` selects a backend and ``app/utils/zep_paging.py`` pages
    through an interface client; neither imports ``graphiti_core``/``kuzu`` — only
    the adapter does. If this fails, the offender is the module to route back
    through the seam, not the adapter list to widen.
    """

    offenders = []
    for module, path in _app_modules():
        if module == ADAPTER:
            continue
        for lineno, root, imported, _names in _imported_modules(_parse(path)):
            if root in BACKEND_SDKS:
                offenders.append(f"{module}:{lineno}: import {imported}")

    assert not offenders, (
        "a graph-backend SDK is imported outside its adapter "
        f"({ADAPTER}); services must reach the graph through the seam "
        "(`app/utils/zep.py`):\n  " + "\n  ".join(offenders)
    )


def test_services_carry_only_interface_types_from_a_graph_sdk():
    """Outside the seam layer a graph SDK may supply interface types, not clients.

    ``NotFoundError``, ``BatchAddItem`` and the ontology bases are the
    Zep-compatible payload/exception vocabulary the seam mirrors, so services
    build with them. ``Zep`` / ``zep_cloud.client`` is a backend client
    constructor and belongs to the seam layer only.
    """

    offenders = []
    for module, path in _app_modules():
        if module in SEAM_LAYER or module == ADAPTER:
            continue
        for lineno, root, imported, names in _imported_modules(_parse(path)):
            if root != INTERFACE_SDK:
                continue
            leaked = set(names) - INTERFACE_TYPE_NAMES
            if leaked:
                offenders.append(f"{module}:{lineno}: import {imported} -> {sorted(leaked)}")

    assert not offenders, (
        "a graph client is imported outside the seam layer "
        f"({', '.join(SEAM_LAYER)}); a service may only carry the interface "
        f"types {sorted(INTERFACE_TYPE_NAMES)}:\n  "
        + "\n  ".join(offenders)
    )


def test_losing_bakeoff_candidates_never_entered_app():
    """ADR 0001 kept Graphiti: cognee/semantica code must not reach ``app/``.

    Both candidates live in their own trees (``Prediction/{cognee,semantica}``)
    and their SDKs were only ever installed in isolated bake-off venvs. This is
    the grep-able half of that: nothing under ``app/`` may name them, so a swap
    cannot arrive as an unrecorded import.
    """

    offenders = []
    for module, path in _app_modules():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "cognee" in line.lower() or "semantica" in line.lower():
                offenders.append(f"{module}:{lineno}: {line.strip()[:80]}")

    assert not offenders, (
        "a losing bake-off candidate's name appears under app/ — ADR 0001 kept "
        "Graphiti, so a swap needs its own ticket and adapter:\n  "
        + "\n  ".join(offenders)
    )


def test_only_the_seam_layer_constructs_graph_clients():
    """A graph client is built by the seam selector, by nothing else."""

    offenders = []
    for module, path in _app_modules():
        if module in SEAM_LAYER or module == ADAPTER:
            continue
        for lineno, name in _called_names(_parse(path)):
            if name in CLIENT_CONSTRUCTORS:
                offenders.append(f"{module}:{lineno}: {name}(...)")

    assert not offenders, (
        "a module outside the seam layer constructs a graph client; it must take "
        "one from `get_zep_client()` instead:\n  " + "\n  ".join(offenders)
    )


def test_only_the_adapter_constructs_the_decided_backend_engine():
    """The Graphiti engine and its Kuzu driver are built inside the adapter."""

    offenders = []
    for module, path in _app_modules():
        if module == ADAPTER:
            continue
        for lineno, name in _called_names(_parse(path)):
            if name in ENGINE_CONSTRUCTORS:
                offenders.append(f"{module}:{lineno}: {name}(...)")

    assert not offenders, (
        "the decided backend's engine is constructed outside its adapter "
        f"({ADAPTER}):\n  " + "\n  ".join(offenders)
    )


def test_the_constructor_scan_sees_attribute_form_calls():
    """``module.GraphitiZepClient(...)`` is a constructor call too.

    A module that builds a graph client or the decided backend's engine through
    a module attribute reaches the same constructor as a bare
    ``GraphitiZepClient(...)`` — graphiti's own examples spell it that way
    (``graphiti_core.driver.kuzu_driver.KuzuDriver(...)``) — so the boundary
    scan must name both spellings or the attribute form is a hole in it.
    """

    tree = ast.parse(
        "import app.utils.graphiti_zep as graphiti_zep\n"
        "from graphiti_core.driver import kuzu_driver\n"
        "engine = kuzu_driver.KuzuDriver()\n"
        "client = graphiti_zep.GraphitiZepClient()\n"
    )
    names = {name for _lineno, name in _called_names(tree)}
    assert names >= {"GraphitiZepClient", "KuzuDriver"}, (
        "an attribute-form constructor call escaped the boundary scan; "
        "_called_names must yield the callee's attribute name as well as its "
        "bare Name form: " + repr(sorted(names))
    )


def test_services_reach_the_graph_through_the_seam_client_names():
    """Every services-layer driver of ``client.graph.*`` owns a seam client.

    A service that drives the graph obtains it from ``get_zep_client()`` (the
    seam) and then speaks the interface names; a module that touches
    ``client.graph.`` / ``client.batch.`` without that accessor resolved its
    client from somewhere else.
    """

    offenders = []
    for module, path in _app_modules():
        if not module.startswith(SERVICE_LAYER):
            continue
        text = path.read_text(encoding="utf-8")
        if "client.graph." not in text and "client.batch." not in text:
            continue
        imports_seam_accessor = any(
            root == ""  # relative import from a sibling: ``from ..utils.zep import``
            and "utils.zep" in (imported or "")
            and SEAM_ACCESSOR in names
            for _lineno, root, imported, names in _imported_modules(_parse(path))
        )
        if not imports_seam_accessor:
            offenders.append(module)

    assert not offenders, (
        "a services-layer module drives the graph without the seam accessor "
        f"(`{SEAM_ACCESSOR}` from `app/utils/zep.py`): " + ", ".join(offenders)
    )


def test_shipped_graph_backend_resolves_to_the_decided_seam():
    """The shipped ``GRAPH_BACKEND=graphiti`` selection resolves to the adapter.

    This is the durable half of "the seam is the shipped default": the value the
    repo ships (``.env.example``, and the environment ``docs/GRAPH_BAKEOFF.md``
    records) must keep selecting the local Graphiti client that ADR 0001 kept.
    """

    from app.config import Config
    from app.utils import graphiti_zep, zep

    env_example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
    shipped = [
        line.split("=", 1)[1].strip()
        for line in env_example.splitlines()
        if line.strip().startswith("GRAPH_BACKEND=")
    ]
    assert shipped == ["graphiti"], (
        "the shipped graph backend is not the decided one; ADR 0001 keeps "
        "Graphiti + embedded Kuzu: " + repr(shipped)
    )

    zep.clear_zep_client_cache()
    client = zep.get_zep_client()

    assert type(client).__module__ == "app.utils.graphiti_zep"
    assert isinstance(client, graphiti_zep.GraphitiZepClient)
    assert Config.is_local_graph_backend() is True
