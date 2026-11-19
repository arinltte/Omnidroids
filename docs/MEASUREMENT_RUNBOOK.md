# Measurement runbook

**Read this before any measurement** (chunk size, a model/quant swap, the extraction
budget, an evidence run). It is the conventions `HANDOFF.md` §2 + §9 B/C already hold —
every rule here was paid for once. The gates and the hard constraints live in
`HANDOFF.md` §2 / §7; this file is how to *measure* against them.

## -1. If the run is a quality comparison, read the gold standard first

A run is **not** comparable on node counts, `ok/skip`, retries or wall time. Before you
report any arm as "better": read `docs/GOLD_STANDARD.md`, run
`scratch/gold_score.py --check` (the gold must still be grounded in its slice), then score the
arm's graph with `gold_dump_graph.py` + `gold_score.py --score`. The frozen 8-bit baseline and
the metric definitions are in `docs/GOLD_STANDARD.md`; the next session's full brief is
`HANDOFF.md` §9 G.1.1.

## 0. Pre-flight, every run

```bash
pgrep ollama                      # must be EMPTY before the run — assert again after
ps -eo pid,ppid,etime,command | grep python   # no idle python3.11 from an earlier run
git status --porcelain            # clean tree, one writer
```

- Work from the **main tree's `backend/`**. Worktrees lack `backend/.venv`, `models/`,
  `.env` and the harness-log layout — do not create them there.
- All harness logs tee to the **main tree's git-ignored `scratch/`** (`scr/<triple>.log`,
  `scr/<triple>.json`, `scr/<triple>_backend.log`).
- Stop the backend with **SIGTERM only** (Kuzu WAL → SIGBUS on `kill -9`). No
  `python3.11` sits idle after a run.

## 1. The two shell traps (the ones that silently waste a session)

**Trap 1 — `.env` eats the shell.** `app/config.py` loads the repo-root `.env` with
`load_dotenv(override=True)`. Every key `.env` *defines* clobbers a same-named shell
variable; a shell value survives **only for keys `.env` does not define**. `.env`
currently defines `LLM_MODEL_NAME`, `LLM_API_KEY`, `LLM_BASE_URL`, `GRAPHITI_LLM_MAX_TOKENS`,
`LLM_REASONING_EFFORT`, … — so a shell `GRAPHITI_LLM_MAX_TOKENS=8192` does **nothing**
and a shell `LLM_MODEL_NAME=…` does **nothing**. That is why the model override for a
harness run is `GRAPHITI_LLM_MODEL=<name>` (absent from `.env`, so it survives) and
**never** a shell `LLM_MODEL_NAME`.

**Trap 2 — the M1 clamp moves in-process only.** `GRAPHITI_LLM_MAX_TOKENS` is in `.env`
(4096), so the clamp cannot be raised from the shell at all. The one worked precedent is
the measurement-only in-process override `scratch/chunk_slice_measure.py --budget`. Do
not touch M1 (`app/utils/graphiti_zep.py`) and do not edit `.env` for an experiment —
`.env` is the owner's git-ignored file.

## 2. The graph-build measurement seam (§9 B/C's harnesses)

For chunk-size, model, quantisation and budget questions the seam is the real pipeline,
not a re-implementation:

