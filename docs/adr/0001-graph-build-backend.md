# ADR 0001 — Graph Build backend: keep Graphiti + embedded Kuzu

- **Status:** Accepted (2026-11-08, ticket #39, spec #30, parent #20)
- **Evidence base:** `docs/GRAPH_BAKEOFF.md` §6 / §6b / §6c — three candidates measured on one identical sha256-recorded document with one shared local small model (`LFM3-1.7B-MLX-4bit`, registry `small`) and one shared local embedder (`Snowflake/snowflake-arctic-embed-s`). Every figure below cites its run log under `scratch/bakeoff/` (git-ignored).

## Status

Accepted. The bake-off (#36 → #37 → #38) is complete; #39 records the decision.
Superseded-by: none yet. Revisit trigger: #45 (the small-model decision) lands a
model that still hits the Graphiti extraction clamp, or no #45 candidate clears
the bar — see Consequences.

## Context

The owner's question, verbatim: *"will changing from graphiti to cognee or
semantica improve the speed of the graph build?"* Step 1 (Graph Build) today runs
Graphiti + embedded Kuzu + the local `small` model behind the Zep-compatible seam
`backend/app/utils/graphiti_zep.py` (`client.graph.*` / `client.batch.*`), with
reliability machinery M1/M2/M3 (`_CappedLLMClient` 4096-token leaf clamp,
`_TruncatedJSONError` fail-fast, `_ingest_record` per-chunk skip + batch-succeeded
terminator) that must not be relaxed.

Measured on the identical document (8 sha256-verified chunks, 3 375 chars;
`scratch/bakeoff/ident_doc_extract.chunks.json`), run of record per candidate:

| | Graphiti (`baseline_graphiti_1794046080`) | Cognee (`cognee_1794056509`) | Semantica (`semantica_1794069690`) |
|---|---|---|---|
| wall time | 1 032.4 s | 229.9 s | 651.7 s |
| usable graph? | **no** — 2/8 chunks, 75 % skip; service gate rejects the batch (limit 30 %) | yes — 8/8 chunks | yes — 8/8 chunks |
| chunks verbatim | 2/8 | 8/8 | 8/8 |
| skip rate | 75 % (6× `malformed-json` — M3 `_TruncatedJSONError` after M1's 4096-token clamp) | 0 % | 0 % |
| graph | 40 nodes / 47 edges (from 2 chunks) | 105 nodes / 222 edges | 118 nodes / 47 edges |
| 5-query relevance | 1 relevant / 1 partial / 3 irrelevant (only chunks 0, 3 in graph) | 4 relevant / 1 partial | 5 relevant |
| query latency | 0.03–0.05 s | **22–31 s** | 0.21–0.25 s |
| JSON-with-local-LM | 0 parse failures / 6 truncation fail-fasts | run 2: 0 terminal; run 1: terminal `ValidationError` → all-or-nothing | 7 recovered parse failures / 0 terminal |
| crash-resume | **not measured** — design evidence only (`_ensure_ingestion` re-enters a non-terminal batch; harness has no `--resume-batch`) | measured: no resume entry point — interrupted run stays non-terminal, re-`cognify()` redoes it | measured: no resume entry point — nothing of its own survives except the caller's `graph.json` |
| peak RSS | 2 853.5 MB | **2 900.6 MB** (highest) | 2 401.8 MB (lowest) |
| integration | ships — 0 | full swap (isolated venv, litellm, per-run env file, ladybug + lancedb stores, new adapter seam) | full swap (isolated venv, torch 5 stack, embedder-swap workaround, new adapter seam) |

Retrieval-semantics caveats that decide it: Cognee's 22–31 s per query is its own
LLM completion inside every search, and its answers come from a graph+chunk mix;
Semantica's 5/5 is **chunk recall** — its graph-search leg returned 0 facts on all
five queries (`ContextGraph.query` is a whitespace-token substring match and these
queries are Chinese), and its vector ranking was weakly discriminative even at 8
chunks (top score 0.96–0.99, the next five within 0.92–0.96).

## Decision

**The Graph Build backend stays Graphiti + embedded Kuzu, served unchanged behind
the existing Zep-compatible seam `backend/app/utils/graphiti_zep.py`
(`client.graph.*` / `client.batch.*`).**

The answer to the owner's question is **no**: swapping does not improve the
graph build in the way that matters. Cognee's build is 4.5× faster (229.9 s vs
1 032.4 s) but it buys that by moving the cost into every query (22–31 s vs
0.03–0.05 s) and its ingest is all-or-nothing with no resume entry point;
Semantica's 5/5 answers do not use its graph at all. The incumbent's only
measured defect — 6/8 chunks skipped by the M3 truncation fail-fast — is the
outgoing 1.7B small model's JSON-clamp class, and #45 replaces that model by
construction (its bar is 6/6 full mode with 0 skips on the decided backend; the
9B already measured 6/6 with the dedupe split, per #30). A backend swap is the
irreversible move; the model swap is one ticket away and already scheduled.

## Consequences

- **No swap lands in #39 or before #40.** Nothing in `app/` / `backend/` changes
  on this decision; the seam stays the shipped default and #40 (now unblocked)
  verifies exactly that and closes on the evidence — no swap wiring.
- **The losing trees stay on disk.** `Prediction/cognee` and `Prediction/semantica`
  are kept as the measured comparison set so the bake-off stays re-runnable
  (`docs/GRAPH_BAKEOFF.md` §10 Reproduce). HANDOFF's delete rule for those trees
  applies only on owner cancellation of the bake-off, which this decision is not.
- **Step 1 stays blocked on the model, not the backend.** With `LFM3-1.7B-MLX-4bit`
  the measured skip rate is 75 % and the service gate rejects the batch
  (`baseline_graphiti_1794046080.json`). #45 picks the small model on this decided
  backend's extraction path (4-bit candidates first, bar 6/6 with 0 skips).
- **The extraction-prompt split resolves with this decision:** Graphiti wins, so
  `fix/22-hybrid-dedupe-statement` (the plain-English dedupe schema that took the
  9B to 6/6) lives — it does not die with a swap, per #30.
- **Revisit trigger.** If no #45 candidate clears the bar, #45 records the gap and
  this ADR is superseded by a re-run of the bake-off with the new model — the
  comparison numbers in §8 of the bake-off doc are the baseline that re-run cites.
  A swap, if it ever lands, must sit behind `client.graph.*` / `client.batch.*`
  (bake-off protocol §3, rule 10) — no backend SDK may leak into services.
- **Known gap accepted:** Graphiti crash-resume on the identical document is
  still unmeasured (design evidence only). It is not load-bearing for this
  decision because both candidates were measured to have *no* resume entry point
  at all — but it is a recorded gap, not a measured strength.

## Alternatives considered

**Swap to Cognee — rejected.** Its win is real: 8/8 chunks verbatim, 105 nodes /
222 edges, 229.9 s vs the baseline's rejected 1 032.4 s (`cognee_1794056509.json`).
Rejected because: (1) every one of the five queries costs 22–31 s — Cognee runs
its own LLM completion inside each search — against the incumbent's precomputed
edges at 0.03–0.05 s, and steps 3–5 are query-heavy; a report-generation pass
making dozens of queries spends the build-time saving in two queries. (2) Ingest
is all-or-nothing: run 1 died on a single malformed-JSON chunk
(`ValidationError: Invalid JSON: trailing comma at line 131`, 533.8 s, 0 entities)
— no per-chunk skip semantics, no M2-style terminator. (3) No resume entry point:
a re-`cognify()` redoes the extraction and abandoned runs reconcile only after
3 600 s. (4) Highest peak RSS of the three (2 900.6 MB, backend child 2 126.4 MB)
on a 16 GB Mac that co-residents the 9B for steps 4–5. (5) The 1.7B's noise is
visible in its graph (junk entities `acme`/`alice`/`58%`, section-heading entities,
105 connected components). (6) Full #40-sized swap: isolated venv, litellm routing,
a per-run `COGNEE_ENV_FILE` to dodge this repo's own `.env`, dual stores (ladybug +
lancedb) and a new adapter seam.

**Swap to Semantica — rejected.** Its 5/5 query relevance is chunk recall, not
graph reasoning: the graph leg returned 0 facts on all five queries and the vector
ranking was weakly discriminative even at 8 chunks (0.96–0.99 top, then 0.92–0.96)
— it degrades further on the 53-chunk document. It also has the highest per-chunk
cost (3–4 LLM calls/chunk, 651.7 s for 3 375 chars), the noisiest graph (18 of 43
distinct edge types are invented `http://example.org/...` URIs, 19 of 47 edges),
no resume entry point at all (only the harness's own `graph.json` survived the
SIGTERM test), the least mature tree (0.8.0, torch 5.22 / transformers 5.22
stack, and a mandatory embedder-swap workaround because its default embedder
cannot load offline), and the same full swap cost. A backend whose retrieval does
not read its own graph answers the owner's question with a faster build and a
fake win.
