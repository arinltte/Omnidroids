# SPDX-License-Identifier: AGPL-3.0-or-later
"""#27 guard: the Windows "untested on Windows" note and its ``file:line`` anchors.

The Windows GGUF path (``Omnidroids.bat``) has never been executed on a Windows
box, so ticket #27 records it as documented-"untested" rather than "tested". That
note lives in ``README.md`` and is the honest ceiling on what we claim about
Windows (the internal handoff that once carried a second copy is not published). This scan keeps it from rotting back into a "verified" claim:

1. **Marker** — both docs carry an explicit untested-on-Windows marker. If either
   note is deleted, or reworded to read as if the path were verified, this fails.
2. **Anchors** — every capability/limit the note cites by ``file:line`` still
   resolves: the cited construct is present in the named source file, within a
   small tolerance of the cited line, so a moved or deleted construct (or a line
   number the note has silently outgrown) is caught.

This mirrors ``test_no_ollama_references.py`` — a scan over the committed prose and
source, never a mock. macOS claims are deliberately out of scope here: macOS is
measured, and this guard exists only to keep the Windows caveat honest.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"

# The explicit phrase #27 requires both docs to carry. Case-insensitive; the note's
# heading ("untested on Windows hardware") and its body both use it.
UNTESTED_MARKER = re.compile(r"untested on Windows", re.IGNORECASE)

# A construct found within this many lines of the cited number still "resolves":
# tolerates the nearby edits a doc citation survives, catches a construct that moved
# far, or a line number the note has outgrown.
LINE_TOLERANCE = 12

# (path relative to REPO_ROOT, regex identifying the cited construct, cited 1-based
# line, what the note claims it backs). One entry per documented file:line claim.
ANCHORS = [
    ("Omnidroids.bat", r"setlocal enabledelayedexpansion", 16,
     "launcher's setlocal scopes the confined HF_HOME"),
    ("Omnidroids.bat", r'cd /d "%ROOT%"', 21,
     "cwd-independent launcher"),
    ("Omnidroids.bat", r'set "HF_HOME=%ROOT%', 51,
     "HF_HOME confined inside the folder, set before the backend spawn"),
    ("Omnidroids.bat", r'netstat -an \| findstr /r /c:":5001 \.\*LISTENING"', 56,
     "the :5001 port-in-use short-circuit"),
    ("Omnidroids.bat", r'cmd /c "cd /d %ROOT%', 62,
     "the backend child that inherits the launcher's HF_HOME"),
    ("Omnidroids.bat", r"never taskkill /f", 78,
     "the launcher banner: never taskkill /f the backend"),
    ("backend/app/models/downloader.py", r"def _confine_hf_cache", 359,
     "in-process HF confinement at download time"),
    ("backend/app/models/downloader.py",
     r"import huggingface_hub\.constants as hf_constants", 371,
     "rewrites hub constants that resolve at import time"),
    ("backend/app/config.py", r"HF_CACHE_DIR = os\.environ\.get\('HF_CACHE_DIR'", 224,
     "repo-rooted HF cache default that needs no env var"),
    ("backend/app/__init__.py",
     r"index_path = os\.path\.join\(dist_dir, 'index\.html'\)", 148,
     "SPA path built with os.path.join"),
    ("backend/app/__init__.py",
     r"send_from_directory\(dist_dir, 'index\.html'\)", 156,
     "SPA served via send_from_directory"),
    ("backend/run.py", r"if sys\.platform == 'win32':", 9,
     "UTF-8 console setup"),
    ("backend/app/utils/logger.py", r"def _ensure_utf8_stdout", 13,
     "UTF-8 console setup helper"),
    ("backend/app/services/simulation_runner.py", r"\['taskkill', '/F', '/PID'", 1036,
     "the taskkill /F escalation for the OASIS simulation subprocess tree"),
    ("backend/pyproject.toml", r"kuzu==0\.11\.3", 34,
     "the Kuzu version never exercised on a Windows process"),
    ("backend/app/models/runtime.py", r"cross-platform \(incl\. Windows\)", 13,
     "the GGUF runtime's cross-platform claim (never run on Windows)"),
]


def test_readme_carries_untested_on_windows_marker():
    assert UNTESTED_MARKER.search(README.read_text(encoding="utf-8")), (
        "README.md no longer states that the Windows GGUF path is untested (#27) — "
        "the honest ceiling on what we claim about Windows has been dropped"
    )


def test_documented_windows_anchors_resolve():
    offenders = []
    for relpath, pattern, cited, claim in ANCHORS:
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        hits = [text.count("\n", 0, m.start()) + 1
                for m in re.finditer(pattern, text)]
        if not hits:
            offenders.append(
                f"{relpath}: {claim!r} — construct gone (the note cited it at :{cited})"
            )
            continue
        nearest = min(hits, key=lambda n: abs(n - cited))
        if abs(nearest - cited) > LINE_TOLERANCE:
            offenders.append(
                f"{relpath}:{nearest}: {claim!r} drifted from the cited :{cited} "
                f"(outside ±{LINE_TOLERANCE}) — update the #27 note's file:line"
            )
    assert not offenders, "Windows-note file:line anchors no longer resolve:\n" + "\n".join(
        offenders
    )
