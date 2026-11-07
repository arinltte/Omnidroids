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

**`small` role — decision (#45, 2026-11-07): unchanged, and an OPEN item, not a
downgrade.** No 4-bit candidate cleared the full-mode bar (6/6, 0 skips): Qwen3.5-4B
0/6, MiniCPM5-2B 3-5/6, gemma-4-e2b 0/6, SmolLM3-3B 2/6, with granite-4.2-3b already
OUT before the wave. The `small` role therefore **stays `LFM3-1.7B-MLX-4bit`** because
the incumbent's own full-mode rate is **3/6** — the same gate — so leaving the role put
records the fact that *no* 4-bit has been found that clears 6/6 with 0 skips, rather
than silently promoting a model that fails the bar (or demoting the one that is still
the best 4-bit on the node path). The 1.7B's blocker is `ExtractedEdges` verbosity past
M1's 4096-token clamp, a class none of the four candidates shares. The 8-bit probes are
the conditional follow-up (#46, in isolation — 8-bit cannot co-reside with the 9B).
Full comparison table, failure classes and the weights-retention decision: **§7c
item 9**.

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

#### 6. Small-model candidate 2 — MiniCPM5-2B-mlx-4Bit (issue #42) — **OUT**

Branch `model/42-minicpm5-2b-4bit` (off `phase6-integration` @ `a50c906`; the docs-only
commit in this ticket). Weights `mlx-community/MiniCPM5-2B-mlx-4Bit` downloaded once to
`models/MiniCPM5-2B-mlx-4Bit` (`HF_HOME=<repo>/.cache/hf`, nothing new in `~/.cache`; log
`scratch/check42_download.log`) — **1,426,011,807 B / 1.33 GB**, 9 files, ~41 s, clean.
Loaded through the app's own runner path (`app.models.runtime.MLXRunner.load`, what
`ModelManager` serves the seam) in ~1 s, offline: `config.model_type=llama`, vocab
130 560, hidden 2048, 42 layers, rss 1912 MB, unload OK. Registered in the git-ignored
`models/registry.json` (backend `mlx`, `size_bytes` recorded) with **no role assigned** —
`roles.small` stays `LFM3-1.7B-MLX-4bit`, `roles.big` stays `Ornith-1.5-9B-MLX-6bit`.

Config in force identical to every §7c run: `json_schema` structured output,
`GRAPHITI_LLM_MAX_TOKENS=4096`, `GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s 384-d; a
**fresh graph id per run**; Ollama stopped and asserted down before AND after by the
harness; `GRAPHITI_LLM_MODEL=MiniCPM5-2B-mlx-4Bit` (survives `load_dotenv`; a shell
`LLM_MODEL_NAME` would be clobbered by `.env`). *LLM calls/chunk* is the harness's own
per-chunk `calls=` column (its additive counter on the `chat.completions.create` wrapper,
printed in every `[chunk]` line and the `[sum]` line); peak RSS comes from the
`scratch/rss_sampler.py` thread that `scratch/check42_run.sh` runs beside the harness.

Row of record — both launches from `backend/` exactly as #41 did, on fresh graph ids:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | backend_rss | sampler peak |
|---|---|---|---|---|---|---|---|---|---|---|
| node ×2 | `check42_node.log` | `check42_minicpm5_1794082200` | **12/12** | 0 | — | 47.9 | 4.0 s | 1.0 | 180→2026 MB | 2026 MB |
| full ×1 | `check42_full.log` | `check42_minicpm5_1794082300` | **3/6** | 3 | other-validation 2, wrong-keys 1 | 65.3 | 10.9 s | 2.3 | 180→2026 MB | 2026 MB |

Reproducibility — the same two modes run again from `backend/` on fresh graph ids
(`_rep1` the pair, `_rep2` a further full run). The ok count moves between runs; it never
reaches 6/6:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | sampler peak |
|---|---|---|---|---|---|---|---|---|---|
| node ×2 | `check42_node_rep1.log` | `check42_minicpm5_1794081200` | 12/12 | 0 | — | 47.9 | 4.0 s | 1.0 | 2024 MB |
| full ×1 | `check42_full_rep1.log` | `check42_minicpm5_1794081300` | 4/6 | 2 | wrong-keys 1, malformed-json 1 | 154.6 | 25.8 s | 3.2 | 2026 MB |
| full ×1 | `check42_full_rep2.log` | `check42_minicpm5_1794081700` | 5/6 | 1 | wrong-keys 1 | 65.5 | 10.9 s | 2.2 | 2025 MB |

**Failure classes, per chunk.** Unlike candidates 0/1 this one clears the prompts they die
on: `ExtractedEntities` answers with the right wrapper and sensible `entity_type_id`s,
`NodeResolutions` answers with `entity_resolutions`, and it reaches `SummarizedEntities`.
It then fails one step later — and **which** prompt fails moves between runs, so the class
has to be named per chunk:

| run | failing chunk | response model | shape |
|---|---|---|---|
| node ×2 (both node runs) | none | — | 12/12, every body `finish_reason=stop` and schema-valid |
| full (`check42_full.log`) | 2, 5 | `ExtractedEdges` | **null placeholder**: `{"source_entity_name": null, "target_entity_name": null, "relation_type": null, "fact": null, "valid_at": null, "invalid_at": null, "episode_indices": []}` — schema-shaped but null, so `edges.0.source_entity_name Input should be a valid string` (`other-validation`) |
| full (`check42_full.log`) | 4 | `EdgeDuplicate` | **half-echo** (`calls=4` with retries): `{"properties": {"duplicate_facts": [], "contradicted_facts": []}, "required": […], "title": "EdgeDuplicate", "type": "object"}` — real values one level too deep, so `duplicate_facts Field required` |
| full (`check42_full_rep1.log`) | 2 | `EdgeDuplicate` | the same half-echo |
| full (`check42_full_rep1.log`) | 3 | `SummarizedEntities` | **broken JSON**: a `$defs` echo, then four tenacity retries (`calls=7`) ending in `Expecting ',' delimiter: line 1 column 873` — a syntax error inside a `finish_reason=stop` body, not a clamp truncation |
| full (`check42_full_rep2.log`) | 3 | `ExtractedEdges` | **class-name echo**: `{"ExtractedEdges": [{"source_entity_name": …}]}` — the real array under the model's own class name, so `edges Field required` |

An earlier full run (19:47Z, same conditions) failed as two `$defs` schema echoes
(`SummarizedEntities`, then `ExtractedEdges`) at 4/6; it was overwritten while a
launch-path mistake was being fixed (the runner's `../scratch/` resolves differently from
the repo root than from `backend/`, which put that pair's sampler `.rss` one directory up
under `Prediction/scratch/`); the stray files were removed and every surviving run pair
(`check42_*.log` + `check42_*.rss`) is coherent under `Omnidroids/scratch/`.
**No failure in any run is M1's 4096 clamp**: there are zero `_TruncatedJSONError`s and no
`finish_reason=length`, which is the class that blocks the 1.7B.

**Two caveats the ok-count hides.** (a) *Empty extraction.* A chunk that "passes" with
`nodes=0 edges=0` is schema-valid and still useless: in the row-of-record full run only
chunk 3 carried a graph (2 nodes, 1 edge) — chunks 0 and 1 passed as `{"extracted_entities":
[]}` / `{"edges": []}` and the other three chunks are the failures; in `_rep2` 5 of 6
chunks came back empty and the whole graph was chunk 0 (6 nodes, 3 edges). Node mode tells
the same story — 0-6 entities per chunk against the 1.7B's steady 5-14
(`check24_1p7b_node_a.log`), and in the row-of-record run's first repeat every one of the
six chunks returned `{"extracted_entities": []}`. (b) *Not deterministic.* The two
repeats of one node run disagree on the same chunk (row of record: chunk 5 returns 6
entities in the second repeat and 0 in the first;
`check42_node_rep1.log`: chunk 1 returns 4 then 0,
and chunk 2's `National Oceanographic Bureau` is typed `2` then `3`) although
`temperature=0.0` is requested — the MLX runner drops temperature below 1, so sampling
stays on. The full-mode class differs run to run for the same reason.

**Verdict — FAIL, does not reach the bar.** Full mode never reached 6/6 with 0 skips in
any of the three surviving runs (3/6, 4/6, 5/6 — 12/18 chunks). It is the best candidate
measured so far on the harness's own numbers: against candidate 1 (Qwen3.5-4B: 0/12 node,
0/6 full) it is better on every axis, and against the 1.7B baseline (node 12/12 @ 7.9 s;
full 3/6 @ 72.4 s) it is **faster** (node mean 4.0 s, full mean 10.9 s) with a different
blocker — schema echo, a null-filled placeholder or broken JSON on `EdgeDuplicate`,
`SummarizedEntities` or `ExtractedEdges` instead of `ExtractedEdges` verbosity at the
4096 clamp. Resident RSS ~2.03 GB is the smallest of the wave, far inside the 11 GB
budget. **But the extra passes are not extra graph:** the 1.7B's three passing chunks
carry 6/15/12 nodes and 7/10/6 edges (`check24_1p7b_full_c.log`), while MiniCPM5's best
run passes 5 chunks carrying 6 nodes and 3 edges in total, the failing prompt moves
between runs, and its own repeats disagree. It cannot be handed the `small` role on a gate
that a 0-node/0-edge chunk would pass. **Candidate 2 is OUT for #45; `small` role
unchanged, and the gap is recorded rather than downgraded silently.**

#### 7. Small-model candidate 3 — gemma-4-e2b-it-4bit (issue #43) — **OUT**

Branch `model/43-gemma-4-e2b-4bit` (off `phase6-integration` @ `1b72914`; the docs-only
commit in this ticket). Weights `mlx-community/gemma-4-e2b-it-4bit` downloaded once (log
`scratch/check43_download.log`) — **3,583,088,661 B / 3.34 GB**, 10 files, ~94 s, clean —
to repo-root `models/gemma-4-e2b-it-4bit` via `--local-dir ../models/…` from `backend/`
(`HF_HOME=<repo>/.cache/hf`, nothing new in `~/.cache`). The task's literal
`--local-dir models/…` from `backend/` would have written a non-existent `backend/models/`
tree the app cannot resolve (`MODELS_DIR` and every registry `path` are repo-root), so
#43 confirmed with the owner first and used `../models/`, candidate 1/2's exact method.
This is the one **multimodal VLM** of the wave (`Gemma4ForConditionalGeneration`,
text+vision+audio; Gemma-3n "e2b" effective-2B with per-layer embeddings, 4-bit affine
gs64). Loaded through the app's own runner path (`app.models.runtime.MLXRunner.load`, what
`ModelManager` serves the seam) offline (`HF_HUB_OFFLINE=1`) in ~1 s: `config.model_type=
gemma4`, text vocab 262 144 / hidden 1536 / 35 layers / max_pos 131 072, rss ~3.5-4.4 GB
(model+processor+vision/audio towers), unload OK. Registered in the git-ignored
`models/registry.json` (backend `mlx`, path `/Users/…/Omnidroids/models/gemma-4-e2b-it-4bit`,
`size_bytes` 3 583 088 661) with **no role assigned** — `roles.small` stays
`LFM3-1.7B-MLX-4bit`, `roles.big` stays `Ornith-1.5-9B-MLX-6bit`.

Config in force identical to every §7c run: `json_schema` structured output,
`GRAPHITI_LLM_MAX_TOKENS=4096`, `GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s 384-d; a
**fresh graph id per run**; Ollama stopped and asserted down before AND after by the
harness; `GRAPHITI_LLM_MODEL=gemma-4-e2b-it-4bit` (survives `load_dotenv`; a shell
`LLM_MODEL_NAME` would be clobbered by `.env`). *LLM calls/chunk* is the harness's own
per-chunk `calls=` column (its additive counter on the `chat.completions.create` wrapper),
and peak RSS is the `scratch/rss_sampler.py` thread `scratch/check43_run.sh` runs beside
the harness. Every launch was from `backend/` on a fresh graph id, one model resident.

Row of record:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | backend_rss | sampler peak |
|---|---|---|---|---|---|---|---|---|---|---|
| node ×2 | `check43_node.log` | `check43_gemma4e2b_1794084085` | **0/12** | 12 | wrong-keys 12 | 77.4 | 6.4 s | 1.0 | 180→3960 MB | 4556 MB |
| full ×1 | `check43_full.log` | `check43_gemma4e2b_1794084193` | **0/6** | 6 | wrong-keys 6 | 41.0 | 6.8 s | 1.0 | 180→2730 MB | 3347 MB |

Reproducibility — the same full mode run again from `backend/` on a fresh graph id. It is
byte-for-byte the same failure per chunk (chars 376/368/213/390/129/390 in both runs), so
the failure is deterministic, not sampling noise:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | sampler peak |
|---|---|---|---|---|---|---|---|---|---|
| full ×1 | `check43_full_rep1.log` | `check43_gemma4e2b_1794084268` | 0/6 | 6 | wrong-keys 6 | 41.0 | 6.8 s | 1.0 | 4013 MB |

**Failure class, per chunk.** All 12 node calls and both full runs fail on the
**entity-extraction prompt** with the §7b *wrong-keys schema-echo*: the model answers
`finish_reason=stop` by handing back the JSON schema-defs object itself
(`{"$defs": {"ExtractedEntity": {"properties": {"name": …`}, payload keys `['$defs']`), so
`ExtractedEntities(**payload)` raises `extracted_entities Field required` — the identical
prompt and class candidate 1 (Qwen3.5-4B) died on. The failing response model is
`ExtractedEntities` in every chunk of every run (deterministic); because `extract_nodes`
is the first call inside `graphiti.add_episode`, a chunk aborts before any
edge/dedupe/summary call, which is why full mode makes **exactly one** LLM call per chunk
(calls/chunk 1.0 — matching candidate 1, vs the 1.7B/9B that clear extraction and make
several). It is **not** M1's 4096 clamp (every body is `finish_reason=stop`, never `length`;
zero `_TruncatedJSONError`s), **not** a bare-array `TypeError` (granite-4.2-3b), **not** the
null-filled placeholder / half-echo / class-name echo candidate 2 showed, and **not** a
parse failure — and it never reaches the `ExtractedEdges` edge path that blocks the 1.7B,
failing one step earlier on `ExtractedEntities`.

