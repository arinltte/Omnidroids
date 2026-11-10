# Local Graphiti Extraction — Root-Cause Investigation

**Status:** investigation + one safe production fix + regression tests (see §D, §E).
**Scope:** why Graphiti-on-local-LLM extraction fails / is sparse, and what to do about it.
**Posture:** the goal is *semantically correct, complete knowledge-graph extraction with reliable local ingestion* — not valid JSON, not fewer retries, not faster wall time.

This document distinguishes **CONFIRMED** (read from our code, our measured logs, or upstream primary sources) from **HYPOTHESIS** (needs a measured experiment). Every measurement records provenance; experiments not yet run are marked and their plan preserved (§G).

> **Next session — start here.** Option 6 was executed (see §F, the bottom line, and `HANDOFF.md` §9 G). The durable code is already in place (`graphiti_zep` schema-echo detection; guard `.env` at 0.40). **The gold standard that gated the quality arms is now built:** `docs/GOLD_STANDARD.md` + the frozen `docs/gold_abfix_slice8.json` (7 gold / 6 borderline / 144 junk entities, 5 gold edges), scored by `scratch/gold_score.py` over a read-only dump of a frozen run's Kuzu graph; the first numbers are in `HANDOFF.md` §9 G.1. What remains, one variable at a time: Exp-3 JSON-Schema normalization, Exp-5 drop the `Person` catch-all (mind the `chunk_slice_run.sh` `--ontology` trap), Exp-6 `GRAPHITI_MAX_COROUTINES=2`, Exp-7 chunk 1500 — each ranked against the gold, after its owner sign-off and the independent second-annotation pass. Do **not** re-run the already-falsified description/budget/9B arms, and do **not** equate valid-JSON or fewer retries with quality.

---

## 0. Bottom line

1. **The 9B experiment failed because of a model capability/instruction-following gap, not because of `structured_output_mode`, not JSON-Schema complexity, and not endpoint matching.** Keeping the 8-bit control (LFM3-1.7B-MLX-8bit) as the reference is correct; do not adopt Ornith-1.5-9B-MLX-6bit.
2. **The dominant failure class in the local seam is prompt behavior, not interface behavior.** In our production seam the OpenAI `response_format` is *stripped* by our `/v1` proxy and neither backend (MLX, llama.cpp) performs grammar-constrained JSON-Schema decoding. So the model is effectively **always in a `json_object`-equivalent regime**: it sees the injected schema + a strict-JSON preamble, and generation is free-form. **Switching `structured_output_mode` between `json_schema` and `json_object` cannot change what the model sees** (both are collapsed by the proxy), and our 9B run — already in the default `json_schema` mode — *still* echoed the schema. This falsifies the popular "#868/#1920 → just use json_schema" fix for our seam.
3. **The 9B's schema-echo skips were mislabeled "wrong-keys," hiding the real class.** We added a tested detector (`_looks_like_schema_echo`) + a terminal `_SchemaEchoError` so a schema echo is reported as such and fails fast instead of burning retries (**the one safe production change**).
4. **#1909's "grammar drops optional temporal fields" and "exact-match endpoint drops edges" do not manifest in our seam/version:** the proxy strips the schema (no grammar), Pydantic defaults `None` (no validation loss), and graphiti-core 0.31.2 **materializes dangling endpoint nodes** instead of dropping edges (a PR #1964 behavior that landed *after* #1909's 0.30.1 report). PR #1910 (the proposed recursive `required` transform) is **closed unmerged**.
5. **Sparse graphs in the confirmed data are caused by the model emitting too little**, not by downstream rejection. 9B produced ~2.2 nodes/successful-chunk vs the 8-bit's ~14.7 — a ~6.7× extraction yield gap that persists *even on chunks that succeeded*.

---

## A. Root-cause report

### A.1 The actual structured-output path (CONFIRMED — read from code)

Chain for every Graphiti extraction/dedupe call:

