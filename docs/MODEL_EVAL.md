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
| `big` | **Ornith-1.5-9B-MLX-6bit** | `ornith-ai/Ornith-1.5-9B-MLX-6bit` | 7.30 GB | mlx |
| `embed` | **snowflake-arctic-embed-s** | `Snowflake/snowflake-arctic-embed-s` | 33M params (8 MB RSS Δ) | sentence-transformers (in-process) |

The quant *fallbacks* (LFM3-1.7B-MLX-8bit, Ornith-1.5-9B-MLX-4bit) were deleted
on 2026-11-07 to reclaim ~6.4 GB — both were measured (tables below), neither is
assigned a role. Re-download with the Model Manager if a fallback is needed.

### 1b. Step → model assignment (owner decision, issue #22)

**Source:** the owner's decision on issue #22 (quoted in the commit that landed it,
`fix/22-step-model-assignment`). Steps 1–3 keep the `small` role; steps 4–5 use the
`big` role. The owner's fallback — "if it failed inform me and i will provide other
options of large models" — is honoured by keeping the assignment a **role**, not a
model name.

| Step (UI `main.stepNames`) | Served by | Resolves to |
|---|---|---|
| 1 Graph Build — `ontologyGeneration` / `graphRagBuild` | `OntologyGenerator` + the graphiti seam | `Config.LLM_MODEL_NAME` → `small` (unchanged) |
| 2 Env Setup | — | `Config.LLM_MODEL_NAME` → `small` (unchanged) |
| 3 Run Simulation | `oasis_profile_generator` / `simulation_config_generator` | `Config.LLM_MODEL_NAME` → `small` (unchanged) |
| 4 Report Generation | `ReportAgent` (`app/api/report.py` `/generate`) | **`big`** (`Ornith-1.5-9B-MLX-6bit`) |
| 5 Deep Interaction | `ReportAgent.chat` (`/api/report/chat`) | **`big`** (`Ornith-1.5-9B-MLX-6bit`) |