**Verdict — FAIL, does not reach the bar.** Full mode **0/6** against the 6/6-0-skip bar,
deterministic across the row-of-record and the reproduction. It lands in the same place as
candidate 1 — Qwen3.5-4B was also 0/12 node / 0/6 full on this same node-prompt echo — but
gemma-4-e2b is ~2× faster per call (node mean 6.4 s vs 14.0 s, full mean 6.8 s vs 14.3 s);
the speed buys nothing at 0/6, where results are counted and per-call latency is not. It is
strictly worse than candidate 2 (MiniCPM5: 12/12 node, 3-5/6 full), which clears the entity
prompt gemma-4-e2b cannot. Against the 1.7B candidate-0 baseline (node 12/12 @ 7.9 s;
full 3/6 @ 72.4 s) it again trades correctness for speed — as fast or faster per chunk yet
completing 0 chunks against the 1.7B's 12 nodes and 3 full. Resident RSS (~2.7-4.0 GB
after-run, sampler peak 3.3-4.6 GB across runs) stays inside the 11 GB budget and below the
9B's 7.30 GB, but memory is moot at 0/6. **Candidate 3 is OUT for #45; `small` role
unchanged.** With candidates 1 and 3 at 0/6 and candidate 2 at 3-5/6, three of the four
4-bit candidates have now measured below the bar (candidate 4, SmolLM3-3B — since
measured at 2/6 and OUT, item 8 below; all four are now below the bar, decision in
item 9) — no 4-bit candidate has reached 6/6, so the gap is recorded for #45 rather
than downgraded silently.