| Harness (`scratch/`, git-ignored) | Job |
|---|---|
| `chunk_size_probe.py` | Offline: rebuilds the acceptance document's text through the production parser and **byte-verifies the `chunk_size` chunking (sha256-for-sha256) against the real run's batch record** (`backend/data/graphiti/<graph_id>/batches/*.json`), then cuts the slice. Run it first: it proves the slice *is* the text the baseline measured. |
| `chunk_slice_measure.py` | The real seam on that slice: drives `app.utils.graphiti_zep._build_llm_client()` (M1's per-call clamp live), the Flask `/v1` proxy of a real `run.py` child (no direct runner call), `add_episode` with `_ingest_record`'s exact arguments over embedded Kuzu + the production embedder, and **the baseline run's own ontology** (`…/ontology.json`, 10 entities / 9 edges — not a 3-type fixture). `--budget N` is the in-process clamp override (Trap 2). One row per LLM call in its JSON summary. |
| `chunk_slice_run.sh` | The runner: Ollama assertion, RSS sampler, stall guard, SIGTERM only. **Trap: it never passes `--ontology`** — the harness silently uses its default `scratch/chunk_size_ontology.json`, which `chunk_size_probe.py` *overwrites* from the baseline run. Before any ontology A/B, copy the ontology under test over that path (and back the old one up), or the arm runs the wrong ontology. |
| `register_model.py` | Register a locally downloaded model the way `downloader.py` does. **No role assignment until a decision ticket.** |
| `check_small_role.py` / `verify_small_role_serving.py` | The role-swap proofs (now retired for the guard class by `backend/tests/test_model_registry_roles.py`). |

Fresh **graph id per run**: `check<n>_<slug>_<epoch>` (e.g. `chunk1500_1p7b_1794212039`).
Evidence lives in the `.log` + `.json` + `_backend.log` triple — cite the triple, not a
remembered number.

## 2.1 The recovery path — what the seam does with a malformed body

**Read this before touching the extraction seam.** Since 2026-11-11 the seam recovers the
model's own finished elements from an unparseable body instead of discarding the whole chunk
(`_salvage_truncated_json` in `backend/app/utils/graphiti_zep.py`, driven from
`_CappedLLMClient._generate_response`). The full record is `docs/GOLD_STANDARD.md` §5; this
section is the *contract a modifier must keep*.

**The policy, by failure class** (one variable per change — never widen a class silently):

| class (`finish_reason`) | pre-recovery behaviour | recovery now |
|---|---|---|
| `"length"` or absent (certified clamp) | terminal `_TruncatedJSONError`, chunk skipped | longest complete element slice; **empty answer allowed** — the chunk keeps its other calls |
| `"stop"` (generation defect) | propagates for tenacity's retry | longest complete slice; **empty NOT allowed** — a defect with nothing complete still propagates (retry preserved) |
| anything else | propagates | **unchanged** — the salvage never sees it |
| schema-echo shape (any class) | terminal `_SchemaEchoError` | still terminal — the salvage's payload is re-checked by `_looks_like_schema_echo` before use |

**The invariants** (pinned by `backend/tests/test_graphiti_truncation_salvage.py` — 21 tests;
the suite is the gate):

1. **Nothing is invented.** Every candidate slice is validated *by parsing*; the scanner alone
   is not trusted (a defect body's later text can quote-balance out and fool it).
2. **Corrupt early elements are dropped, never repaired.** When no from-element-0 slice
   parses, the salvage retries from later element boundaries and returns `dropped_early` —
   the payload and the log line both carry the count, so a run's report cannot hide a loss.
3. **Only the single-array shape is rebuilt** (`{"<key>": [<elements>]}`) — a second top-level
   array, no root object, or no array value recovers nothing.
4. **The return contract is a 5-tuple** `(payload, kept, cut, empty, dropped_early)`; changing
   it is a two-callsite change (truncation + defect branches).

**Extending it** (the recipe that worked for each added class): write the failing-body test
first (the verbatim recorded shape — the malformed bodies are recoverable from a run's
`per_call` rows and the `_backend.log`'s `graphiti LLM response:` lines), watch it fail, widen
the scan, keep 1–4 green, then measure on the frozen slice before claiming anything.

**Grepping a run for recovery events** (the `_run.log` tees the backend log's lines):

- `salvaged-prefix` — a certified clamp truncation was recovered (`kept_elements`, `empty`,
  `dropped_early` on the line).
- `salvaged-defect` — a `stop` defect was recovered.
- Still terminal: `truncated/unparseable JSON response (not retried)`.

**Traps that cost time here** (paid once each):

- A **`ProjectStatus`** value that does not exist in the enum breaks `GET`/`POST` on *every*
  project route with a 500 (`Project.from_dict` validates on load). Statuses are exactly
  `created` / `ontology_generated` / `graph_building` / `graph_completed` / `failed` — never
  invent one, and validate after any direct edit to a stored `project.json`.
- The scorer's own gate is manual: `gold_score.py --check` must pass **before** `--score` on a
  freshly edited gold (§4 of `docs/GOLD_STANDARD.md`). A self-gating `--score` and a
  `--fold-queue` adjudication mode are the recorded gaps (tracker #56).
- The per-call log line caps the body at 400 chars (`%.400s`), so a late failure looks healthy
  in the log; read the failing body from the harness JSON's `per_call` rows (`body_head`/
  `body_tail`), not from the log.

## 2.2 Driving a full document build without the browser

For future full-document tests (the 53-chunk acceptance build takes ~2.3 h at the measured
rate), the production path is drivable over HTTP — no UI clicks needed:

```bash
# 1. launcher (or run.py from backend/); wait for the health route
curl -s http://localhost:5001/health        # {"status":"ok",…}
# 2. claim the build for a project whose status is a valid, buildable one
curl -s -X POST http://localhost:5001/api/graph/build \
    -H "Content-Type: application/json" \
    -d '{"project_id": "proj_xxxx", "chunk_size": 500, "chunk_overlap": 50}'
#    → {"task_id": …}; poll it:
curl -s http://localhost:5001/api/graph/task/<task_id>
# 3. the authoritative progress split is the batch record, not the progress line
ls -t backend/data/graphiti/<graph_id>/batches/*.json   # items[].status: succeeded/skipped/pending
# 4. stop with SIGTERM (Ctrl+C on the launcher) — never kill -9 (Kuzu WAL → SIGBUS)
```

The measured full-build result (2026-11-11, task `a029c3a1` on the acceptance document):
**49/53 succeeded, 4 skipped (7.5 %), 410 nodes / 736 edges, guard passed with 5× margin** —
the pre-recovery rebuilds tripped it. Project-specific settings live in
`backend/uploads/projects/<id>/project.json` (the ontology the extractor actually uses); back
the file up before editing it, and keep the status a valid enum value.

## 3. Reading a run (the traps that lie)

- **Which model served**: the backend log's `mlx <model> generate: prompt_tokens=…
  max_tokens=…` line. Never the config line, never the harness's `model=` line (it
  reports the config value, not the loaded model).
- **The UI's progress line counts ok + skipped.** `9/53 完成` can be a 30 % yield. The
  split (`done` / `skipped` / `skipped_indices` / `items[].status` + `.error`) is only in
  the batch record `backend/data/graphiti/<graph_id>/batches/*.json`. Diagnose from the
  record first.
- **Call bodies span lines.** `graphiti LLM response:` records are pretty-printed JSON;
  `grep … | cut -c1-320` renders a full-budget call as `body={` and reads as empty. Read
  bodies from the harness's JSON summary (`per_call`), and expect one record to span
  many lines when grepping any log.
- **Truncation is the stop condition.** Edge calls hitting `finish_reason=length` at the
  clamp: the measured stop line is **~20 %** (§9 B). Peak `run.py` RSS comes from the
  runner's sampler.

## 4. Shape knobs that dominate cost (ranked by measured leverage)

1. **The junk entities** — every junk entity multiplies into edges and dedupe calls. The
   magnet is `entity_type_id 0`, the ontology's 兜底 fallback. One-line ontology change
   with a measured multiplier (25 LLM calls for 2 landed chunks at 8-bit, 16 of them
   per-edge dedupe).
2. **The extraction budget** — `GRAPHITI_LLM_MAX_TOKENS`, one knob, no code change, but
   in-process only (Trap 2). Measured price of more budget: ~96 s per full-budget edge
   call at 8-bit decode speed — pays only after (1).
3. **The prompt scaffolding** — an edge prompt is ~4 200–5 650 tokens for ~1 000 content
   tokens (read the `prompt_tokens=` lines). Trimming shrinks every call; unmeasured.
4. **Chunk size** — `Config.DEFAULT_CHUNK_SIZE` (`app/config.py`), the per-project
   parameter (`app/models/project.py`) and the API's documented default
   (`app/api/graph.py`) move together, pinned by `backend/tests/test_chunk_size_default.py`.
   **500 is the measured default; 1500 was measured and rejected** (1 ok / 3 skipped,
   40 % edge-call truncation, above the ~20 % line) — a session cannot re-raise it
   without a new measurement. Re-measure after 1+2 land.
5. **Then the 9B on steps 1–3** — the owner's call, needs a per-chunk cost measurement
   first.

## 5. Gates (before any merge) and hygiene

```bash
cd backend
.venv/bin/python -m compileall -q app run.py scripts
.venv/bin/python -m pytest tests/ -q
```

- **Never pipe a gate through `head`/`tail` and read `$?`** — `$?` is the pipe's. Check
  `${PIPESTATUS[0]}` or tail a log file.
- The suite leaks daemon threads (module-singleton resets), so `pytest -q` can end with a
  `ValueError: I/O operation on closed file` traceback **after** a green summary. Read the
  summary line, not the tail.
- `timeout` does not exist in this shell (macOS, no coreutils).
- Downloads: `hf download <repo> --local-dir ../models/<name>` **from `backend/`** (the
  literal `models/` creates a non-resolvable `backend/models/`). Register in the
  git-ignored registry with **no role assignment** until a decision ticket.
- Stamp every measured number with the SHA it was measured on — the tree moves under a
  session (`gh issue list` + `git log main..HEAD` are the source of truth).
