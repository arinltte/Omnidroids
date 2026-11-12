# Omnidroids

The words this project uses for its local knowledge-graph extractor, fixed so every
report, handoff note and ticket means the same thing by the same word. Extending this
file is part of any change that invents a concept; silently using a second word for an
existing one is a defect.

## The measurement vocabulary (HANDOFF §9 G, docs/GOLD_STANDARD.md)

**Gold**:
The frozen annotation of what a correct extraction of the frozen slice looks like: which
real-world entities must exist, which must not, and which links with what dates. A gold
that does not pass its own grounding check is not a standard.
_Avoid_: answer key, ground truth, expected output.

**Slice**:
The frozen test document (the acceptance report's first 8 chunks, 500 chars / 50
overlap). Every arm is run on the same slice so the numbers can be compared.
_Avoid_: test set, corpus.

**Arm**:
One measured test of the pipeline against the gold, changing exactly one variable.
An arm's result is claimed only after its graph is scored by the gold scorer and its
review queue is adjudicated by hand.
_Avoid_: experiment, run, trial.

**Baseline / control**:
The frozen 8-bit runs the arms are compared against (`ab8before2_1794285607`,
`ab8after2_1794286815`). Not a re-run — a reference.
_Avoid_: old version, previous run.

**Hypothesis**:
What one run actually produced, dumped from its graph and fed to the scorer. The
hypothesis is never edited by hand.
_Avoid_: prediction, output, proposal.

**Recall**:
The share of the gold's entities the hypothesis found. The gold fixes the scale: one
gold entity is 14.3 recall points on the slice.
_Avoid_: accuracy, coverage (unless you say coverage of what).

**Precision**:
The share of the hypothesis's nodes that resolve to gold entities. Reported both
unadjudicated and adjudicated, because the review queue changes it.
_Avoid_: quality, correctness.

**Junk**:
A surface form the gold declares must NOT become a node (abstract themes, metrics,
document artifacts, out-of-ontology types). Extracting junk is penalised; missing it is
not. A junk entry must be justified by an annotation rule and grounded in the slice —
never added because the model emitted it.
_Avoid_: noise, garbage, false positive (see also: review queue).

**Borderline**:
A defensible extraction a reasonable reader could accept or reject. Reported on its own
line, never silently counted.
_Avoid_: maybe, gray area.

**Gold edge**:
A link the ontology can express between two gold entities, with the direction the
ontology declares, and the dates the text states. All five on this slice are
`REPORTS_ON` (platform → university).
_Avoid_: correct link, true relation.

**Review queue**:
Hypothesis nodes matching neither gold nor junk. They count as false positives until a
human rules on each one, and the ruling is recorded in the gold's revision history.
_Avoid_: leftovers, unmatched nodes.

**Resolution limit**:
The smallest move this slice can show: one entity = 14.3 recall points, one edge = 20
edge-recall points. An arm that moves fewer than two entities is noise and must be
reported as noise.
_Avoid_: margin of error (this is a slice property, not a statistic).

## The pipeline vocabulary (backend/app, docs/MEASUREMENT_RUNBOOK.md)

**Chunk**:
One slice of document text fed to the model as one episode (500 chars / 50 overlap by
default). The unit of fault isolation: a chunk either lands or is skipped whole.
_Avoid_: segment, block.

**Graph**:
The knowledge graph in embedded Kuzu under `backend/data/graphiti/<graph_id>/`, holding
nodes, edges and their types for one ingestion.
_Avoid_: database, store.

**Node / edge / relation**:
A node is a typed entity in the graph (typed by the ontology), an edge a directed link
between two nodes, and the relation its name. "Entity" in a report means the real-world
thing the text is about; a node is the graph object that (correctly, or wrongly)
represents it.
_Avoid_: entity for node (they diverge exactly where recall and precision diverge).

**Ontology**:
The allowed node types and edge types (10 and 10 on the frozen slice), persisted per
graph at `<graph_id>/ontology.json`. A real-world thing with no possible type is junk by
rule — the ontology is a hard constraint on the gold.
_Avoid_: schema (the JSON Schema of a model response is a different thing entirely).