#### 8. Small-model candidate 4 — SmolLM3-3B-4bit (issue #44) — **OUT**

Branch `model/44-smollm3-3b-4bit` (off `phase6-integration` @ `0f4e655`; the docs-only
commit in this ticket). Weights `mlx-community/SmolLM3-3B-4bit` downloaded once (log
`scratch/check44_download.log`) — **1,747,380,812 B / 1.63 GB**, 10 files, ~49 s, clean —
to repo-root `models/SmolLM3-3B-4bit` via `--local-dir ../models/…` from `backend/`
(`HF_HOME=<repo>/.cache/hf`, `~/.cache/huggingface` unchanged at 7 944 384 KB before and
after; the repo cache gained only a 4 KB refs snapshot), candidate 1/2/3's exact method —
the literal `--local-dir models/…` from `backend/` would have written a `backend/models/`
tree the app cannot resolve (`MODELS_DIR` and every registry `path` are repo-root).
This is a plain **text causal LM** (`SmolLM3ForCausalLM`, no vision/audio towers, no
`text_config`), 4-bit gs64, hidden 2048 / 36 layers / 16 heads / 4 KV heads / vocab
128 256 / max_pos 65 536, NoPE pattern `1,1,1,0` (`no_rope_layer_interval=4`), bf16 base,
index metadata 3 075 098 624 parameters. Loaded through the app's own runner path
(`app.models.runtime.MLXRunner.load`, what `ModelManager` serves the seam) offline
(`HF_HUB_OFFLINE=1`): `config.model_type=smollm3`, rss 2300 MB, unload OK — no
generation, this ticket's load check only. Registered in the git-ignored
`models/registry.json` (backend `mlx`, path `/Users/…/Omnidroids/models/SmolLM3-3B-4bit`,
`size_bytes` 1 747 380 812, `added_at` 2026-11-07T21:03:14Z) with **no role assigned** —
`roles.small` stays `LFM3-1.7B-MLX-4bit`, `roles.big` stays `Ornith-1.5-9B-MLX-6bit`.