```
graphiti generate_response()                       [OpenAIGenericClient, 0.31.2]
  ├─ messages[0] += language instruction
  ├─ (json_object mode only) messages[-1] += "Respond with a JSON object in\n\n{json.dumps(model_json_schema())}"
  └─ _generate_response_with_retry()  →  _CappedLLMClient._generate_response()  [OUR SEAM]
        ├─ max_tokens clamped to GRAPHITI_LLM_MAX_TOKENS (4096)
        ├─ _append_schema_reminder(messages, response_model)      # ALWAYS, both modes
        ├─ response_format = _build_response_format(response_model)
        │      # json_schema (default) → {'type':'json_schema','json_schema':{schema: model_json_schema()}}  (has $defs/$ref)
        └─ chat.completions.create(...)  →  POST /v1/chat/completions  [OUR PROXY]
              └─ manager.generate_sync(): response_format.type ∈ {json_object,json_schema}
                    ⇒ json_mode=True → PREPEND "You are a strict JSON API…" to prompt,
                      eff_response_format = None   # response_format STRIPPED
                    → runner.generate()              # MLX / llama.cpp — NO grammar decoding
                    → _repair_json(content)          # extract first JSON value, re-serialize
        └─ json.loads(_strip_code_fences(result)) → [schema-echo check] → _normalize_to_schema()
```

