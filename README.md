# Omnidroids

A personal, **fully local** multi-agent prediction platform. Ingest a document,
build a local knowledge graph, generate agent personas, run a local multi-agent
simulation, and produce a report with interactive Q&A — **entirely on your own
machine. No cloud services**.

> **Status: Phases 0–6 built out — functional end-to-end on macOS (MLX).** Model
> runtime with hard unload, Model Manager, sessions main page with resume, local
> Graphiti/Kuzu graph, local embeddings, launcher + single-process SPA serving,
> light/dark appearance. Remaining items are tracked as GitHub issues (see
> [`HANDOFF.md`](./HANDOFF.md) §6). AGPL-3.0 derivative of `MiroFish`; see
> [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md) for licensing.

## License / Attribution

Omnidroids is a derivative work of **MiroFish** and is therefore distributed under
the **GNU Affero General Public License v3.0** — see [`LICENSE`](./LICENSE).

- **Upstream base:** MiroFish (AGPL-3.0, © MiroFish contributors). Omnidroids is a
  re-homed, modified fork; files copied from MiroFish keep their notices, and new
  files carry `SPDX-License-Identifier: AGPL-3.0-or-later`.
- Graphiti — Apache-2.0 · Kuzu — MIT · MLX / mlx-lm — MIT · llama.cpp — MIT ·
  huggingface_hub — Apache-2.0 · Flask — BSD-3-Clause · Vue — MIT.
