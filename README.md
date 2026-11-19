# Omnidroids

A personal, **fully local** multi-agent prediction platform. Ingest a document,
build a local knowledge graph, generate agent personas, run a local multi-agent
simulation, and produce a report with interactive Q&A — **entirely on your own
machine. No cloud services, no API keys.**

> **Based on MiroFish: Omnidroids is a derivative work of MiroFish** (AGPL-3.0,
> © MiroFish contributors) — a re-homed, modified fork that carries the upstream
> reliability work forward. See *Credit & license* below.

## What it does

Five steps, one pipeline, all local:

1. **Document + ontology** — upload a document (PDF/text); a small local model
   extracts an entity/relation ontology for your domain.
2. **Knowledge graph** — the document is chunked and ingested into a local
   **Graphiti + Kuzu** graph with local embeddings.
3. **Agent personas** — the graph's entities become simulated agents with
   personas, driven by a generated simulation config.
4. **Multi-agent simulation** — the agents act, post, and react across
   simulated Twitter and Reddit worlds; the graph remembers the run.
5. **Report + Q&A** — a large local model writes a graph-grounded report, and
   answers questions about the simulation — citing the run's own entities,
   numbers and events, not generic prose.

You can drive all of it from the browser UI, or from the built-in interactive
console (`omni`), which also answers questions about a finished run and
interviews individual agents.

## Run it — one click

```bash
./Omnidroids.sh          # macOS: double-click works (right-click -> Open if blocked)
```

On Windows: double-click `Omnidroids.bat` — **untested on Windows** (see
*Requirements*).

The launcher does everything for you — **you just wait**:

1. installs backend/frontend dependencies if missing (`uv sync`, `npm install`);
2. builds the web UI if missing;
3. creates `.env` from `.env.example` if missing;
4. **checks the local models** — if the two role models (a small one for the
   graph/persona phases, a large one for the report/Q&A phases) or the local
   embedding model are missing, it downloads them from HuggingFace into the
   project folder and wires them up — a one-time wait on first run
   (~8.5 GB total);
5. starts the platform at <http://localhost:5001> (the backend serves both the
   API and the UI — no other process to run).

Stop it with `Ctrl+C` (or close the window) — **SIGTERM only**, never
`kill -9` (the embedded graph store needs a clean close).

## Requirements

- **macOS on Apple Silicon** for the default path (models run in-process via
  MLX). ~16 GB unified memory recommended; ~15 GB disk for the models.
- **Windows** (`Omnidroids.bat`, GGUF runtime) is designed for but **untested on
  Windows** — it has never been executed on a Windows machine here, so treat it
  as unverified.
- No GPU server, no Ollama, no cloud endpoint. Everything — inference,
  embeddings, graph store — runs in the one backend process.

## What a full run costs (measured on a 16 GB Mac)

**This is a proof of concept that a fully local multi-agent prediction pipeline
works — not a fast one.** Every phase is bounded by consumer hardware: one
memory budget shared by two models, a single model worker thread (phases
serialize), and stochastic per-chunk local extraction. Measured end to end on
the bundled acceptance document (53 chunks, 72 simulated hours across two
platforms):

| Phase | Measured |
|---|---|
| ontology | ~2 min |
| knowledge graph (53 chunks) | 2 h 20 min – 10 h 20 min (varies per run) |
| agent personas | ~1 h |
| simulation (72 rounds × 2 platforms) | ~10 h, ~500 LLM-written agent actions |
| report (3 sections) | ~1 h |
| Q&A (per question) | ~1–2 min |

**A complete run is a ~22–25 hour batch job** on this class of machine — plan
it as an overnight run. Faster hardware (more unified memory, more worker
threads, faster local models) moves every number; nothing in the design assumes
cloud capacity. The first run additionally waits for the one-time model
downloads.

## Credit & license

Omnidroids is a derivative work of **MiroFish** and is distributed under the
**GNU Affero General Public License v3.0** — see [`LICENSE`](./LICENSE):

- **Upstream base:** MiroFish (AGPL-3.0, © MiroFish contributors). Files derived
  from MiroFish keep their notices; new files carry
  `SPDX-License-Identifier: AGPL-3.0-or-later`.
- Third-party components: Graphiti (Apache-2.0) · Kuzu (MIT) · MLX / mlx-lm
  (MIT) · llama.cpp (MIT) · huggingface_hub (Apache-2.0) · Flask (BSD-3-Clause) ·
  Vue (MIT) — full table in
  [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

If you publish or serve this software, AGPL-3.0 obligations apply (provide the
corresponding source, including over a network).

Model weights are downloaded at runtime into `models/` and are **never**
committed to this repository.