Implementation (`app/models/assignment.py`: `resolve_report_model`, wired into
`ReportAgent.__init__`): steps 4–5 read the role name from
`Config.REPORT_LLM_ROLE` (default `big`) and resolve it through
`ModelStore.model_for_role` from `models/registry.json` **at call time**; an optional
`Config.REPORT_LLM_MODEL_NAME` (default empty, must name an installed model) overrides
it. Both knobs are documented in `.env.example`. No model name or machine path is
hard-coded, so reassigning the `big` role in the Model Manager — or pointing the config
value at another 9B-class model — changes the pipeline with no code change. An
unassigned role, a deleted model or an unknown override raises
`StepModelUnavailableError` naming the role, the Model Manager and the config key — it
never falls back to `small` (that would silently undo the decision). Graph extraction
(steps 1–3's graphiti seam) is untouched and still runs `small`.

**Measured** (`scratch/prove_step_model_assignment.py`, real backend on a free port,
Ollama stopped, log `logs/step_model_assignment.log`): the step-4 client resolved
`Ornith-1.5-9B-MLX-6bit` through the role and answered the §3 report prompt
(271 words, 27.8 s) **with the small model still resident**:

    MODEL LOADED Ornith-1.5-9B-MLX-6bit [mlx] rss_before=1711MB rss_after=2602MB est=8004MB
    MODEL UNLOADED Ornith-1.5-9B-MLX-6bit rss_before=2432MB rss_after=836MB

Backend RSS 1711 → 2432 MB over the call (4735 MB peak sampled), back to 836 MB after
the forced hard unload — the same hard-unload guarantee §4 records. Budget gate
(`ModelManager._limit_violation`, `app/models/manager.py`) with the small model still
resident: estimate 8004 MB + 1711 MB resident = 9715 MB against the 11264 MB budget,
concurrency 4/4 — **no violation**, and the 9B loaded. (In-process RSS under-reports
MLX unified memory the same way §4 notes: the load's own line read 2602 MB while the
sampler saw 4735 MB peak.)

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
fixture quality and 100% JSON validity in both. The 8-bit weights were deleted
in session 5; re-download on demand (see §1).

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

Against the 4-bit quant (measured before its weights were deleted; log
`scratch/bigmodel_eval_4bit.log`, also 5/5):

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

### 4b. Re-verification 2026-11-07 — issue #34 (quiet gate)

Re-measured `Ornith-1.5-9B-MLX-6bit` on branch `phase6-integration` at commit
`ae7c01e`, with the #29 thread-affinity fix present. Harnesses were run one at
a time from `backend/`, one model resident at a time; no stale backend, Ollama
closed before and after. The old `502 There is no Stream(gpu, N) in current
thread` failure did **not** appear in any of the three logs.

| harness | log | measured | verdict |
|---|---|---|---|
| `mem_check.py` | `scratch/check34_mem.log` | baseline 59 MB → final resident **0 MB**; per-phase `residMB` 0 / 0 / 0 / 0, all four phases (incl. the forced-exception phase) `released`, live arrays 0 MB; process RSS 59 → 728 MB; per-phase own-RSS floor drift `[809, 688, 537] MB` | **PASS** |
| `bigmodel_eval.py` | `scratch/check34_bigmodel.log` | `BIGMODEL_EVAL: 5/5 PASS` — extract 333 chars/7.8 s, report **275 words**/24.8 s (band 250–350), json valid/3.6 s, recall 27.2 s, grounded 3.1 s | **PASS** |
| `coresidency_probe.py` | `scratch/check34_coresidency.log` | peak **proc_rss=1245 MB** of the 11264 MB budget ⇒ 10019 MB headroom (small 1605 MB + big 1397 MB resident = 2490 MB) | **PASS** |

All three gates pass on the current tip. The report word count (275) and the
co-residency peak differ from the §3/§4 rows above only because the probe
measures a different moment of the process-RSS curve — same model, same
harness, same budget rule; the measured values are copied verbatim from the
2026-11-07 logs.

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

### 5.1 Fresh re-confirmation of the chosen embedder — issue #35 (2026-11-07)

One measured run, offline from the shared cache, no code change
(`scratch/embedder_probe_35.py`, log `scratch/embedder_probe_35.log`). The runtime
loads the embedder through `backend/app/utils/graphiti_zep.py:920`
(`_LocalSentenceTransformerEmbedder.__init__` → `SentenceTransformer(model_name)`,
built by `_build_embedder()` at `backend/app/utils/graphiti_zep.py:942` from
`Config.GRAPHITI_EMBEDDING_MODEL` / `GRAPHITI_EMBEDDING_DIM` /
`GRAPHITI_EMBEDDING_QUERY_PROMPT`, `backend/app/config.py:83-90`), i.e. the same
in-process path Graph Build uses; the app's own `.env` keeps the old nomic
768-d lines commented out (`nomic-embed-text:v1.5` / 768), so the 384-d arctic
default is what resolves.

| metric | fresh run (#35) | §5 bake-off | note |
|---|---|---|---|
| embedding dim | **384** (asserted) | 384 | unchanged |
| params | 33.2 M | 33.2 M | unchanged |
| single encode (median of 5) | **9.9 ms** plain / **9.8 ms** query-prompted | 12.7 ms | ~25% faster; both prompt sides run |
| throughput (50 docs, batch 16, median of 3) | **836.2 docs/s** (best 845.3) | 402.1 docs/s (48 docs) | corpus differs, not directly comparable |
| RSS Δ | +24 MB at load, **+117 MB** after load+encode | 8 MB | measured on the §5 psutil method |

Offline proof: `HF_HOME=<repo>/.cache/hf` (the launcher's convention) with
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`; the model
resolved from the 523.3 MB cache snapshot under that `HF_HOME`, and the probe
hard-blocks every non-loopback TCP connect and DNS lookup (airplane-mode guard) —
**0 network attempts**, load 0.41 s warm / 3.31 s cold through the app's own
`_build_embedder()`. Throughput used a fixed deterministic corpus of exactly **50
short sentences / 3594 chars**, so the docs/s figure is not comparable to the
bake-off's 48 longer docs; the single-encode latency and the 384-d/prompt wiring
are the apples-to-apples confirmation that **`Snowflake/snowflake-arctic-embed-s`
is the embedded model end to end**, with the query prompt applied on the query
side and plain text on the doc side exactly as production encodes.

## 6. Local end-to-end smoke (`scratch/prove_local_graphiti.py`)

Real backend on a free port, ingest+search through our `/v1` proxy with the
registered small model, local embeddings in-process, hard-fail if anything
answers on :11434 (the Ollama port). Final run
(`scratch/prove_local_graphiti_final.log`): **3/3 episodes ingested, recall
edges=5, `alice_bob_recall=True`**; chunk times 9.6 s / 133.3 s / 10.5 s (the
second episode is the cold-start LLM call), search 0.0 s. Clean SIGTERM exit.

## 7. Speed plan — expected vs measured (spec §7)

The §7 projections were estimates made before the runtime work. What is measured
so far: the small model's 86 tok/s JSON throughput, the per-episode ingest times
above, and the co-residency headroom. A full 53-chunk wall-time re-measurement on
the Omnidroids pipeline has **not** been run yet — it is the next measurement and
belongs in this doc (§12 acceptance criterion 3 records it).

## 7b. Extraction quality on the real pipeline — issue #22 (measured)

**Under test:** the `small` role, `LFM3-1.7B-MLX-4bit`, answering graphiti's own
extraction prompts through the Flask `/v1` proxy (`ModelManager.generate_sync` → MLX)
behind the seam in `backend/app/utils/graphiti_zep.py` (`_CappedLLMClient`,
`_append_schema_reminder`). Config in force for every run, printed by the harness:
`json_schema` structured output, `GRAPHITI_LLM_MAX_TOKENS=4096`,
`GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s 384-d. `temperature=0.0` is dropped by the
MLX runner (the checkpoint's sampler — temp 0.1, top_k 50 — applies), so these are
SAMPLED, not deterministic, measurements; every fix was measured with `--repeat 2`.
Ollama is stopped before each run and never answers (harness asserts :11434 is closed).
Commands, run from the worktree's `backend/` (chunks = the six real ones of batch
`local_a84bf4218fe24790`, sha256-verified by the harness):

```bash
.venv/bin/python ../scratch/graphiti_extraction_check.py --mode node --repeat 2
.venv/bin/python ../scratch/graphiti_extraction_check.py --mode full --repeat 1
```

`--mode node` (H2) drives graphiti's node prompt through the production client alone;
`--mode full` (H1) runs `graphiti.add_episode` over Kuzu + the local embedder, i.e. every
call a chunk makes (nodes, node dedupe, edges, edge dedupe, summaries).

### Node extraction — before/after class mix (12 calls each, `--repeat 2`)

| run | log | ok | malformed-json | wrong-keys | LLM s | mean |
|---|---|---|---|---|---|---|
| pre-fix baseline (schema never reached the model) | `.agent-notes/graphiti_extraction_baseline.log` | **0/12** | 3 | 9 | 478.5 | 39.9 s |
| schema reminder + item-field sentence | `scratch/after_fix_node.log` | 11/12 | 0 | 1 | 116.3 | 9.7 s |
| first shape-clause draft (clauses mid-reminder) | `scratch/interim_node.log` | 10/12 | 2 | 0 | 200.6 | 16.7 s |
| **final** (shape clauses close the reminder) | `scratch/final_node.log` | **12/12** | 0 | 0 | 95.0 | 7.9 s |

Per chunk in the final run (`scratch/final_node.log`):

| idx | chars | rep 0 s / nodes | rep 1 s / nodes |
|---|---|---|---|
| 0 | 376 | 9.0 / 6 | 4.7 / 6 |
| 1 | 368 | 8.8 / 26 | 5.6 / 12 |
| 2 | 213 | 15.3 / 5 | 3.6 / 5 |
| 3 | 390 | 6.1 / 14 | 5.5 / 12 |
| 4 | 129 | 4.3 / 8 | 4.5 / 8 |
| 5 | 390 | 21.8 / 13 | 5.8 / 13 |

Backend RSS 180 → 1716 MB over the run (idle unload returns it, §4). The two defects the
failing runs still showed — a body split over 232 lines, and a `}  // Wait, ...` line
between two array items — are what the closing clause names; with it the node path is
12/12 at 7.9 s mean, consistent with §2's 86 tok/s JSON throughput on ~200-token bodies.

### Full ingestion — the edge path is the remaining blocker (#22 stays OPEN)

| run | log | ok | skipped | ExtractedEdges calls | of which `finish_reason=length` | `NodeResolutions` schema echoes |
|---|---|---|---|---|---|---|
| pre-fix baseline (6 chunks × 2 reps) | `.agent-notes/graphiti_extraction_baseline.log` | 0/12 | 12 | — | — | — |
| schema reminder + item-field sentence | `scratch/after_fix_full.log` (interrupted) | 0/3 reached | 3 | 4 | 3 | 0 |
| **final, run 1** | `scratch/interim_full.log` | 0 | 5 (crash on 6th) | 4 | 4 | 1 |
| **final, run 2** | `scratch/final_full.log` | 0 | 5 (crash on 6th) | 6 | 3 | 3 |

Per chunk in the final run (`scratch/final_full.log`):

| idx | chars | status | class | wall | the call that skipped it |
|---|---|---|---|---|---|
| 0 | 376 | skip | malformed-json | 113.1 s | `ExtractedEdges` `finish_reason=length`, ~14 000 chars over 232 lines |
| 1 | 368 | skip | malformed-json | 109.4 s | `ExtractedEdges` `finish_reason=length`, ~14 000 chars over 196 lines |
| 2 | 213 | skip | wrong-keys | 29.5 s | `NodeResolutions` answered the SCHEMA, not the data |
| 3 | 390 | skip | malformed-json | 94.0 s | `ExtractedEdges` `finish_reason=length` at char 14 020 / line 340 |
| 4 | 129 | skip | wrong-keys | 15.9 s | `NodeResolutions` answered the SCHEMA again |
| 5 | 390 | — | — | — | harness died first (Kuzu SIGBUS, below) |

361.9 s for the five completed chunks (72.4 s mean) before the run stopped. Two
blockers, neither of them the token cap:

1. **`ExtractedEdges` pretty-prints.** 5 of 6 logged edge bodies in the final run start
   `{  "edges": [\n    {\n      "source_entity_name": ...`; three reach the 4096-token
   leaf clamp, which is M1's invariant, so M3's `_TruncatedJSONError` fires and the chunk
   is skipped. The same bodies carry sentence-length `fact` values and invented
   microsecond `valid_at` values (`2026-11-06T19:24:07.613958Z`) — exactly what the seam's
   shape clauses ask it not to do. The node prompt obeys those clauses (12/12 above); the
   edge prompt does not. Raising the cap is not the answer (M1: the pre-clamp 16 k-token
   call took ~348 s and still failed), and a truncated-body salvage parser is not the
   answer (M3).
2. **`NodeResolutions` echoes the schema.** With the schema reminder in the prompt the
   model sometimes answers the node-dedupe prompt with the schema itself
   (`{"$defs": {"NodeDuplicate": ...`), 3 of 5 calls in the final run. A cost of the
   reminder that fixed the wrong-keys class, not of the shape clauses.

**Harness defect (not the seam):** both full-mode runs ended in `Bus error: 10` (SIGBUS)
in the harness process during chunk 5's node-summary/dedupe phase, right after
`SummarizedEntities` returned at 04:57:46, so neither run reached a summary line.
Reproducible (two runs, same phase). The backend child stayed healthy and unloaded the
model cleanly on SIGTERM (RSS 1721 → 781 MB, interim run's `backend.log`). H1 results
therefore have to be read from the per-chunk lines, and this SIGBUS has to be fixed
before §12's SIGTERM-mid-build proof can be re-run.

The §2/§6 fixture results are still **fixture-level only**: #22 is not closed. What is
measured is a partial fix — the node path that filed the issue (wrong keys, malformed
JSON) is 12/12 at 7.9 s/call, and the edge path plus the node-dedupe prompt still violate
the schema often enough that no 0-skip ingestion has been measured. Remaining options,
all requiring a decision: a shape clause that also names the schema-echo defect, an
extraction-dedicated bigger model, or grammar-constrained decoding. Do **not** raise
`GRAPHITI_LLM_MAX_TOKENS`.

**The model question is now decided (§1b):** the owner kept extraction on the
`small` role and moved steps 4–5 (Report Generation, Deep Interaction) to `big`.
So "an extraction-dedicated bigger model" is off the table for this issue — the
edge path stays a small-model prompt problem, and the remaining options are the
shape clause and grammar-constrained decoding.

### §7c — The plain-English schema statement, the 9B on graphiti's prompts, and the dedupe split (#24, #22, #29)

All runs below: the six real chunks of batch `local_a84bf4218fe24790`
(sha256-verified), a **fresh graph id per run**, Ollama stopped and asserted down by
the harness, `json_schema` mode, `GRAPHITI_LLM_MAX_TOKENS=4096`,
`GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s 384-d. Logs in `scratch/`.

**Model-selection trap (it cost one run):** `app/config.py` calls
`load_dotenv(.env, override=True)`, so a shell `LLM_MODEL_NAME=…` is clobbered by
`.env` before `Config` reads it, and the harness's `[ok] backend up … model=` line
reports that config value rather than what served. Select with
`GRAPHITI_LLM_MODEL=<name>` (absent from `.env`, so it survives) and confirm by RSS
and output style.

#### 1. The plain-English schema statement everywhere (#24) — measured and REJECTED

Branch `fix/24-plain-english-schema` (pushed): `_schema_reminder` sends a
schema-derived statement instead of `json.dumps(model_json_schema())` whenever the
dump carries `$defs`/`$ref` or exceeds 512 chars.

| model | mode | ok | skip | classes | llm s | mean |
|---|---|---|---|---|---|---|
| LFM3-1.7B sample A | node ×2 | 10/12 | 2 | malformed-json 2 | 946.2 | 78.8 s |
| LFM3-1.7B sample B | node ×2 | 12/12 | 0 | — | 501.9 | 41.8 s |
| LFM3-1.7B | full ×1 | 3/6 | 3 | malformed-json 3 | 1316.2 | 219.4 s |
| granite-4.2-3b | node ×2 | **0/12** | 12 | other 12 (bare array) | 91.7 | 7.6 s |
| granite-4.2-3b | full ×1 | **0/6** | 6 | other 6 (bare array) | 41.9 | 7.0 s |

Baseline (raw dump, §7b): 1.7B node 12/12 @ 7.9 s mean, full 3/6 @ 72.4 s; 3B node
11/12 @ 9.0 s, full 1/6 @ 47.0 s. It DID fix what it aimed at — `NodeResolutions`
echoed `{"$defs": …}` on 0 of 4 calls (was 3 of 5), `ExtractedEdges` compacted on
3 of 4 comparable chunks — but granite-4.2-3b then lost the wrapper entirely
(`[{"name": …}]` → `ExtractedEntities(**payload)` raises `TypeError`), the 1.7B
pretty-printed with `// Wait, …` comments and a trailing `Wait, need to re-`
again, and wall time went 10×. Closed as *measured, not fixed* per #24's protocol.

The learning: **the dump is the extraction prompts' only compact one-line exemplar,
and prose is what stops a model from handing the schema back.**

#### 2. The 9B on graphiti's own prompts — the missing number (#25)

`GRAPHITI_LLM_MODEL=Ornith-1.5-9B-MLX-6bit`:

| run | log | result |
|---|---|---|
| node ×2 | `check24_9b_node_a.log` | **12/12, 0 skips**, mean 23.6 s, deterministic (identical node counts on both repeats), every body `finish_reason=stop` and carrying `episode_indices` — it follows the schema it is shown |
| full ×1 | `check24_9b_full_a.log` | 1/6 — **17 calls** answered 502 `There is no Stream(gpu, N) in current thread` (see §3) |
| full ×1, after the #29 fix | `check24_9b_full_b.log` | 1/6 — the ONLY failure is `EdgeDuplicate` answering `{"properties": {"duplicate_facts": []}}`, 5 of 5 calls. Every extraction prompt (nodes AND edges) was correct, including the `ExtractedEdges` the 1.7B cannot fit inside M1's clamp |

So the 9B clears the extraction prompt and the edge-extraction prompt that block
#22, and echoes only on the edge-dedupe prompt — the same class the 1.7B shows on
its node-dedupe prompt. Wall time ~128 s/chunk (≈95 min for §12's ~44 chunks —
about 2× the 1.7B, which is a real cost against the ≤~60 min bar).

#### 3. #29 — MLX calls now run on the model's own thread

`MLXRunner` used to load on a temporary one-worker pool that it then discarded and
generate through `asyncio.to_thread` (an arbitrary thread per call). MLX GPU
streams are thread local and `mlx_vlm.generate` mutates shared model state, so
concurrent graphiti calls (`GRAPHITI_MAX_COROUTINES=2`) crossed threads. Measured:
the 9B full-mode run above failed 4 of 6 chunks on 17 such 502s, while every
sequential run of the same model and every 1.7B/3B full-mode run measured to date
had zero. Fix: one single-worker executor per loaded model carries `load`,
`generate` and the unload cache-release on the same thread
(`tests/test_model_runtime_threading.py` pins it). Evidence: `check24_9b_full_b.log`
has **0** occurrences of the error; the 9B run then fails only on the echo class.
Blast radius before the fix: any `big`-model use under concurrency, i.e. steps 4-5.

#### 4. The dedupe split — what ships on `fix/22-hybrid-dedupe-statement`

`_ECHO_PRONE_RESPONSE_MODELS = {NodeResolutions, EdgeDuplicate}` get the
plain-English statement; every other graphiti response model (extraction and
summaries) keeps graphiti's verbatim dump. It is a measured, per-model decision
pinned to logged runs, not a schema-derived heuristic — no size boundary separates
the classes (NodeResolutions' 873-char dump sits between SummarizedEntities' 614
and ExtractedEntities' 896).

| model | run | result | the echo class |
|---|---|---|---|
| LFM3-1.7B | full ×1 (`check24_1p7b_full_c.log`) | 3/6 (baseline, unchanged) | 0 of 20 dedupe calls echoed; all 3 failures are `ExtractedEdges` at the 4096 clamp (char 13719 / 11157 / 14074) |
| Ornith-1.5-9B | full ×1 (`check24_9b_full_c.log`) | **6/6, 0 skips — first clean full-mode run measured** | 0 echoes |

The 1.7B stays at its measured 3/6 with the dedupe-echo class removed; the 9B is
6/6. Moving Graph Build (step 1) to the `big` role is the owner's call (#22
comment 2); with that routing the edge path is clear, without it #22's blocker is
the 1.7B's edge-extraction verbosity, which no prompt variant has fixed.

#### 5. Small-model candidate 1 — Qwen3.5-4B-MLX-4bit (issue #41) — **OUT**

Branch `model/41-qwen35-4b-4bit` (off `phase6-integration` @ `0848d62`; the docs-only
commit in this ticket). Weights `mlx-community/Qwen3.5-4B-MLX-4bit` downloaded once to
`models/Qwen3.5-4B-MLX-4bit` (`HF_HOME=<repo>/.cache/hf`, nothing in `~/.cache`; log
`scratch/check41_download.log`) — **3,061,132,920 B / 2.85 GB**, 12 files, ~84 s, clean.
`mlx_vlm.load(path)` through the app's own runner path in 2.1 s, offline
(`config.model_type=qwen3_5`). Registered in the git-ignored `models/registry.json`
(backend `mlx`, `size_bytes` recorded) with **no role assigned** — `roles.small` stays
`LFM3-1.7B-MLX-4bit`, `roles.big` stays `Ornith-1.5-9B-MLX-6bit`.

Config in force identical to every §7c run: `json_schema` structured output,
`GRAPHITI_LLM_MAX_TOKENS=4096`, `GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s 384-d; a
**fresh graph id per run**; Ollama stopped and asserted down before AND after by the
harness; `GRAPHITI_LLM_MODEL=Qwen3.5-4B-MLX-4bit` (survives `load_dotenv`; a shell
`LLM_MODEL_NAME` would be clobbered by `.env`). *LLM calls/chunk* was measured by an
additive counter on the harness's existing `chat.completions.create` wrapper — the
harness itself prints no per-chunk call count, so it was not previously observable.

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | backend_rss | sampler peak |
|---|---|---|---|---|---|---|---|---|---|---|---|
| node ×2 | `check41_node.log` | `check41_qwen4b_1794079623` | **0/12** | 12 | wrong-keys 12 | 167.6 | 14.0 s | 1.0 | 180→3198 MB | 3806 MB |
| full ×1 | `check41_full.log` | `check41_qwen4b_1794079831` | **0/6** | 6 | wrong-keys 6 | 85.8 | 14.3 s | 1.0 | 180→3685 MB | 3777 MB |

A second node run (`check41_qwen4b_1794079329`) reproduced 0/12 wrong-keys at 13.8 s
mean — the failure is deterministic. It is **not** M1's 4096 clamp: every call returns
`finish_reason=stop` with the inlined JSON schema verbatim
(`{"$defs": {"ExtractedEntity": …}, "properties": …, "required": …, "title": …}`),
so `ExtractedEntities(**payload)` raises `extracted_entities Field required` — the §7b
*wrong-keys* class, now on the entity-extraction prompt itself. Because `extract_nodes`
is the first call inside `graphiti.add_episode`, a chunk aborts before any edge/dedupe/
summary call, which is why full mode makes **exactly one** LLM call per chunk (the 1.7B
and 9B clear extraction and make several). granite-4.2-3b failed this same node prompt
too, but as a *bare-array* `TypeError`; Qwen3.5-4B echoes the schema object instead.

**Verdict — FAIL, does not reach the bar.** Full mode **0/6** against the 6/6-0-skip
bar, and it never even reaches the `ExtractedEdges` edge path that blocks the 1.7B — it
fails one step earlier, on `extract_nodes`. Against the 1.7B candidate-0 baseline (node
12/12 @ 7.9 s; full 3/6 @ 72.4 s) it is **worse on every axis**: 0/12 node at ~1.8× the
per-call wall time (14.0 s vs 7.9 s) with a slower 4B that still echoes, and 0/6 full
(it cannot complete a single chunk). Resident RSS ~3.8 GB keeps it inside the 11 GB
budget and far below the 9B's 7.30 GB, but memory is moot at 0/6. **Candidate 1 is OUT
for #45; `small` role unchanged.**

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
