# Model Evaluation — Omnidroids local runtime

> **Date:** 2026-11-07 (post-Batch-B, Phase 6). **Method note:** every number below
> is copied from a run log in `scratch/` (git-ignored by design) or a GitHub issue
> comment; the harness that produced each run is named so anyone can re-measure.
> No figure below is estimated or carried over from another machine. Owner-rubric
> items are marked explicitly rather than scored by the agent.

Environment for all runs: macOS Apple Silicon, 16 GB, `GRAPH_BACKEND=graphiti`
(local Graphiti + Kuzu + local models, no cloud), MLX in-process via
`mlx-vlm` 0.7.12 installed **only** in `backend/.venv`. Budget rule from
`HANDOFF.md` §5.2: RAM budget 11 GB, ≥2 GB headroom ⇒ ≤~9 GB of weights+KV per
loaded model. Models never left `Omnidroids/models/` (`HF_HOME` confined to
`Omnidroids/.cache/hf`; verified by `scratch/e2e_download_probe.py` that nothing
lands in `~/.cache`).

## 1. Roles in production (as registered)

| Slot | Model | Repo | Size on disk | Backend |
|---|---|---|---|---|
| `small` | **LFM3-1.7B-MLX-4bit** | `LiquidAI/LFM3-1.7B-MLX-4bit` | 0.99 GB | mlx |
| `small` (fallback) | LFM3-1.7B-MLX-8bit | `LiquidAI/LFM3-1.7B-MLX-8bit` | 1.86 GB | mlx |
| `big` | **Ornith-1.5-9B-MLX-6bit** | `ornith-ai/Ornith-1.5-9B-MLX-6bit` | 7.30 GB | mlx |
| `big` (fallback) | Ornith-1.5-9B-MLX-4bit | `ornith-ai/Ornith-1.5-9B-MLX-4bit` | 5.06 GB | mlx |
| `embed` | **snowflake-arctic-embed-s** | `Snowflake/snowflake-arctic-embed-s` | 33M params (8 MB RSS Δ) | sentence-transformers (in-process) |

## 2. Small model — LFM3-1.7B-MLX 4-bit vs 8-bit (`scratch/small_bakeoff.py`)

Run 2026-11-07, log `scratch/small_bakeoff_measured.log`. Each model inside its
own `ModelManager.phase` (the same load/generate/unload path the pipeline uses),
JSON mode through the `/v1` proxy (prompt-level strict JSON + repair parser).

| model | peak RSS | JSON valid | JSON s | JSON tok | **JSON tok/s** | text s | **text tok/s** |
|---|---|---|---|---|---|---|---|
| LFM3-1.7B-MLX-8bit | 2425 MB | True | 5.23 | 257 | 49.1 | 0.96 | 47.0 |
| **LFM3-1.7B-MLX-4bit** | **1731 MB** | True | **2.56** | 220 | **86.0** | **0.54** | **79.5** |

Quality on the fixture prompts (raw outputs in the log, for owner judgement):
both quants returned the same entity set (Apple / iPhone 16 / Cupertino /
September 9 2024 / …) and the same three factual claims — the 4-bit's text output
was byte-identical apart from trailing whitespace.