**Clamp / M1**:
The per-call output-token ceiling (`GRAPHITI_LLM_MAX_TOKENS`, production 4096),
enforced at the leaf LLM call. The dominant cost and truncation boundary.
_Avoid_: budget (see Exp-3's notes), limit.

**Skip guard**:
The batch policy that aborts a graph build when the skipped-chunk fraction exceeds
`GRAPHITI_MAX_SKIPPED_FRACTION` (0.40 in live `.env` — calibration, not quality).
Distinct from per-chunk fault isolation.
_Avoid_: skip threshold (use for the guard's number, not the guard).

**malformed-json**:
The failure class where the model's response is not parseable JSON. With
`finish_reason=length` it is a certified clamp truncation; with `stop` it is a
generation defect (retryable). The two are never merged in a report.
_Avoid_: parse error, bad JSON.

**schema-echo**:
The failure class where the model returns the request's JSON Schema instead of
extraction data. Terminal, not retried, and never counted as extraction.
_Avoid_: echo, schema dump.

**Salvage**:
The bounded recovery of a clamp-truncated extraction body: keep every COMPLETE element
the model produced and re-close the containers, instead of discarding the whole chunk.
Never invents content; an echo-shaped salvage still fails fast.
_Avoid_: repair (the proxy's `_repair_json` is a different, complete-value-only parser).

**Seam**:
The measurement harness `scratch/chunk_slice_measure.py` — the real pipeline (Flask
`/v1` proxy → ModelManager → MLX, `add_episode` over Kuzu + the production embedder),
one row per LLM call. The only place an arm is measured.
_Avoid_: mock, fixture, test rig.

**Evidence triple**:
`scratch/chunkslice_<label>_<epoch>.log` + `.json` + `_backend.log`. A measured claim
cites the triple and the commit it was measured on — never a remembered number.
_Avoid_: log, results file.

**Provenance**:
The per-arm record §9 G requires: commit, model from the **serving log** (never the
harness's `model=` line), quantisation, provider, graphiti version, output mode, ontology
version + schema/dataset hashes, chunk config, budget, concurrency, retry config, wall
time, node/edge counts, and per-chunk failure class.
_Avoid_: metadata, config dump.

**Recovery path**:
What the seam does with a malformed model body: it keeps the model's own complete
elements instead of discarding the chunk. Policy by failure class, the invariants,
the extension recipe and the full-build HTTP recipe: `docs/MEASUREMENT_RUNBOOK.md`
§2.1–§2.2. The measured record: `docs/GOLD_STANDARD.md` §5.
_Avoid_: repair (the proxy's own complete-value parser), retry (a different mechanism).

**Salvage class**:
The failure class a recovery applies to: a certified clamp truncation
(`finish_reason` `length` or absent) or a `stop`-finished generation defect. A class
never widens silently — one variable per change, measured on the slice first.
_Avoid_: error type, failure mode.

**Dropped early**:
The count of corrupt leading elements the recovery discarded so later complete
elements could be kept. It rides in the payload and the log line, so a report
cannot hide the loss.
_Avoid_: skipped (that means a chunk), dropped nodes (a different metric).

## The arms ledger — what is already measured (2026-11-11)

Never re-run a measured arm; the table says what happened and what the result was
for. Model `LFM3-1.7B-MLX-8bit`, production budget 4096, frozen slice unless noted.

| arm | change | outcome |
|---|---|---|
| baseline | none (control `ab8before2_1794285607`) | recall 2/7, gold edges 0/5, 46 calls per gold entity, 2 chunks skipped |
| Exp-5 | ontology: drop the `Person` catch-all | **null-to-negative — not adopted** (recall unchanged, precision halved; junk is ontology-independent) |
| truncation salvage | code: keep complete elements from a clamped body | **adopted** — recall 5/7, confirmed on a repeat |
| defect salvage | code: same for a `stop`-finished defect | **adopted** — recall 7/7, 8/8 chunks |
| edge descriptions | ontology: sharpen the 10 edge-type descriptions | **adopted with the code** — first conformant relation names; all 5 gold edges in the combined run |
| drop-corrupt-early | code: drop corrupt leading elements, keep the rest | **adopted** — closes the last skip class; combined with the descriptions, banks every gold metric |
| **full build** | none: the 53-chunk document through the launcher | **49/53 ok, 4 skipped (7.5 %), 410 nodes / 736 edges, no errors** |

**Already falsified — do not re-run**: description wording for the `Person` catch-all (§9 C),
budget 8192, the 9B, chunk 1500 (§9 B), and Exp-5 as a precision lever.