Confirmed facts:
- **Version is graphiti-core 0.31.2** (`.venv …/graphiti_core-0.31.2.dist-info`; `pyproject.toml` pins `graphiti-core==0.31.2`, `kuzu==0.11.3`). Not the 0.30.1 cited by #1909.
- **`response_format` never constrains anything.** `manager.generate_sync` (`app/models/manager.py:364-401`) sets `eff_response_format = None` for both JSON modes and adds a prompt-level strict-JSON line; `_JSON_MODE_TYPES = {"json_object","json_schema"}`.
- **No grammar decoding anywhere.** The MLX runner `generate()` (`app/models/runtime.py:337+`) accepts `response_format` but never uses it (plain `mlx_vlm` sampling); the GGUF/llama.cpp runner explicitly comments "response_format is intentionally NOT forwarded for gguf — the router applies prompt-level strict JSON + a repair parser."
- **The schema reaches the model only as prompt text.** `_append_schema_reminder` (`graphiti_zep.py:~660-712`) appends `"Respond with a JSON object in the following format:\n\n" + json.dumps(model_json_schema())` (+ element/shape instructions) to `messages[-1]`, in **both** modes (in `json_object` it detects Graphiti's own appended block and appends only the tail). `model_json_schema()` carries `$defs`/`$ref`.
- Confidence anchor: **the captured 9B echo bodies ARE `model_json_schema()` output** — `{"$defs":{"Edge":{"properties":{"source_entity_name":…}}}}` (`scratch/chunkslice_slice9b_1794295934.json`, `per_call` seq 8/14/24), byte-for-byte the `$defs` form of the injected schema.

**Consequence:** for our seam, `json_object` vs `json_schema` is a *distinction without a difference*. The README's `json_object` "can be more reliable" guidance and #868's "switch to json_schema" both presuppose a backend that actually honors `response_format`; ours does not.

### A.2 Measured failure-mode split, 8-bit vs 9B (CONFIRMED — measured)

Identical slice (`scratch/abfix_slice8.json`), chunk_size 500 / overlap 50, budget 4096, same production ontology, same seam, Ollama closed (local MLX):

| | 8-bit control | 9B |
|---|---:|---:|
| Model (verified from serving log) | LFM3-1.7B-MLX-8bit | Ornith-1.5-9B-MLX-6bit |
| ok / skip | 6 / 2 | 5 / 3 |
| Skip class | **malformed-json** (2) | **schema-echo** (3) |
| Nodes / edges (ok chunks) | 88 / 54 | 11 / 7 |
| Nodes per ok chunk | **14.7** | **2.2** |
| Edges per ok chunk | **9.0** | **1.4** |
| Calls / retries / wall | 92 / 47 / 1058 s | 34 / 6 / 532 s |

- 9B model identity verified independently: `scratch/9bserve_backend_1794296853.log` shows `MODEL LOADED Ornith-1.5-9B-MLX-6bit [mlx] est=8004MB`. The low RSS (~1143–2943 MB) is MLX memory-mapping, **not** evidence a smaller model loaded — the `model loading` line is authoritative.
- The 9B's problem is **not only** skip count: even its 5 "successful" chunks yielded ~6.7× fewer nodes. The 8-bit fails on JSON *formatting* (unescaped quotes / `finish_reason=length` truncation) but does the *actual extraction*.

### A.3 Cause-by-cause classification

| Cause | Verdict | Evidence |
|---|---|---|
| Structured-output **mode** (json_object vs json_schema) | **Ruled out** as the lever | Proxy strips `response_format` in both modes; no grammar; 9B echoed in default `json_schema`. §A.1 |
| **Local model capability / instruction-following** | **CONFIRMED — primary driver** | 9B echoes schema + yields 2.2 nodes/chunk; 8-bit emits real extraction but ~15% malformed. §A.2 |
| **JSON-Schema complexity** ($defs/$ref reaching backend) | **Ruled out as a decoding problem** (real as a *prompt* problem) | Schema never reaches a grammar backend. It *is* in the prompt; the fix is prompt-facing, not schema-facing. §A.1 |
| **Pydantic optionality** (#1909 temporal-drop) | **Ruled out as loss** in our seam | Schema stripped → no grammar "may-skip"; `valid_at/invalid_at` default `None` → validates. It is a *completeness* metric, subordinated by our own `_TIMESTAMP_CLAUSE` ("keep null unless the text states a date"). §A.4 |
| **Graphiti edge logic** (#1909 endpoint **drop**) | **Ruled out for 0.31.2** | 0.31.2 `materialize_dangling_endpoint_node` creates a node for unmatched endpoints; edges not dropped for name mismatch. §A.4 |
| **Endpoint matching** precision risk | **HYPOTHESIS (watch)** | Unmatched endpoints become *new* nodes — a precision risk (spurious nodes/edges), not a recall risk. Measure graph precision. |
| **Ontology `Person` catch-all** (H5) | **HYPOTHESIS — untested** | Production ontology leads with `Person`, overlapping Student/Professor/Journalist/Alumni/Organization; prior A/B shows entity *type selection* is the dominant magnet. §A.5 |
| **Chunking / concurrency** (H6) | **HYPOTHESIS — untested** | cs=500 and `GRAPHITI_MAX_COROUTINES=2` are defaults from the seam; not varied against quality. §G |

### A.4 Upstream provenance for #1909 (CONFIRMED)

- **PR #1910 is closed and NOT merged** (`merged=False`). The author (`v8eta`) closed it because "the CLA will not be signed," and pasted the full `_require_all_properties` helper into #1909 for maintainers to carry.
- **The endpoint-drop complaint is version-superseded.** `dangling_endpoints.py` (`materialize_dangling_endpoint_node`) landed via **PR #1964 (commit 2026-10-09), "Upstream extraction, dedup, and temporal fixes from the Zep fork,"** i.e. in the v0.31.x line — *after* #1909's 0.30.1 (2026-09-01). So the exact-match dropping #1909 describes does **not** occur in our 0.31.2; here a missing endpoint is materialized and, if no kept edge references it, pruned (`prune_unreferenced_materialized_nodes`).
- `_normalize_string_exact` (0.31.2) lowercases + collapses whitespace but **does not** strip quotes/trailing punctuation — the conservative-normalization gap #1909 raised. Moot for *loss* (dangling nodes), still relevant for *precision*.

### A.5 Why `Person` is a live hypothesis, not a finding (H5)

Prior measured A/B ("type selection is more influential than description wording") makes `Person`-as-catch-all a plausible magnet — but the previous description-tweak returned null, and removing `Person` may lose unclassifiable persons/organizations. Treat as an experiment with a precision+recall gold standard, not an automatic win. See §G / §F.

---

## B. GitHub issue applicability matrix

| Issue | Reported problem | Applies to 0.31.2? | Confirmed locally? | Recommended action |
|---|---|---|---|---|
| **#868** (Ollama schema echo) | Prompt-injected `model_json_schema()` (`$defs`/`$ref`) → model echoes `{"$defs":…}` → pydantic "Field required" | **Yes — mechanism matches** | **Yes** (9B run, chunks 2/4/6: `ExtractedEdges`/`SummarizedEntities` "edges/summaries Field required, input=`{'$defs':…}`") | Detect + relabel echo (done). **Not** a mode switch. Extend *prompt* mitigation (`_plain_schema_statement`) to echo-prone extraction response models — as a **measured experiment**. |
| #868 "fix": switch `json_schema` | @fabiosiqueira: `json_object` echoes injected schema; `json_schema` (Ollama 0.32.13 native constrained decoding) fixed it "across models" | **No** | **No** (9B ran default `json_schema`, still echoed) | Requires native `response_format` constrained decoding; our proxy strips it and MLX has no grammar. Do not pursue as our fix. |
| #868 workaround: flatten `$defs`/`$ref` (ilanb custom client) | Simplify schema before injection + defensive parsing | Partially (schema-is-in-prompt is real) | Not tested | Schema flattening alone won't stop a model from *copying* whatever schema block it sees; measure (Exp-3) before adopting. |
| **#1909** (grammar skips optional `valid_at`/`invalid_at`) | Grammar reads `required` as must-emit; optional keys omitted | Premise yes; **manifestation no** | **No** (our skips are wrong-keys/malformed-json, not temporal-drop) | No action for our seam. The transform matters only if we ever enable grammar decoding. Utility + tests added for safety (§E). |
| #1909 endpoint **drop** (vault vs vault data) | Edges lost to exact endpoint match | **Superseded** in 0.31.2 (materializes dangling nodes) | **No** (edges are not dropped) | No #1910-style adoption. Watch dangling-node *precision*. |
| **#1226** (fully-local MCP server) | (1) MCP ignores OpenAI-compatible `api_url`; (2) MCP always uses OpenAI reranker — no local reranker config *(note: the issue is LLM-endpoint + **reranker**, not embeddings as the brief summarized)* | **No** | **No** | Already local by construction: local sentence-transformers embeddings in-process, NoOp reranker, Kuzu. We do not run the MCP server. Nothing to port. |
| **#1920** (fully-local REST vs Ollama) | `json_object`-style schema echo; env vars only patch the LLM client, embedder/reranker stay OpenAI | Mechanism (echo) yes; architecture is a **proposal, not merged** | Echo yes (our seam injects only into our own LLM client; embedder/reranker are already local) | **None upstream — issue CLOSED by author**: "local models did not reach the extraction quality we needed, so we are not pursuing a fully local REST server." Corroborates our §C quality gap. |

**Dependency map (C/#1226 "fully local" check — CONFIRMED from code):** LLM = local MLX/GGUF behind our `/v1`; embeddings = local `SentenceTransformer` in-process (`_LocalSentenceTransformerEmbedder`); reranker = `_NoOpReranker`; graph store = embedded **Kuzu**; no Zep cloud key; no external network on the ingest path.

**Issue/PR states checked live (api.github.com):** #868 open (updated 2026-09-28), #1909 open (2026-09-23), #1226 open (0 comments), #1920 **closed** (2026-10-05), PR #1910 **closed, not merged**.

---

## C. Benchmark report (arms)

Only two measured arms exist for the frozen 8-chunk slice (both are the same budget/ontology/seam); the rest are preserved plans in §G. Quality is richer than runtime:

| Arm | ok/skip | Nodes | Edges | Nodes/ok-chunk | retries | wall | Quality read |
|---|---:|---:|---:|---:|---:|---:|---|
| **8-bit control** (LFM3-1.7B-MLX-8bit) | 6/2 | 88 | 54 | 14.7 | 47 | 1058 s | Does real extraction; fails ~15% on JSON formatting |
| **9B** (Ornith-1.5-9B-MLX-6bit) | 5/3 | 11 | 7 | 2.2 | 6 | 532 s | Echoes schema on 3 chunks; ~6.7× lower yield even when ok |

**Interpretation (not runtime):** the 9B is ~2× faster with ~87% fewer nodes/edges; its few retries come from doing far less, not from being more reliable. **Retain the 8-bit control.** No gold-standard precision/recall numbers exist yet — that is prerequisite work for any further arm (§G, §10).

---

## D. Recommended implementation

**D-1. Required fix (IMPLEMENTED, safe):** *Schema-echo detection + failure classification.*
- `graphiti_zep._looks_like_schema_echo(payload, response_model)` — conservative: fires only when **no** required top-level field is present **and** the payload looks like a schema document (`$defs`/`properties`/… markers). It never fires on a populated body, on a wrong-keys alias body, or on an edge that merely omits `valid_at` (that is #1909 completeness, pydantic-defaulted, not an echo). Validated: 9B = 3/3 echo-like, 8-bit = 0.
- `graphiti_zep._SchemaEchoError` — terminal, outside graphiti's tenacity retried set (companion to `_TruncatedJSONError`), so a reproducible echo fails fast instead of burning retries. This only **relabels an already-guaranteed chunk skip**; it never turns success into failure and never changes graph content.
- Leaf wiring logs `[schema-echo]` with response_model + body and raises `_SchemaEchoError`.
- Rationale for the confirmed root cause: echo skips were surfacing as generic "wrong-keys," hiding the class and inviting the wrong fix (a mode switch). This addresses reporting + fail-fast and is reversible.

**D-2. Optional improvements (NOT applied — need measurement):**
- Extend `_plain_schema_statement` (already used for `NodeResolutions`/`EdgeDuplicate`) to echo-prone **extraction** response models (`ExtractedEdges`/`SummarizedEntities`) for the 9B specifically — behind a per-model flag, measured against the Control+gold. Extending it to the 8-bit is contraindicated (measured: the raw dump is the extraction prompts' only compact exemplar; prose made 1.7B worse).
- A schema-echo *repair* (re-issue once with a plain-English statement). Higher risk; measure.

**D-3. Experimental ontology changes (NOT applied):** remove `Person` catch-all (§G, Exp-5). A product trade-off, measure precision+recall.

**D-4. Operational config (RECOMMENDED, not applied here):** guard calibration. Confirmed implementation: `graph_builder.SKIP_THRESHOLD` (default `0.3`, validated `[0,1]`), rejection is **strictly-greater** (`skipped/total > threshold`, with detailed `skipped_indices` reporting preserved). On the 8-chunk slice: 2/8=0.25 passes, 3/8=0.375 fails at 0.30; at **0.40** 3/8 passes while 4/8=0.50 still fails (still rejects genuinely broken runs). Suggested `.env`: `GRAPHITI_MAX_SKIPPED_FRACTION=0.40`. **This is guard calibration only — it does not improve extraction quality and must not be used to conceal schema failures.**

**D-5. Explicitly NOT changed:** `structured_output_mode` (already the effective default; switching is a no-op for the seam), upstream graphiti-core (0.31.2 is correct + pinned), the ontology, concurrency, chunk size — each is a gated experiment.

---

## E. Regression tests

`backend/tests/test_graphiti_schema_echo_and_transforms.py` (25 tests, offline, all passing; suite now 412 passed):

1. **Schema-echo detection** — the two recorded 9B echo payloads + full-schema/`$defs`/`properties` forms detected; populated bodies, wrong-keys alias bodies, `valid_at`-omitting edges, wrong inner keys, non-dict/empty/None-model **not** flagged.
2. **Schema echo fails fast** — a `finish_reason=stop` echo raises `_SchemaEchoError` (not `JSONDecodeError`, not `_TruncatedJSONError`); a populated body still flows through `_normalize_to_schema`; and the request captured proves echo is a **prompt** phenomenon (`response_format.type==json_schema` *and* `$defs` in the prompt) — pinning the root cause.
3. **JSON-Schema transformation** — `_require_all_properties` (quoted from #1909/PR#1910, an *analysis* utility deliberately **not** wired into the response path): every field at every level (incl. `$defs`), nullable types preserved, original schema not mutated, `additionalProperties`-only and a field literally named `properties` handled, response still validates.
4. **Required-field completeness** — `ExtractedEdges{edges}`, `ExtractedEntities{extracted_entities}`, `SummarizedEntities{summaries}`; `Edge` requires source/target/relation/fact and leaves `valid_at`/`invalid_at`/`episode_indices` optional (precise #1909 premise for 0.31.2).
5. **Endpoint normalization** — `_normalize_string_exact` lowercases/collapses whitespace, does **not** fold quotes/punctuation (`vault.`≠`vault`), never collides materially-different names (conservative safety); empty endpoint rejected.
6. **Skipped-fraction guard** — default 0.3, env override, invalid/out-of-range → 0.3; strictly-greater boundary (3/8 fails 0.30 but passes 0.40; 4/8 fails 0.40; equal-at-threshold accepted).

---

## F. Final recommendation

**Option 6 (combine), dominated by Option 1:** **Retain the 8-bit model; ship the schema-echo detection fix; calibrate the guard; do NOT change the structured-output path, and gate every other lever behind a measured, gold-scored experiment.**

- **Retain 8-bit; reject 9B.** Evidence §C: 9B is ~2× faster but ~6.7× less extractive and echoes; the quality/throughput trade is unacceptable. (Also independently corroborated: #1920's author abandoned fully-local Ollama because "local models did not reach the extraction quality we needed.")
- **Do not pursue `structured_output_mode` as a fix.** Confirmed no-op for the seam (proxy strips + no grammar) and falsified by the 9B run.
- **Do not adopt PR #1910 / #1909 fixes blindly.** Closed-unmerged, and the mechanism (grammar optionality; endpoint drop) doesn't fire in 0.31.2 + our proxy. Tooling+tests exist so it *can* be tried later without risk.
- **The real levers for precision/recall** (all gated, §G): prompt-side echo mitigation for the 9B, ontology `Person`-removal, and only after a gold standard — chunk size and concurrency.
- **Guard → 0.40** as operational calibration; it buys rebuild stability at the measured ~68% ± 4% yield, *not* quality.

---

## G. Experiments planned but not yet run (provenance preserved)

Prerequisite for quality arms: a frozen, independently-reviewed **gold standard** on `scratch/abfix_slice8.json` (entities/types/relations/direction/temporal/attributes) — none exists yet (§10). The harness (`scratch/chunk_slice_measure.py`) already monkeypatches `_generate_response` to capture bodies and classifies via `gex.classify_error`; add a `schema-echo` class there to mirror the seam.

- **Exp-2 json_schema vs json_object:** predicted near-identical for the same reason in §A.1 (proxy collapses both). Measure to confirm; do not expect it to fix the 9B.
- **Exp-3 schema normalization:** original vs flatten-`$defs` vs recursive-`required` vs combined; nullable preserved; unit-tested. Measure yield/quality, not just valid-JSON.
- **Exp-4 endpoint audit:** derive from captured bodies (proposed/accepted/dangling counts) — code path is `edge_operations.py` ~260-320. Use Design-2 shape-aware normalization if a precision repo appears.
- **Exp-5 ontology `Person` removal:** measure node-type distribution + whether persons/orgs vanish; gold-scored precision+recall.
- **Exp-6 `GRAPHITI_MAX_COROUTINES=2`; Exp-7 chunk_size=1500:** vary one at a time, after a stable baseline.

Each future run must record: git commit; model identity from the **serving log** (not RSS); quantization; provider; graphiti-core 0.31.2; `structured_output_mode`; ontology version + schema/dataset hashes; chunk config; budget; concurrency; retry config; wall time; node/edge counts; and per-chunk failure classification (now including `schema-echo`).

---

## Run provenance (this investigation)

- Repo: `phase6-frontier` @ `d43758d` (working tree was clean). Code+test changes are the only diff (`backend/app/utils/graphiti_zep.py` +117/−1; new `backend/tests/test_graphiti_schema_echo_and_transforms.py`).
- graphiti-core **0.31.2**; kuzu 0.11.3; embedder local SentenceTransformer; reranker NoOp; store embedded Kuzu.
- 8-bit control: `scratch/chunkslice_ab8before2_1794285607.json` — 6 ok / 2 skip (malformed-json), 88 nodes / 54 edges, budget 4096, cs 500/50, model LFM3-1.7B-MLX-8bit.
- 9B: `scratch/chunkslice_slice9b_1794295934.json` + `scratch/9bserve_backend_1794296853.log` — 5 ok / 3 skip (schema-echo), 11 nodes / 7 edges, budget 4096, cs 500/50, model **Ornith-1.5-9B-MLX-6bit** (serving-log verified, est 8004 MB).
- Upstream sources read live: issues #868/#1909/#1226/#1920, PR #1910 (closed-unmerged), PR #1964 (dangling endpoints, 2026-10-09), `README.md` "Structured output and small models."