- Full table + candidate licenses: [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

If you publish or serve this software, AGPL-3.0 obligations apply (provide the
corresponding source, including over a network).

## Architecture

- **Backend** — Python 3.11 + Flask. One process serves the API *and* the built
  frontend SPA.
- **Graph memory** — **Graphiti** (Apache-2.0) + embedded **Kuzu** store, behind a
  Zep-compatible client seam (`backend/app/utils/graphiti_zep.py`). Cognee and
  Semantica are evaluated as alternatives in a bake-off (`HANDOFF.md` §3.2).
- **Model runtime** — models load on demand and are **hard-unloaded from memory
  when each task finishes** (a binding requirement). Backends: **MLX** (macOS,
  in-process via `mlx-vlm`) and **llama.cpp GGUF** (cross-platform incl. Windows by
  design — but the Windows path is **untested on Windows**; see *Windows (GGUF-only)*
  below).
  One internal OpenAI-compatible `/v1` surface everything points at. (The legacy
  Ollama path was removed in B5 — nothing in the runtime depends on it.)
- **Two models, one 16 GB device** — a small model for parallel work (ingestion,
  personas); a larger model for report synthesis and Q&A (sequential phases,
  RAM-budget guarded, unloaded after each phase).
- **Models** — HuggingFace repo ids are downloaded into `Omnidroids/models/` only
  (`HF_HOME` is redirected into the folder); weights are **never** committed.
- **Sessions** — SQLite-backed project history; interrupted runs resume from
  per-chunk checkpoints. The session list is the main page.

## Intentionally kept from the validated reference

- **Reliability fixes M1/M2/M3** (validated on a 53-chunk build, 5–6 chunks
  skipped, 0 errors): the `max_tokens` leaf-clamp (`_CappedLLMClient`),
  truncation fail-fast (`_TruncatedJSONError`), and per-chunk fault isolation
  (`_ingest_record` skips a bad chunk instead of aborting the whole batch) plus
  `SKIP_THRESHOLD`. **Do not relax these.**
- **`OASIS_DEFAULT_MAX_ROUNDS=6`** unless explicitly changed.
- **Reasoning is OFF by default.** With the local 1.7B model, thinking ON is 4–6×
  slower and hurts per-agent action validity; OFF keeps JSON valid and agent
  actions usable (`LLM_REASONING_EFFORT=none`, live-toggleable in the UI).

## Quick start

```bash
# 1. backend deps  (creates backend/.venv; graphiti/kuzu/psutil are declared deps)
#    the gguf runtime works on any OS; add the mlx extra on Apple Silicon:
cd backend
uv sync
uv sync --extra mlx         # macOS only: in-process MLX inference backend
cd ..

# 2. config  (no secrets; local Graphiti needs no API key)
cp .env.example .env

# 3. frontend build  (dev-time bundle; runtime is served by the backend)
cd frontend && npm install && npm run build && cd ..
```

### Run — `./Omnidroids.sh` (macOS) / `Omnidroids.bat` (Windows)

The launcher is the supported entry point (single process — the backend serves
both the API and the built SPA at http://localhost:5001; no Node/Vite server at
runtime):

1. installs deps only when missing (`uv sync`, `npm install`);
2. builds the SPA once if `frontend/dist` is absent (`npm run build`);
3. exports `HF_HOME=<repo>/.cache/hf` — every HuggingFace artifact stays inside
   the folder (verified on macOS; `Omnidroids.bat:51` does the same on Windows, but
   only by static reading — **untested on Windows**, see *Windows (GGUF-only)* below);
4. starts the backend and opens **only** the main page;
5. stops with **SIGTERM only** (`Ctrl+C`) — never `kill -9` (Kuzu WAL).

```bash
./Omnidroids.sh                    # macOS (double-click works; right-click -> Open if blocked)
OPEN_BROWSER=0 ./Omnidroids.sh     # don't auto-open the browser
```

On Windows (**untested on Windows** — see *Windows (GGUF-only)* below): double-click
`Omnidroids.bat`. Stop by closing the backend window (or the launcher window); never
`taskkill /f` the backend (Kuzu WAL). This instruction has never been run on Windows.

Run the backend directly instead (useful for development):

```bash
cd backend && uv run python run.py     # API + built SPA on http://localhost:5001
```

### Windows (GGUF-only) — untested on Windows hardware

`Omnidroids.bat` and the Windows GGUF runtime have **never been executed on a Windows
box** (there is no such machine here), so this section records the honest ceiling (#27):
what a **static reading** of the committed code guarantees, and what remains **untested
on Windows** and must not be claimed. macOS claims here and elsewhere are measured;
Windows is a static reading only.

**Known-good by static reading (never executed on Windows):**
- **Launcher ordering** — `HF_HOME` is set at `Omnidroids.bat:51`, *before* the backend
  spawns at `:62`; the `cmd /c` child inherits it, and `setlocal` (`:16`) scopes it to
  the launcher (it cannot leak into your shell).
- **Three-layer HF confinement** — `downloader.py:359-377` `_confine_hf_cache()` sets
  `HF_HOME`/`HF_HUB_CACHE`/`HF_XET_CACHE` and rewrites `huggingface_hub.constants`
  (the hub resolves those *at import time*, so the launcher's env var alone can land
  too late in an already-imported worker), and `config.py:200`'s `HF_CACHE_DIR` default
  is already repo-rooted — so even with no env var, nothing is written outside
  `.cache/hf`; no app code writes to `~/.cache`.
- **cwd-independence** — `cd /d "%ROOT%"` (`:21`) after `ROOT=%~dp0` (`:23`).
- **SPA path construction / separator** — every path is built with `os.path.join` /
  `send_from_directory` (`app/__init__.py:145-166`), so the separator is `os.sep`; no
  hard-coded `/`; end-to-end SPA *serving* on Windows is **not** known-good — see below.
- **UTF-8 console** — `run.py:8-9` and `logger.py:13-23`.
- **Port short-circuit** — the `:5001`-already-listening check (`Omnidroids.bat:56`).

**NOT known-good — never executed on Windows:**
- `Omnidroids.bat` end to end; first-run `uv sync` / `npm install`.
- one real GGUF download + load; the `llama-server` Windows process and its SIGTERM
  unload; GGUF quant availability.
- the Kuzu `0.11.3` embedded driver opening a graph on Windows (`pyproject.toml:34`).
- SPA serving on Windows.
- the `taskkill /F` escalation path (`simulation_runner.py:935`).

**Recorded discrepancy (not papered over).** Two different process trees involve
`taskkill`, and they must not be conflated. `Omnidroids.bat:78` (and the Run step above)
say **never `taskkill /f` the backend** — that protects the Flask backend at `:5001`,
whose Kuzu WAL needs a clean close. The `taskkill /F` at `simulation_runner.py:935`
escalates for an **OASIS simulation subprocess tree** (`:919`'s Windows branch, after a
graceful `taskkill /T` timeout); that tree carries no Kuzu WAL and is the Windows twin
of the repo's SIGTERM-only rule. One protects the graph-backed backend; the other tears
down a spawned simulation tree.

**Turn "untested" into "tested."** A Windows owner: double-click `Omnidroids.bat`, or in
a terminal `cd backend && uv run python run.py`. Confirm no artifact is written under
`%USERPROFILE%\.cache` (the Windows twin of the macOS proof in
`scratch/e2e_download_probe.py`), then **paste the launcher output / `logs/backend.log`
and one real GGUF download+load log into issue #27**.

## Model runtime (Phase 1)

Models are swapped behind one OpenAI-compatible `/v1` endpoint and **load on
demand / hard-unload when the task ends** (a binding requirement). Backends:

- **mlx** — macOS Apple Silicon, in-process (`mlx-lm`); unload clears the Metal
  cache so our own RSS falls.
- **gguf** — cross-platform incl. Windows (a designed target, but the Windows path is
  **untested on Windows**); a `llama-server` subprocess; unload is a `SIGTERM` (never
  `kill -9`) so the whole process, and its RAM, is released. Never executed on Windows.

Verify the hard-unload guarantee (HANDOFF §12.3):

```bash
cd backend
MODEL=LFM3-1.7B-MLX-4bit .venv/bin/python3 ../scratch/mem_check.py   # any registered local model
MODEL=Ornith-1.5-9B-MLX-6bit .venv/bin/python3 ../scratch/bigmodel_eval.py
# -> per-phase process RSS + resident-model footprint, asserting release.
```

Registered roles (git-ignored `models/registry.json`, set in the Models UI):
`small` = `LiquidAI/LFM3-1.7B-MLX-4bit`, `big` = `ornith-ai/Ornith-1.5-9B-MLX-6bit`
(A7 decision in GitHub issue #5; embeddings run locally via sentence-transformers —
`Snowflake/snowflake-arctic-embed-s` 384-d, see the bake-off in `scratch/embedder_bakeoff.py`).

## Model Manager (later phase)

Paste a HuggingFace repo id → it downloads into `Omnidroids/models/` only → assign
roles (small/big). Models load for a phase and are **hard-unloaded when it ends**;
RSS before/after is logged as proof. Example repos: `LiquidAI/LFM3-1.7B-MLX-8bit`,
`LiquidAI/LFM3-1.7B-MLX-4bit`, `ornith-ai/Ornith-1.5-9B-MLX-8bit`.

## End-to-end acceptance proof (2026-11-18/19) — measured, fully local

The full pipeline — document → knowledge graph → agent personas → dual-world
multi-agent simulation → report → interactive Q&A — **has run end to end on this
repo's own Apple Silicon machine with no cloud service of any kind**: 53/53 chunks
extracted (0 skipped, 624 nodes / 1105 edges), 163 personas, a 72-round
twitter+reddit simulation reaching 100 % with **497 LLM-written agent actions**,
a three-section graph-grounded report, and five Q&A answers grounded in that
run's own entities and numbers (plus a working per-agent `interview` on the
completed run).

**Time taken (measured, one acceptance run; the graph build is stochastic, so the
same document has measured 2 h 22 m on its best day and 10 h 23 m here):**

| Phase | Measured |
|---|---|
| 1 ontology | 2.3 min |
| 2 graph build (53 chunks, `small`) | 2 h 22 m – 10 h 23 m across runs (per-run variance; ~2.7–13.5 min/chunk) |
| 3 environment setup (163 personas, `small`) | ~60 min |
| 4 simulation (72 rounds × 2 platforms, `small`) | ~9 h 49 m (2.2–13.5 min/round) |
| 5 report (3 sections, `big`) | ~69 min (after 3 failed attempts — see below) |
| 5 chat (5 questions, `big`) | 66–103 s per answer |
| **total, this run** | **~22–25 h** |

**This is a proof of concept that a fully local multi-agent prediction pipeline is
possible — not a fast one.** Every phase is bounded by this machine's hardware
specification, not by the architecture: one 16 GB unified-memory budget shared by
two models that serve on a single MLX worker thread (so phases serialize and the
report phase's 9B-model peak co-residency with its own graph tools hit the memory
ceiling and needed retries before completing), a stochastic per-chunk local
extraction whose pace varies ~5× run to run, and a `small` local model whose agent
actions land but decode slowly. Larger unified memory, more worker threads, or a
faster local model moves every number above; nothing in the design assumes cloud
capacity. The full measured record — every platform reason verbatim, the five
answers, the evidence bundle — is in `HANDOFF.md` §9 L.10 and
`docs/sessions/whu_dualworld_e2e.md` (Attempt 5).

## Hard constraints

- **Never `kill -9` the backend** (Kuzu needs a clean close or the next open can
  `SIGBUS`) — use `SIGTERM`.
- Models are never installed to global home paths and never pushed to GitHub.
- The `Prediction/MiroFish` tree is the validated reference and must stay runnable;
  this repo is a copy that is then modified.
