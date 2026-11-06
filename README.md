# Omnidroids

A personal, **fully local** multi-agent prediction platform. Ingest a document,
build a local knowledge graph, generate agent personas, run a local multi-agent
simulation, and produce a report with interactive Q&A — **entirely on your own
machine. No cloud services**.

> **Status: Phase 0 — skeleton.** Built from the validated `MiroFish` reference.
> See [`HANDOFF.md`](./HANDOFF.md) for the full build spec and phased plan, and
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
  in-process via `mlx-vlm`) and **llama.cpp GGUF** (cross-platform incl. Windows).
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
   the folder;
4. starts the backend and opens **only** the main page;
5. stops with **SIGTERM only** (`Ctrl+C`) — never `kill -9` (Kuzu WAL).

```bash
./Omnidroids.sh                    # macOS (double-click works; right-click -> Open if blocked)
OPEN_BROWSER=0 ./Omnidroids.sh     # don't auto-open the browser
```

On Windows: double-click `Omnidroids.bat`. Stop by closing the backend window
(or the launcher window); never `taskkill /f` the backend.

Run the backend directly instead (useful for development):

```bash
cd backend && uv run python run.py     # API + built SPA on http://localhost:5001
```

## Model runtime (Phase 1)

Models are swapped behind one OpenAI-compatible `/v1` endpoint and **load on
demand / hard-unload when the task ends** (a binding requirement). Backends:

- **mlx** — macOS Apple Silicon, in-process (`mlx-lm`); unload clears the Metal
  cache so our own RSS falls.
- **gguf** — cross-platform incl. Windows; a `llama-server` subprocess; unload is
  a `SIGTERM` (never `kill -9`) so the whole process, and its RAM, is released.

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

## Hard constraints

- **Never `kill -9` the backend** (Kuzu needs a clean close or the next open can
  `SIGBUS`) — use `SIGTERM`.
- Models are never installed to global home paths and never pushed to GitHub.
- The `Prediction/MiroFish` tree is the validated reference and must stay runnable;
  this repo is a copy that is then modified.