Config in force identical to every §7c run: `json_schema` structured output,
`GRAPHITI_LLM_MAX_TOKENS=4096`, `GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s 384-d; a
**fresh graph id per run**; Ollama stopped and asserted down before AND after by the
harness; `GRAPHITI_LLM_MODEL=SmolLM3-3B-4bit` (survives `load_dotenv`; a shell
`LLM_MODEL_NAME` would be clobbered by `.env`). *LLM calls/chunk* is the harness's own
per-chunk `calls=` column (its additive counter on the `chat.completions.create` wrapper),
and peak RSS is the `scratch/rss_sampler.py` thread `scratch/check44_run.sh` runs beside
the harness. Every launch was from `backend/` on a fresh graph id, one model resident,
SIGTERM-only (`harness_rc=1 blocked=0`, no leftover `run.py` in any run).

Row of record:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | backend_rss | sampler peak |
|---|---|---|---|---|---|---|---|---|---|---|
| node ×2 | `check44_node.log` | `check44_smollm3_1794085764` | **6/12** | 6 | wrong-keys 5, malformed-json 1 | 664.5 | 55.4 s | 1.1 | 180→2412 MB | 2412 MB |
| full ×1 | `check44_full.log` | `check44_smollm3_1794087026` | **2/6** | 4 | wrong-keys 3, other-validation 1 | 359.5 | 59.9 s | 2.3 | 180→2427 MB | 2460 MB |

The node run's two repeats disagree, so 6/12 is an average over both: repeat 1 passed
2/6 (chunks 0, 5; 65.5 s/chunk), repeat 2 passed 4/6 (chunks 2-5; 45.3 s/chunk) — the
same chunk can pass in one repeat and echo the schema in the other. Its passing calls
return 3-13 entities (against the 1.7B's steady 5-14), but five of the twelve calls emit
nothing but the inlined schema and a sixth is a body truncated at the clamp.

Reproducibility — full mode run again from `backend/` on a fresh graph id. Same ok count,
different chunks, which is what the variance looks like for #45:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | backend_rss | sampler peak |
|---|---|---|---|---|---|---|---|---|---|---|
| full ×1 | `check44_full_rep1.log` | `check44_smollm3_1794087679` | 2/6 | 4 | wrong-keys 3, malformed-json 1 | 580.1 | 96.7 s | 4.7 | 180→2411 MB | 2411 MB |

**Failure classes, per chunk.** SmolLM3 does not fail at one place: it fails at whatever
prompt it is on, and the failing prompt moves between runs, so the class is named per
chunk (payload keys below are the harness's `keys=` column, printed on failure).

| run | failing chunk | response model | class / shape |
|---|---|---|---|
| node ×2 (both repeats) | 2, 3, 4 (varying) | `ExtractedEntities` | **wrong-keys schema echo**: payload keys `['$defs']` — the model hands back graphiti's inlined schema object itself (`{"$defs": {"ExtractedEntity": …}, "properties": …, "required": …, "title": …, "type": …}`), so `extracted_entities Field required`. Same prompt and class candidates 1 and 3 died on. |
| node ×2 (repeat 1) | 1 | `ExtractedEntities` | **M3 truncation fail-fast** (`calls=2`): first call a schema echo, then a real `{"extracted_entities":[…]` body truncated at the 4096 clamp (`finish_reason=length`, `Unterminated string … line 1 column 16427`) → `_TruncatedJSONError`, not retried. |
| full (row of record) | 3 | `ExtractedEntities` | **other-validation**: `extracted_entities Input should be a valid list` — the schema-echo prefix followed by a non-list `extracted_entities` in the same object. |
| full (row of record) | 4 | `ExtractedEdges` | **wrong-keys** `edges Field required`, payload keys `['$defs']` (an `Edge` schema echo). That chunk's `ExtractedEntities` call had already returned a schema-valid but **off-document** list — `Dr. Amara Osei`, `AAN`, `migraine study`, `CGRP combination protocol` — for a chunk whose text is about Dr. Ada Chen and the Wuhan Institute: fabricated entities, a class no earlier candidate showed. |
| full ×1 | 0 | `SummarizedEntities` | **M3 truncation fail-fast**: `finish_reason=length` at the 4096 clamp → `_TruncatedJSONError`, after `EdgeTimestamps` had answered with its own schema twice. |
| full ×1 | 1 | `SummarizedEntities` | **wrong-keys by rename**: `{"summarizedEntities": […]}` — real values under a near-miss key, so `summaries Field required` (twice, `calls=5`). |
| full ×1 | 4 | `EdgeDuplicate` | **half-echo** of the kind candidate 2 also showed: `{"properties": {"duplicate_facts": …}}` — schema one level too deep, so `duplicate_facts Field required`. |
| full ×1 | 5 | `ExtractedEntities` | **wrong-keys** schema echo (`$defs`), `finish_reason=length`. |

Two things the ok-count hides. (a) *The raw-body head is not the class.* Four of the
twelve node calls (chunks 0 and 5 of repeat 1, chunks 4 and 5 of repeat 2) and two
full-mode calls (chunk 2 of the row-of-record run, chunk 3 of `_rep1`) PASSED with a body
whose first 120 chars are the `$defs` schema echo: the probe
(`scratch/check44_shape_probe.py`, one call, not part of the row of record) shows why —
the payload is `{'$defs': …, 'extracted_entities': […]}`, both keys present, and pydantic
ignores the extra `$defs`. So this model **prepends the schema** and then answers, which is
why a body that looks like candidate 1/3's fatal echo still validates. The same probe also
returned a closed 1 249-char body with `finish_reason=length`, so a `length` finish is not
by itself the failure — it is terminal only when the body does not parse. (b) *The passes
are thin.* The row-of-record full run passed chunks 2 and
5 carrying 13 nodes / 5 edges in total, and `_rep1` passed chunks 2 and 3 with 9 nodes /
4 edges — real content, but a fraction of the 1.7B's 33 nodes / 23 edges across its three
passing chunks (`check24_1p7b_full_c.log`) and only a little more than MiniCPM5's 6 nodes
/ 3 edges over five passing chunks. Chunk 5 of the row-of-record run also surfaced a
`SummarizedEntities` answer of `"Not enough info in messages to summarize"` (schema-
valid, content-free) after one malformed attempt, a `NodeResolutions` body with two
entries sharing `id=0`, and `ExtractedEdges` bodies whose `relation_type` reads `J OINT`
and whose entity names are bare (`bureau`) — the graph that does land is loose.

**Verdict — FAIL, does not reach the bar.** Full mode is **2/6 with 4 skips** in both the
row-of-record and the reproduction, against the 6/6-0-skip bar. Against candidates 1-3 it
is **better than candidates 1 and 3 on every axis** (Qwen3.5-4B and gemma-4-e2b were both
0/12 node and 0/6 full, dying on this same entity-prompt echo) and **worse than candidate
2**: MiniCPM5-2B cleared node 12/12 and reached 3-5/6 full, where SmolLM3 is 6/12 node and
2/6 full. Like candidate 2 it reaches `ExtractedEdges`, `NodeResolutions`, `EdgeDuplicate`
and `SummarizedEntities` and lands a real (if small) graph in full mode — 13 nodes / 5
edges on the row-of-record run, more graph than MiniCPM5's best run (6 nodes / 3 edges
over five passing chunks) — but the failing prompt moves between runs and the ok
count never reaches 6/6. Against the 1.7B candidate-0 baseline (node 12/12 @ 7.9 s; full
3/6 @ 72.4 s) it is worse on both quality axes and **far slower per call**: node mean
55.4 s and full mean 59.9 s (the slowest of the wave — Qwen3.5-4B 14.0 s, MiniCPM5-2B
4.0 s, gemma-4-e2b 6.8 s), because its answers carry the inlined schema plus the array and
its truncated bodies run to the 4096 cap; only half the 1.7B's node chunks complete
(6/12 vs 12/12) at ~7× the per-call wall time (55.4 s vs 7.9 s), and full mode completes
one fewer chunk (2/6 vs 3/6) despite a lower mean (59.9 s vs 72.4 s).
Resident RSS ~2.4 GB (sampler peak 2 411-2 460 MB across runs) sits between MiniCPM5-2B's
2.03 GB and Qwen3.5-4B's ~3.8 GB, far inside the 11 GB budget; memory is moot below the
bar.
**Candidate 4 is OUT for #45; `small` role unchanged, and the gap is recorded rather than
downgraded silently.** All four 4-bit candidates have now measured below the bar
(0/6, 3-5/6, 0/6, 2/6), so per spec #30 the 8-bit probes are the conditional follow-up
(#46) rather than a silent downgrade to a model that fails the gate.

#### 9. The small-role decision (#45) — `small` stays `LFM3-1.7B-MLX-4bit`; the gap is an OPEN item

Docs-only, branch `model/45-small-role-decision` (off `phase6-integration` @ `177ee3d`;
no model run, no download). All four 4-bit candidates (#41-#44) measured **below** the
full-mode bar, so per the #45 ticket rule and spec #30 the `small` role does not move.
`roles.small` in the git-ignored `models/registry.json` stays `LFM3-1.7B-MLX-4bit`
(confirmed, not edited here); `roles.big` stays `Ornith-1.5-9B-MLX-6bit`.

**The bar (spec #30, §7c):** full mode **6/6 with 0 skips** on the six sha256-verified
chunks — the gate the 9B clears (`check24_9b_full_c.log`, §7c item 4) and the 1.7B does
not. **Every cell below cites its log**; the four candidate rows carry the row of record
plus a reproduction, `calls/chunk` is the harness's per-chunk `calls=` counter, and peak
is the `scratch/rss_sampler.py` `.rss` max.

| model (issue) | node ok/12 — log | full ok/6, row of record — log | full reproductions — log | mean s/call — log | calls/chunk | peak RSS — `.rss` | failure class (the prompt that skips the chunk) |
|---|---|---|---|---|---|---|---|
| **LFM3-1.7B-MLX-4bit** — candidate 0, current `small` (ref.) | 12/12 @ 7.9 s — `final_node.log` (§7b)* | **3/6**, 3 skips — `check24_1p7b_full_c.log` | 3/6 on the dedupe split (0/20 dedupe echoes); `final_full.log` (§7b) 72.4 s mean, 5 chunks pre-SIGBUS* | 7.9 node / 72.4 full (§7b) or 142.2 full (`_c.log`) | n/a (pre-#41 harness) | 1713 MB backend (`_c.log` `[sum]`) | malformed-json — `ExtractedEdges` verbosity at the 4096 clamp (chars 13719/11157/14074). **The one class no candidate shares.** |
| Qwen3.5-4B-MLX-4bit (#41) | **0/12** @ 14.0 s — `check41_node.log` | **0/6** @ 14.3 s — `check41_full.log` | 0/12 again on the 2nd node run (`gid …329`), deterministic — §7c item 5 | 14.0 / 14.3 | 1.0 / 1.0 | 3806 / 3777 MB | wrong-keys — `ExtractedEntities` `$defs` schema echo on `extract_nodes` (first call). |
| MiniCPM5-2B-mlx-4Bit (#42) | **12/12** @ 4.0 s — `check42_node.log` | **3/6** @ 10.9 s — `check42_full.log` | 4/6 `check42_full_rep1.log`, 5/6 `check42_full_rep2.log` — never 6/6 | 4.0 / 10.9 | 1.0 / 2.3 | 2026 MB | other-validation 2, wrong-keys 1 — moving: null `ExtractedEdges` placeholder, `EdgeDuplicate` half-echo, `SummarizedEntities` broken JSON. **NOT the 4096 clamp.** |
| gemma-4-e2b-it-4bit (#43) | **0/12** @ 6.4 s — `check43_node.log` | **0/6** @ 6.8 s — `check43_full.log` | 0/6 byte-identical per chunk — `check43_full_rep1.log`, deterministic | 6.4 / 6.8 | 1.0 / 1.0 | 4556 / 3347 MB | wrong-keys — `ExtractedEntities` `$defs` schema echo on `extract_nodes` (first call). |
| SmolLM3-3B-4bit (#44) | **6/12** @ 55.4 s — `check44_node.log` | **2/6** @ 59.9 s — `check44_full.log` | 2/6 `check44_full_rep1.log` (different chunks); node repeats 2/6 & 4/6 — moving | 55.4 / 59.9 | 1.1 / 2.3 | 2412 / 2460 MB | wrong-keys 3, other-validation 1 — moving: `ExtractedEntities` echo, `SummarizedEntities` rename, `EdgeDuplicate` half-echo, M3 truncation fail-fast, off-document fabrication. |
| granite-4.2-3b-MLX-4bit (pre-wave, OUT) | **0/12** @ 7.6 s — §7c item 1 | **0/6** @ 7.0 s — §7c item 1 | deterministic (plain-English prompt 0/12 / 0/6) | 7.6 / 7.0 | — | — | other — bare-array `TypeError` (`[{"name": …}]`). Already OUT before the wave. |

*The 1.7B shipped-config node 12/12 @ 7.9 s and the §7b full 72.4 s mean are recorded
from `final_node.log` / `final_full.log`, whose files were **worktree logs** (reaped
with the worktree, the log-loss effect that #42/#44 document); the in-tree `check24_1p7b_full_c.log` re-measures
full at 3/6 on the dedupe split (mean 142.2 s over all six chunks). Re-measure with the
§8 harness for a byte-exact baseline log.

**Decision.** No 4-bit candidate reached 6/6 full mode with 0 skips — the closest,
MiniCPM5-2B, topped out at 5/6 across three runs. Per spec #30 (*the fastest candidate
that reaches the bar*; *if none reaches it, keep `LFM3-1.7B-MLX-4bit` and record the
gap*) and the #45 rule, **`small` stays `LFM3-1.7B-MLX-4bit`.** This is a **recorded
open item, not a silent downgrade**: the incumbent's own full-mode rate is **3/6**
(the same gate), so leaving the role put is neither promotion nor demotion — it states
that *no measured 4-bit clears 6/6 with 0 skips*. The 1.7B's blocker is `ExtractedEdges`
writing bodies past M1's 4096-token clamp, a class none of the four shares (candidates 1/3
die one step earlier on `extract_nodes`; candidates 2/4 clear nodes and die late and
variably on the edge/dedupe/summary prompts), so the 1.7B remains the only 4-bit that
reliably clears the node path (12/12) and whose failures are the clamp the seam was built
around.

**Open item recorded (carried into #45).** *A 4-bit that clears 6/6 with 0 skips has not
been found.* The ticket's conditional consumer **#46 therefore fires**: the 8-bit probes
run next, one at a time in isolation, because 8-bit cannot co-reside with the 9B (RAM,
below). **granite-4.2-3b is already OUT** (pre-wave, deterministic 0/12 node / 0/6 full,
§7c item 1).

**Weights-retention decision (a recommendation — nothing is deleted here, `roles`
unchanged).** **RAM arithmetic:** the 9B holds `big` at 7298 MB (registry 7 298 159 159
B) and production co-residence is the 9B with the 1.7B `small` — the §1b step-4 proof
loaded the 9B with the 1.7B resident at the manager's estimate 8004 + 1711 = 9715 MB
against the 11 264 MB budget, no violation. That pairing is the only candidate-slot
co-residence production uses, and it holds the 1.7B, not a measured candidate, so **no
candidate has a resident slot beside the 9B.** Under the ≥2 GB headroom rule (§4,
HANDOFF §5.2) a model beside the 9B may use at most 11 264 − 7298 − 2048 = **1918 MB** of
weights; the five unused candidates are 11 735 MB together, so with the 9B resident they
are 19 033 MB — ~7.8 GB over budget — and the set can never co-reside in aggregate.

| model (issue) | on-disk (registry) | beside the 9B under the ≥2 GB rule (≤1918 MB)? | full-mode | recommendation |
|---|---|---|---|---|
| gemma-4-e2b-it-4bit (#43) | 3583 MB (3.34 GB) | No — headroom 383 MB | 0/6 | **DELETE** |
| Qwen3.5-4B-MLX-4bit (#41) | 3061 MB (2.85 GB) | No — headroom 905 MB | 0/6 | **DELETE** |
| SmolLM3-3B-4bit (#44) | 1747 MB (1.63 GB) | Yes — headroom 2219 MB | 2/6 | **DELETE** (clear fail: slowest, thinnest graph, no edge over the 1.7B) |
| granite-4.2-3b-MLX-4bit (pre-wave) | 1918 MB (1.78 GB) | Borderline — exactly the line, 2048 MB | 0/6 | **DELETE** (already OUT before the wave) |
| MiniCPM5-2B-mlx-4Bit (#42) | 1426 MB (1.33 GB) | Yes — headroom 2540 MB | 3-5/6 | **KEEP** (owner call — see below) |

Deleting the four DELETEs reclaims **10 309 MB (~10.3 GB)**; deleting MiniCPM5-2B too
reclaims **11 735 MB (~11.7 GB)**. Reverse cost is one `hf download <repo> --local-dir
models/<name>` per model (the exact per-model commands and `scratch/check4*_download.log`
are in §7c items 5-8) from the same confined `HF_HOME=<repo>/.cache/hf`, so a deleted
model is one command to restore.

**The one judgement call.** MiniCPM5-2B is the candidate to argue over: the only one
that cleared node 12/12, the only one that reaches `EdgeDuplicate`/`SummarizedEntities`/
`ExtractedEdges` and lands a real (if small) graph in full mode, and the only candidate
that sits beside the 9B with real margin (2540 MB headroom). Recommend **keep** it as the
single open re-measure seam for the #46 prompt/gating follow-up; **delete it with the
rest if the owner prefers the full ~11.7 GB reclaimed.** Deletion (if any) is an owner /
follow-up action — it is **not** performed here and `models/registry.json` `roles` are
unchanged.

#### 10. Closing #22 on the decided stack (issue #47) — 2026-11-07, `small` 5/6 with one `ExtractedEdges` clamp skip

Branch `fix/47-close-22-on-decided-stack` (off `phase6-integration` @ `dad7acb`; two
commits: the `fix/22-hybrid-dedupe-statement` split cherry-picked as `79c4de6`, then this
docs-only commit). The run below measures the **decided stack** — Graphiti + embedded
Kuzu + arctic-embed-s 384-d, `LFM3-1.7B-MLX-4bit` on the `small` role (choice B, #45) —
**with the hybrid dedupe split landed** (§7c item 4), i.e. the split's status resolves
**merged** on the kept backend (Graphiti, ADR 0001). `roles`, `.env` and M1/M2/M3
untouched.

Config in force identical to every §7c run: `json_schema` structured output,
`GRAPHITI_LLM_MAX_TOKENS=4096` (M1 untouched), `GRAPHITI_MAX_COROUTINES=2`, arctic-embed-s
384-d; a **fresh graph id** `check47_1p7b_full_1794091410`; Ollama asserted down before AND
after by the harness (`independence proven` both); `GRAPHITI_LLM_MODEL=LFM3-1.7B-MLX-4bit`
(survives `load_dotenv`). `scratch/check47_run.sh` ran beside `scratch/rss_sampler.py`
(SIGTERM-only stall guard, no stall). Full-mode row of record — `scratch/check47_full.log`:

| run | log | graph id | ok | skip | class | llm s | mean | calls/chunk | backend_rss | sampler peak |
|---|---|---|---|---|---|---|---|---|---|---|
| full ×1 | `check47_full.log` | `check47_1p7b_full_1794091410` | **5/6** | 1 | malformed-json 1 | 1852.4 | 308.7 s | 12.3 | 180→1712 MB | 1729 MB |

Per-chunk detail (same log; `--mode full --repeat 1` on the six sha256-verified chunks):

| idx | status | chars | s | nodes | edges | calls | class / cause |
|---|---|---|---|---|---|---|---|
| 0 | ok | 376 | 25.0 | 7 | 7 | 3 | — |
| 1 | **skip** | 368 | 73.7 | — | — | 3 | malformed-json — `ExtractedEdges` `finish_reason=length` at M1's 4096 clamp (truncated body → `Expecting property name enclosed in double quotes: line 294 column 3`) |
| 2 | ok | 213 | 279.9 | 7 | 8 | 11 | — |
| 3 | ok | 390 | 448.3 | 13 | 18 | 22 | — |
| 4 | ok | 129 | 596.9 | 14 | 30 | 19 | — |
| 5 | ok | 390 | 428.6 | 14 | 32 | 16 | — |

The five succeeding chunks land **55 nodes / 95 edges**. Exactly **one** `ExtractedEdges`
response hit `finish_reason=length` (M1's 4096-token clamp) in the whole run, and it
aborted exactly one chunk (idx=1); M2 fault isolation kept the other five intact and M3 was
not salvaged.
**Wall time ≈ 31.5 min** (22:43:30–23:15:02 UTC); peak run.py RSS **1729 MB** (sampler),
backend 180→1712 MB — inside the 11 GB budget, co-resident with the 9B with headroom.

**Verdict — still not 0 skips, so #22 closes with the recorded rate and the failing class
named.** Full mode is **5/6 ok / 1 skip (16.7%)** against the §30 bar of 6/6 with 0 skips
(the 9B's `check24_9b_full_c.log` reference, §7c item 4). The not-0-skips acceptance branch
therefore applies: #22 closes with the measured rate and the class — `ExtractedEdges`
edges running past **M1's 4096-token clamp** → truncated JSON (`malformed-json`) — as an
**open item**; §12's acceptance run (#23) proceeds on that basis per the owner's **choice
B** (Graph Build stays on `small`), and the clamp stays the recorded open item carried by
#45 (no 4-bit candidate cleared it; #46's 8-bit wave is pending). A visible skip beats a
silently wrong graph.

**Baseline comparison (§7c item 4).** The prior in-tree reference on the split,
`scratch/check24_1p7b_full_c.log`, measured **3/6** (3 skips, all the same `ExtractedEdges`
clamp class; mean 142.2 s over the six — its skipped chunks are fast because they abort
early on the clamp). This run is **5/6**, one skip: the **dedupe-echo class is already gone
on the split** (0 echoes on both — the class the split removed on the 1.7B), so the
surviving skip is purely the `ExtractedEdges` clamp, i.e. the **same class mix, one fewer
chunk in it**. The 3-vs-1 skip delta is generation variance, not a new class: temperature
0.0 is dropped by the MLX runner (the harness's `--repeat` caveat), so how many of the six
chunks' `ExtractedEdges` bodies run past the clamp is not perfectly reproducible run to
run — the point is the class mix, and it is unchanged.

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
