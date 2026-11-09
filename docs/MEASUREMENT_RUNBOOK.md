# Measurement runbook

**Read this before any measurement** (chunk size, a model/quant swap, the extraction
budget, an evidence run). It is the conventions `HANDOFF.md` §2 + §9 B/C already hold —
every rule here was paid for once. The gates and the hard constraints live in
`HANDOFF.md` §2 / §7; this file is how to *measure* against them.

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
| `chunk_slice_run.sh` | The runner: Ollama assertion, RSS sampler, stall guard, SIGTERM only. |
| `register_model.py` | Register a locally downloaded model the way `downloader.py` does. **No role assignment until a decision ticket.** |
| `check_small_role.py` / `verify_small_role_serving.py` | The role-swap proofs (now retired for the guard class by `backend/tests/test_model_registry_roles.py`). |

Fresh **graph id per run**: `check<n>_<slug>_<epoch>` (e.g. `chunk1500_1p7b_1794212039`).
Evidence lives in the `.log` + `.json` + `_backend.log` triple — cite the triple, not a
remembered number.

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
