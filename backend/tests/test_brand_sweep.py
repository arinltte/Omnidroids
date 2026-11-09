# SPDX-License-Identifier: AGPL-3.0-or-later
"""#28 guard: one brand in the user-visible surface.

Issue #28 sweeps the in-app brand: the seven view headers that still said
``MIROFISH`` now say ``OMNIDROIDS`` like ``SessionsView`` / ``ModelsView`` /
``Home``, and the title-cased locale copy / SPA shell / logo alt text follow.
Attribution is NOT part of the sweep — ``README.md``,
``THIRD_PARTY_NOTICES.md`` and ``LICENSE`` keep the upstream name, and neither
is the load-bearing backend wire format (Zep ``source_description`` payloads,
``mirofish_operation_id`` op-ids, graph-id prefixes, logger names), whose
allowlist is spelled out below.

The scan is pure filesystem: no app import, no network, no browser. It mirrors
``test_no_ollama_references.py`` — the same precedent of a line-numbered
offender list for a repo-consistency boundary.

Two residual classes are deliberately permitted rather than swept, and the
permission is checked (see ``LOGO_FILENAME`` and ``BACKEND_WIRE_FORMAT_PINS``)
so the guard passes for a stated reason, not by luck.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SPA_SHELL = REPO_ROOT / "frontend" / "index.html"
VUE_GLOB = "**/*.vue"
LOCALES_GLOB = "*.json"

# The brand this repo now presents. In-app headers are all-caps (template text,
# identical in both locales); title / meta / prose is title-cased.
BRAND = "Omnidroids"

# The old brand, any casing, as it can reach a user: header template text, a
# locale value, a <title>/<meta> string, an ``alt`` attribute, an asset path.
OLD_BRAND = re.compile(r"mirofish", re.IGNORECASE)

# Residual class 1 — the un-renamed logo FILENAME
# (``frontend/src/assets/logo/MiroFish_logo_left.jpeg``, and the hashed copy
# ``npm run build`` emits under ``frontend/dist/assets/``). A filename is not
# user-visible chrome, so #28 keeps it and only changes the logo's ``alt``
# text; the token is allowed wherever it appears as that asset's path stem.
LOGO_FILENAME = re.compile(r"mirofish_logo_", re.IGNORECASE)

# Residual class 2 — backend wire formats: values a remote API or a test pins,
# NOT user-visible chrome. Sweeping them would change a Zep/Graphiti contract
# (an episode ``source_description``, an op-id key, a graph-id prefix, a logger
# name captured by ``caplog``). Recorded here so the allowlist is explicit, and
# each pin is asserted still present below — an accidental sweep of one of them
# fails this guard instead of silently breaking a contract.
BACKEND_WIRE_FORMAT_PINS = [
    # graph-id prefix + the op-id key the deep-Zep contracts read back
    ("app/services/graph_builder.py", "mirofish_operation_id"),
    # episode payloads written to Zep (source_description / metadata source)
    ("app/services/graph_builder.py", "MiroFish source document chunk"),
    ("app/services/zep_graph_memory_updater.py", "MiroFish simulation activity batch"),
    ("app/services/zep_graph_memory_updater.py", "mirofish_simulation"),
    ("app/utils/graphiti_zep.py", "mirofish.graphiti_zep"),
    # the tests that pin those wire formats
    ("tests/test_zep_graph_memory_updater.py", "MiroFish simulation activity batch"),
    ("tests/test_zep_cloud_contracts.py", "mirofish_operation_id"),
    ("tests/test_zep_cloud_contracts.py", "MiroFish source document chunk"),
    ("tests/test_graphiti_extraction_schema.py", "mirofish.graphiti_zep"),
]

# Header brand divs. Every view carries one; they are locale-independent
# template text, so they are checked directly rather than through ``$t``.
HEADER_BRAND = re.compile(r'class="(?:nav-)?brand"[^>]*>([^<]*)<')


def _scanned_files():
    """Every user-visible source the sweep must keep on one brand."""

    files = sorted((REPO_ROOT / "frontend" / "src").rglob(VUE_GLOB))
    files.append(SPA_SHELL)
    files += sorted((REPO_ROOT / "locales").glob(LOCALES_GLOB))
    return files


def _line_no(text, index):
    return text.count("\n", 0, index) + 1


def test_no_old_brand_in_the_user_visible_surface():
    offenders = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8")
        for match in OLD_BRAND.finditer(text):
            if LOGO_FILENAME.match(text, match.start()):
                continue  # the deliberately un-renamed logo filename
            line = text.splitlines()[_line_no(text, match.start()) - 1].strip()
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{_line_no(text, match.start())}: {line}")
    assert not offenders, (
        f"old brand text still user-visible ({BRAND} is the brand):\n" + "\n".join(offenders)
    )


def test_every_view_header_says_the_current_brand():
    """Both directions: the seven swept headers, and the three already-branded.

    A regression in either direction — a header drifting back to the old brand,
    or a newly added view carrying a different spelling — fails here."""

    offenders = []
    for path in sorted((REPO_ROOT / "frontend" / "src" / "views").glob("*.vue")):
        text = path.read_text(encoding="utf-8")
        for match in HEADER_BRAND.finditer(text):
            if match.group(1).strip() != BRAND.upper():
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{_line_no(text, match.start())}: "
                    f"header says {match.group(1).strip()!r}, expected {BRAND.upper()!r}"
                )
    assert not offenders, "view headers are not on one brand:\n" + "\n".join(offenders)


def test_health_self_reported_name_uses_the_current_brand():
    """``/health`` reports the app's own name — user-visible, so it is swept too."""

    source = BACKEND_DIR / "app" / "__init__.py"
    text = source.read_text(encoding="utf-8")
    match = re.search(r"'service':\s*'([^']+)'", text)
    assert match, f"{source.relative_to(REPO_ROOT)}: /health no longer self-reports a service name"
    assert "mirofish" not in match.group(1).lower(), (
        f"{source.relative_to(REPO_ROOT)}: /health still reports the old brand "
        f"{match.group(1)!r}"
    )
    assert match.group(1) == f"{BRAND} Backend", (
        f"{source.relative_to(REPO_ROOT)}: /health reports {match.group(1)!r}, "
        f"expected {BRAND + ' Backend'!r}"
    )


def test_backend_wire_format_pins_stay_out_of_the_brand_sweep():
    """The allowlist above is load-bearing: each pin must still be there.

    These are the strings #28 explicitly does NOT sweep. If one of them has been
    renamed, the sweep reached a wire format and a Zep/Graphiti contract changed
    with it — fail here, with the reason, rather than let it pass unnoticed."""

    missing = []
    for relative, token in BACKEND_WIRE_FORMAT_PINS:
        path = BACKEND_DIR / relative
        if not path.is_file() or token not in path.read_text(encoding="utf-8"):
            missing.append(f"{path.relative_to(REPO_ROOT)}: wire-format pin {token!r} is gone")
    assert not missing, (
        "backend wire formats were swept (they are load-bearing, not chrome):\n"
        + "\n".join(missing)
    )