**Decision (spec §6 rule — "if 8-bit ≈ 4-bit in quality, take the 4-bit
terminal-throughput winner"):** 4-bit is `small`. It is **1.75× the JSON
throughput** (86.0 vs 49.1 tok/s) with **694 MB less peak RSS**, at equal
fixture quality and 100% JSON validity in both. The 8-bit stays registered as
the documented fallback.

## 3. Big model — Ornith-1.5-9B-MLX-6bit acceptance (`scratch/bigmodel_eval.py`)

Five checks through the same phase path + `/v1` proxy. Log
`scratch/bigmodel_eval_6bit_stated_band.log`.

| check | result | wall |
|---|---|---|
| extract (structured facts from a market note) | PASS — 333 chars | 6.8 s |
| report (synthesis from a context bundle) | PASS — 275 words (band 250–350) | 24.0 s |
| json (strict-JSON via `/v1`) | PASS — valid JSON | 3.2 s |
| recall (needle in ~8k-token context) | PASS | 25.6 s |
| grounded (must not fabricate outside the bundle) | PASS | 2.9 s |
| **suite** | **5/5 PASS** | — |

Against the 4-bit fallback (`scratch/bigmodel_eval_4bit.log`, also 5/5):

| check | 6-bit (chosen) | 4-bit (fallback) |
|---|---|---|
| extract | 333 chars / 6.8 s | 344 chars / 4.9 s |
| report | 275 words / 24.0 s | 218 words / 15.9 s |
| json | valid / 3.2 s | valid / 2.5 s |
| recall | 25.6 s | 22.7 s |
| grounded | 2.9 s | 2.1 s |

**Why 6-bit stays `big` despite the 4-bit being faster:** verbosity is
architecture-level, not quant-level. The report check asks for a 250–350-word
band; the 4-bit wrote 218 words — *under the band* — while the 6-bit wrote 275,
in-band. The earlier "6-bit report miss" was prompt calibration (a soft
"~300-word" ask), not capability: with the band stated, the 6-bit is 5/5 and the
4-bit would not have fixed the under-length failure either. Both quants were
evaluated head-to-head before the choice (A7 evidence in issue #5).

## 4. Memory guard — hard unload (`scratch/mem_check.py`, spec §12.3)

Every phase (including a forced-exception phase) returns resident model memory to
**0 MB** with nothing left loaded and zero live MLX array bytes.

| model | baseline RSS | final RSS | per-phase floor drift | verdict |
|---|---|---|---|---|
| Ornith-1.5-9B-MLX-6bit | 59 MB | 725 MB | `[+451, +1015, −835] MB` | PASS |
| Ornith-1.5-9B-MLX-4bit | 59 MB | 822 MB | `[+932, +466, +738] MB` | PASS |

**Documented limitation (upstream, watched — not a leak):** mlx-vlm's
in-process load leaves a fixed ~113 MB RSS floor drift per load that our unload
sequence cannot reclaim; proven independent of model size, processor caching and
`mx.eval` (`scratch/mlx_unload_probe{,2,3,4}.py`; issue #8). MLX arrays are
released fully (live arrays → 0), so the two-model swap choreography — not the
floor — is what fits 16 GB. The drift sawtooths with OS reclamation and is
tracked by the bake-off soak; escalate to a `set_wired_limit` experiment if it
threatens the 11 GB budget in a long session.

**Co-residency** (`scratch/coresidency_probe.py`, log `scratch/coresidency_6bit.log`):
small + big loaded together peaked at **3424 MB process RSS** against the
11264 MB budget ⇒ **7840 MB headroom** (measured 3.4 GB resident, well inside
the ≥2 GB rule; the manager's own estimate is conservative at 8004 MB for the
6-bit).

## 5. Embedder bake-off (`scratch/embedder_bakeoff.py`, issue #14)

Same triple-accuracy harness (6 EN+ZH triples), local sentence-transformers,
in-process:

| model | dim | params | load | single | docs/s | triples | RSS Δ |
|---|---|---|---|---|---|---|---|
| MiniLM-L6-v2 (previous default) | 384 | 22.7M | 3.8 s | 37.8 ms | 73.6 | 5/6 | 31 MB |
| **arctic-embed-s (chosen)** | 384 | 33.2M | 4.6 s | **12.7 ms** | **402.1** | **6/6** | **8 MB** |
| granite-97m-multilingual-r2 | 384 | 97.4M | 5.4 s | 40.4 ms | 56.0 | 5/6 | 258 MB |

**arctic wins on accuracy and speed with the smallest footprint**; its
recommended asymmetric query prompt is applied to every encode (6/6
prompt-always, same as query-only, keeps single-fact and query vectors in one
space). Granite is parked: 3× the params, half the throughput, no measured gain
on these triples — revisit only for a multilingual-heavy workload as a separate
`embed`-role registration. **768→384-d invalidates any pre-existing 768-d
graph embeddings** (none exist on this machine; documented in the B5 commit).

## 6. Local end-to-end smoke (`scratch/prove_local_graphiti.py`)

Real backend on a free port, ingest+search through our `/v1` proxy with the
registered small model, local embeddings in-process, hard-fail if anything
answers on :11434 (the Ollama port). Final run
(`scratch/prove_local_graphiti_final.log`): **3/3 episodes ingested, recall
edges=5, `alice_bob_recall=True`**; chunk times 9.6 s / 133.3 s / 10.5 s (the
second episode is the cold-start LLM call), search 0.0 s. Clean SIGTERM exit.

## 7. Speed plan — expected vs measured (spec §7)

The §7 estimates (ingestion ~90→25–35 min, personas ~25→6–10 min on the
53-chunk reference workload) were projections made before the runtime work. What
is measured so far: the small model's 86 tok/s JSON throughput, the per-episode
ingest times above, and the co-residency headroom. A full 53-chunk wall-time
re-measurement on the Omnidroids pipeline has **not** been run yet — it is the
next measurement and belongs in this doc (§12 acceptance criterion 3 records it).

## 8. Reproduce

```bash
cd Omnidroids/backend
.venv/bin/python ../scratch/small_bakeoff.py                  # §2 (both quants)
MODEL=Ornith-1.5-9B-MLX-6bit .venv/bin/python ../scratch/bigmodel_eval.py
MODEL=Ornith-1.5-9B-MLX-6bit .venv/bin/python ../scratch/mem_check.py
.venv/bin/python ../scratch/embedder_bakeoff.py               # §5
.venv/bin/python ../scratch/coresidency_probe.py              # §4
.venv/bin/python ../scratch/prove_local_graphiti.py           # §6 (stop Ollama first)
```

Model choice is owner-rubric judgement on the report outputs (faithfulness /
specificity / structure); the raw texts are in the logs above for that review.
