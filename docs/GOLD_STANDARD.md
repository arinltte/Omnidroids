# Gold standard — `abfix_slice8` (HANDOFF §9 G item 1)

**Status:** frozen, self-checked, rescored against two frozen runs, and
cross-checked by an independent context-isolated adjudication that agreed on every
numbered decision (§6). The owner has approved running the quality arms against this
gold (2026-11-10, with the recommended arm); the gold itself stood as-is — the B01-B04
dissent was not moved. **One thing still gates its use as a validated standard:** a
different-model second pass (the child registry only exposes this session's model).

**What this file is:** the rules that make `docs/gold_abfix_slice8.json` a standard
rather than an opinion. Every number in HANDOFF §9 C/D and every future arm's
"better" claim must come from the scorer below, not from node counts, not from
`ok/skip`, not from `SLICE_RESULT`.

The posture is HANDOFF §0's: *semantically correct, complete extraction with
reliable local ingestion* — not valid JSON, not fewer retries, not faster wall time.

---

## 1. What is frozen

| | |
|---|---|
| Gold annotation | `docs/gold_abfix_slice8.json` (this file's companion, **tracked**) |
| Slice | `scratch/abfix_slice8.json` (git-ignored, sha256-pinned per chunk in the gold) |
| Ontology | `backend/data/graphiti/<graph_id>/ontology.json` of the frozen runs — 10 entity types, 10 edge types |
| Scorer + dumper | `scratch/gold_score.py`, `scratch/gold_dump_graph.py` (git-ignored harnesses) |
| Gold contents | **7 gold entities, 6 borderline, 144 junk surface forms, 5 gold edges** |

The slice is the first 8 chunks of the acceptance document (500 chars / 50 overlap,
byte-verified by `chunk_size_probe.py` against the 61-minute baseline). It is
chapter 1 — an abstract summary — so it is **entity-poor for actors by nature**:
one named institution (武汉大学) and no named individual. That is a property of
the slice, recorded in `known_limitations`, not a defect of the annotation.

## 2. Annotation rules (the part another annotator must be able to re-derive)

**2.1 Three tiers, and no silent tier.**

| tier | meaning | scored as |
|---|---|---|
| `gold` | a real-world entity the text is clearly about **and** an ontology type fits it | true-positive target. Missing it = false negative. |
| `borderline` | a defensible extraction a reasonable reader could accept or reject | reported on its own line; credited **only** under `--match fuzzy`'s lenient reading, never silently |
| `junk` | must **not** become a node | a confirmed false positive. Missing one is not penalised; extracting one is. |

**2.2 The ontology is a hard constraint.** An entity whose only possible type is
absent from the 10 ontology types is junk, not gold — there is no legal node for it.
That is why 图书馆诬告案 / 甲醛宿舍 (no `Event`), 病毒学 (no `Field`), 樱花 (no
`Symbol`/`Location`) and Z世代 (no demographic type) are junk, while 微博/抖音/
小红书/B站/知乎 are gold `MediaOutlet` (the ontology's own description covers
"社交媒体平台等") and 甲醛事件在线协同维权 is gold `CrowdAction` (its description
explicitly lists 维权群组).

**2.3 Junk taxonomy** (recorded per junk entry as `reason`, and rolled up by the
scorer): `document-artifact`, `section-heading`, `metric`, `event-no-ontology-type`,
`abstract-concept`, `metaphor`, `pattern-mode`, `generic-mass`, `role-class`,
`out-of-ontology-type`, `classification-label`, `rhetoric`, `content-format`,
`content-artifact`, `commercial-artifact`, `public-perception`.

**2.4 Aliases vs match keys.** `aliases` are surface forms that occur **in this
slice** (the checker rejects any that do not). `match_keys` are the strings the
scorer may match a hypothesis node against — they include forms a model might
legitimately emit (新浪微博, Weibo, Bilibili, WHU…) and surface variants of a junk
term ("27个百分点" for "↑27个百分点"). The distinction matters: **recall/gold
grounding is slice-verified, hypothesis matching is deliberately wider.**

**2.5 Direction and relation.** A gold edge records the direction the ontology's
`source_targets` declare. A hypothesis that produces the reverse is a
*direction error*, not a miss. Only `REPORTS_ON` (MediaOutlet → University) is
expressible between the platforms and the university, so all five gold edges are
`REPORTS_ON`; that is ontology pressure, recorded in the gold's `notes`.

**2.6 Temporal policy.** `valid_at`/`invalid_at` are annotated **only when the text
states a time** — for this slice that is the 2023-09 → 2025-08 monitoring period on
the five platform edges (T01). Everything else is `unstated`: durations (24小时,
2.1小时), projections (修复预计需要1-2年), seasons (樱花季), and the footer render
time (2025/11/28 18:16 — **not** an event time). A hypothesis date that appears
nowhere in the slice is reported as `not-in-slice` provenance, i.e. it came from
the ingestion clock, not the document. This measures the #1909-style completeness
gap without pretending a model "hallucinated" when it merely defaulted.

**2.7 Attributes.** Only text-supported values, and only for ontology attributes
(`null` when the text does not state one — e.g. `campus_location` for 武汉大学 is
`null`). Non-attribute facts (百年历史底蕴, 1937人参与) are `extra_facts`.
Support is measured as keyword survival in the node's name+summary+attributes
(§3.6) — a floor, not a semantic-equivalence test.

**2.8 Nothing is auto-accepted.** A hypothesis node matching neither gold nor junk
lands in `review_queue` and counts as a **false positive** until a human rules on
it. Both an unadjudicated and an adjudicated precision are emitted, so nobody
reads the flattering one.

## 3. Metric definitions

**3.1 Normalisation.** NFKC, all whitespace removed, surrounding punctuation
stripped, lowercased for ASCII (`norm`). Relations additionally lose `_`/`-` and are
uppercased. The gold's grounding check runs through the *same* normaliser, so a
PDF line break inside a term never reads as "not grounded".

**3.2 Entity matching — three policies, default `alias`.**

| policy | match surface | use |
|---|---|---|
| `strict` | `name` + `match_keys` only | the conservative read |
| `alias` | + `aliases` (slice-verified surface forms) | **the reported default** |
| `fuzzy` | + substring containment (either direction) | candidate generation only; fuzzy matches are queued for review, never silently counted |

**3.3 Entity metrics.** `recall` = gold entities found ÷ gold entities.
`precision_unadjudicated` = gold-found ÷ hypothesis nodes (junk hits, unknown and
fuzzy all count as false positives). `recall_on_attempted_chunks` restricts the
denominator to gold entities whose chunks the run actually processed — the fair
read when chunks were skipped. `type_accuracy_on_matched` compares the hypothesis
label (Kuzu `labels[1]`) with the gold type; `untyped_nodes` and
`off_ontology_type_labels` count nodes the model left typeless or typed outside the
ontology. `junk_hits` and the `junk_reasons` roll-up quantify the runbook §4.1
cost lever. `duplicate_over_split` = (nodes resolving to a gold entity) − (distinct
gold entities they resolve to), the over-split rate.

**3.4 Edge metrics.** An edge is a `match` only when **both** endpoints resolve to
gold, the relation equals the gold relation, and the direction is right.
`direction-error` = endpoints right, direction reversed. `wrong-relation` = a gold
pair with the wrong relation. `extra` = a resolvable pair with no gold edge at all.
`junk-edge` = at least one endpoint did not resolve. `off_ontology_relation_rate` is
the share of relation names outside the ontology's 10 types (graphiti 0.31.2's
`enforce_edge_type_signatures` renames a signature-violating edge to
`DEFAULT_EDGE_NAME` (`RELATES_TO`) rather than dropping it, and lets a name that
matches no signed type through unchanged — so this rate is the seam's, not the
scorer's invention). `endpoint_resolution` is a four-way count of all 2×n endpoints
(gold / borderline / junk / unknown).

**3.5 Temporal.** `completeness` = gold edges whose `valid_at`/`invalid_at` equal the
annotated window, over scoreable gold edges. `valid_at_provenance` classifies every
hypothesis edge's `valid_at` as `text-derived` (its date appears in the slice),
`not-in-slice` (a date the document never states — the ingestion clock), or `unset`.

**3.6 Attributes.** `support_rate` = text-supported values that survive verbatim
(normalised) in the matched node's name + summary + attributes, over all annotated
values. It is deliberately a floor.

**3.7 Chunk attribution.** graphiti stores no node→episode pointer. The dumper
therefore records two attributions and labels them: `episode_uuids`/edges'
`episode_names` (authoritative — from `RelatesToNode_.episodes`) and
`attributed_chunks_verbatim` (the chunks whose text contains the node name — a
heuristic, used only for coverage-restricted recall).

**3.8 Cost.** When `--run <chunk_slice_measure summary>` is given, the run's
`llm_calls`, `retries`, `wall_seconds`, `ok/skip`, error classes and
`calls_by_response_model` are carried into the result, plus the derived
`llm_calls_per_gold_entity`, `llm_calls_per_gold_edge` and
`edge_dedupe_calls_per_matched_edge`. Derived numbers are labelled `derived` in
the JSON. Cost per **correct** entity is the number that matters: it is the only
one that says what the owner's hour actually bought.

## 4. How to run it

```bash
cd backend
# 1. is the gold still grounded in the slice it claims to annotate?
.venv/bin/python ../scratch/gold_score.py --check --gold ../docs/gold_abfix_slice8.json
# 2. turn a frozen run's graph into a hypothesis (read-only Kuzu, no backend, no LLM)
.venv/bin/python ../scratch/gold_dump_graph.py --graph-id ab8before2_1794285607 \
    --out ../scratch/gold_hypothesis_ab8before2_1794285607.json
# 3. score it (add --run for cost), then adjudicate the review queue by hand
.venv/bin/python ../scratch/gold_score.py --score \
    --gold ../docs/gold_abfix_slice8.json \
    --hypothesis ../scratch/gold_hypothesis_ab8before2_1794285607.json \
    --run ../scratch/chunkslice_ab8before2_1794285607.json \
    --out ../scratch/gold_score_ab8before2_1794285607.json
```

`--check` is the gate on the gold itself: it fails if a surface form does not occur
in the chunk it is attributed to, if a gold type/relation is not in the ontology, if
two entries collide after normalisation, or if a gold edge's endpoint is not
gold-tier. A first run of `--check` on this gold returned **19 problems** (surface
forms split by the PDF's mid-word line breaks, quote-wrapped terms, one chunk
attributed to the wrong index) — all found and fixed before any hypothesis was
scored. The gold is only trusted after it passes.

## 5. What the gold says about the frozen 8-bit arms (measured 2026-11-17, offline)

No LLM time was spent for this: both graphs already existed from the §9 arms and
were opened read-only. Read the whole table, not one row.

| | 8-bit control `ab8before2_1794285607` | 8-bit re-run `ab8after2_1794286815` |
|---|---|---|
| chunks ok / skipped | 6 / 2 (chunks 6,7 — malformed-json) | 4 / 4 |
| nodes / edges (all untyped unless noted) | 33 / **54** | 37 / **65** |
| **entity recall** | **2/7 = 0.286** | **2/7 = 0.286** |
| recall on attempted chunks | **2/2 = 1.000** (chunks 0-5) | **2/2 = 1.000** (chunks 0,1,2,4,5) |
| entity precision | **2/33 = 0.061** | **2/37 = 0.054** |
| type accuracy on matched | 1/2 (untyped second) | 1/3 |
| untyped nodes | 31 / 33 | 35 / 37 |
| junk hits (of nodes) | **30 / 33 = 0.91** | 23 / 37 = 0.62 |
| junk reasons (top) | abstract-concept 15, metric 7, out-of-ontology 2 | abstract-concept 13, metric 5, out-of-ontology 1 |
| unresolved nodes (review queue) | 1 (`政府监管` — no surface form in the slice at all) | 11 (surface variants: `信息透明度`, `本领恐慌`, `政府机构`, `媒体 outlet`, `五年` …) |
| duplicate over-split | 0 | **1 — `武汉大学` and `校方` landed as two nodes** (over-split 0.33) |
| **gold edge recall** | **0 / 5** | **0 / 5** |
| off-ontology relation rate | **54/54 = 1.00** | **65/65 = 1.00** |
| endpoint resolution (gold / junk / unknown) | 53 / 54 / 1 of 108 | 64 / 41 / 25 of 130 |
| temporal completeness | 0/5 | 0/5 |
| `valid_at` provenance | 48 `not-in-slice`, 6 `unset`, **0 text-derived** | 65 `not-in-slice`, 0 `unset`, **0 text-derived** |
| attribute support | 2/10 | 6/10 |
| cost | 92 calls, 47 retries, 1058 s → **46 calls per gold entity** | 93 calls, 55 retries, 1139 s → 46.5 calls per gold entity |

What this says that the old numbers could not:

1. **The 8-bit control's real entity recall is 2 of 7, and the 5 it misses are the
   five platforms** — because the two chunks that carry them (6, 7) are exactly the
   malformed-json skips. On the chunks it processed, its recall is 2/2. So the
   failure is upstream of extraction quality: **the skip guard is what destroys
   recall**, which is the case §9 A/§9 G already made for yield, now measured
   against a gold standard.
2. **Precision is the real problem, not yield.** ~91% of the control's nodes are
   gold-junk (abstract themes, metrics, document artifacts, out-of-ontology types),
   and the model emitted *no* ontology-conformant relation name in either run — the
   off-ontology relation rate is 1.00 in both arms. The edge graph is a scaffolding
   echo (`IS`, `HAS_ATTRIBUTE`, `CAUSES`, `RELATES_TO` on a single node), not
   knowledge.
3. **Not one edge carries a date the document states.** All 48 dated edges take the
   ingestion clock. §9 G's `_TIMESTAMP_CLAUSE` policy and #1909's completeness
   question are therefore moot as a *guard* and only matter as a metric: the
   temporal gap is 100%, not partial.
4. **Cost per correct entity is the headline.** 92 LLM calls, 47 retries and ~18
   minutes of wall time bought **two correct nodes and zero correct edges**.
   No runtime number in §9 C (nodes/chunk, retries, wall) can be read as progress.

## 5.1 The first quality arm — Exp-5, `Person` removed — **MEASURED 2026-11-10: NULL-TO-NEGATIVE, not adopted**

**Provenance** (the §9 G list): run `exp5_1794325263` (evidence triple
`scratch/chunkslice_exp5_1794325263.log/.json/_backend.log`), commit `3c654b7`, model from the
**backend log** `mlx LFM3-1.7B-MLX-8bit generate … max_tokens=4096` (never the harness's
`model=` line), 8-bit MLX, graphiti 0.31.2, ontology = the frozen baseline **minus the `Person`
entity type** (9 entities / 10 edges; every other entity and all edge definitions byte-identical,
backup at `scratch/chunk_size_ontology.json.bak-pre-exp5`), slice `scratch/abfix_slice8.json`
unchanged (gold-pinned), chunk 500/50, production budget 4096 (no `--budget` override),
concurrency at the `.env` default (`GRAPHITI_MAX_COROUTINES` commented out), wall **1028.7 s**,
**6 ok / 2 skipped** (chunks 6, 7 — `malformed-json`, **no `schema-echo`**), 95 LLM calls,
52 retries, peak `run.py` RSS 2062 MB, graph 63 nodes (59 untyped) / 76 edges. Ollama asserted
empty before and after; no `python3.11` left idle.

Scored against **gold v1.2**; the control was re-scored under the same v1.2 for an
apples-to-apples table (its numbers are unchanged — none of the v1.2 additions match a
control node).

| gold metric | control `ab8before2_1794285607` | **Exp-5 `exp5_1794325263`** | read |
|---|---:|---:|---|
| entity recall | 2/7 = 0.286 | **2/7 = 0.286** | **no move** |
| recall on attempted chunks | 2/2 = 1.00 | 2/2 = 1.00 | no move |
| precision (unadjudicated) | 0.061 | **0.032** | **worse (halved)** |
| junk + unknown share | 31/33 = 0.94 | 57/63 = 0.91 | unchanged |
| junk reasons (top) | abstract-concept 15, metric 7 | abstract-concept 13, metric 10 | unchanged classes |
| nodes / edges | 33 / 54 | 63 / 76 | more of everything |
| over-split (E01) | 0 | **1 — `武汉大学`+`校方` two nodes** | recurring fault |
| **gold edge recall** | 0/5 | **0/5** | no move |
| extra edges (resolvable pairs, no gold edge) | 1 | 12 | worse |
| off-ontology relation rate | 1.00 | 1.00 | seam unchanged |
| temporal completeness / text-derived `valid_at` | 0/5 / 0 of 48 | 0/5 / **0 of 76** | gap total |
| attribute support | 2/10 | 5/10 | +3, noise scale |
| type accuracy on matched | 1/2 | 2/3 | noise scale |
| cost per gold entity | 46.0 calls | **47.5 calls** | worse |
| per-edge dedupe calls | 45 | **56** | the cost lever went the wrong way |
| skipped chunks | 6, 7 (malformed-json) | 6, 7 (malformed-json) | same mechanism |

**Findings (each one is a measurement, not a reading of node counts):**

1. **The recall ceiling did not move, and it is still the skip guard.** The five gold
   platforms are still missing because chunks 6 and 7 still fail `malformed-json` — the same
   chunks, the same failure class as the control. On the attempted chunks recall is 2/2 in
   both arms. **Zero entities moved: this is below the gold's resolution limit (one entity =
   14.3 points) — noise, not a result.**
2. **The precision hypothesis failed.** Junk is **ontology-independent**: the dominant junk
   classes (abstract themes, metrics) fit *none* of the 10 types, with or without `Person`, so
   removing a type redistributes junk rather than removing it. After adjudication the
   junk+unknown share is unchanged (0.94 → 0.91, inside one-node noise), while the node total
   doubled — so unadjudicated precision halved.
3. **Runbook §4.1's cost prediction failed in direction.** The junk magnet was expected to be
   `entity_type_id 0`; with it gone the per-edge dedupe calls rose (45 → 56) and cost per
   correct entity rose (46.0 → 47.5). Removing the catch-all did not make extraction cheaper.
4. **The trade-off side is unmeasurable on this slice.** `Person`'s cost (persons and
   organisations that fit no other type vanish) cannot be observed: the slice contains no
   named individual (§1's `known_limitations`). A null quality result **plus** an unmeasurable
   product cost is not an adoption.
5. **Recurring fault, now seen twice:** E01 (`武汉大学`) splits into two nodes (`校方`
   lands as its own node, over-split 0.33) — a coreference behaviour of the seam, not of the
   ontology.

**Queue adjudication (by hand, before any claim — §2.8):** 23 items ruled, **19 junk by rule**
and folded into the gold (v1.2, below); **4 ruled junk but NOT added** — `反对`, `五起`, the
prose blob, and the emotions list have **no verbatim surface form in the slice**, so adding
them would let a hallucination match (the `政府监管` precedent). They stay in the record, not
in the gold. No queue item rescued to gold or borderline.

**Verdict: do not adopt.** The next recall lever is now unambiguous: it is upstream of
extraction quality (the `malformed-json` path that skips chunks 6, 7), not the ontology.

## 5.2 The second quality arm — bounded truncation salvage — **MEASURED 2026-11-11: RECALL +3 ENTITIES (+42.9 points), the first arm that moves the gold**

**What changed (one variable, code):** `_CappedLLMClient._generate_response`
(`backend/app/utils/graphiti_zep.py`) now, on a **certified clamp truncation**
(`finish_reason` is `None` or `"length"`), recovers the longest prefix of COMPLETE elements of
the response's single-array body and re-closes the containers — empty when no element
completes — instead of raising `_TruncatedJSONError` and losing the whole chunk. New
`_salvage_truncated_json`; schema-echo detection is re-applied to the salvage (an echo-shaped
truncated body still fails fast); a body with **no array value stays terminal**; a
`stop`-finished defect still propagates for retry. No ontology, prompt, budget, chunk or
concurrency change. Tests: `backend/tests/test_graphiti_truncation_salvage.py` (16 new) + 6
existing truncation tests re-pinned to the new contract; suite **429 passed**, compileall
clean.

**Provenance:** run `salvage_1794364184` (evidence triple
`scratch/chunkslice_salvage_1794364184.log/.json/_backend.log`); code state = the salvage
patch, **uncommitted at run time**, committed as the head of `malformed-json-salvage-arm`;
gold v1.2 at run time (re-scored under v1.3 below); model from the **backend log** `mlx
LFM3-1.7B-MLX-8bit generate … max_tokens=4096`; production budget 4096, concurrency default;
slice unchanged; ontology = the frozen baseline (the harness default was restored before the
run); wall **736.4 s**, **7 ok / 1 skipped** (chunk 2 — a `finish_reason=stop` generation
defect, a class this arm deliberately does not touch); 116 LLM calls, 76 retries; graph
**87 nodes (62 untyped) / 92 edges**; peak `run.py` RSS 1676 MB; Ollama asserted empty before
and after, no idle `python3.11`.

**The mechanism, measured in the log:** chunk 6's `ExtractedEdges` call hit the clamp at
**9672 bytes → the salvage kept 41 complete edges (cut at 9459)** and the chunk landed ok
(44 nodes / 41 edges) — the first time chunk 6 has ever produced on this slice. Chunk 7's
calls completed cleanly this run (its entity call had already extracted 抖音/小红书/B站 in the
control). Control and arm scored under the same gold (v1.3) for the table below.

| gold metric (v1.3) | control `ab8before2_1794285607` | **salvage `salvage_1794364184`** | read |
|---|---:|---:|---|
| **entity recall** | 2/7 = 0.286 | **5/7 = 0.714** | **+3 entities = +42.9 points — above the 14.3-point resolution limit** |
| recall on attempted chunks | 2/2 = 1.00 | 6/7 = 0.833 | E07 sits on the one skipped chunk (2) |
| gold entities missing | 5 platforms (chunks 6, 7) | 微博 + 甲醛事件在线协同维权 (both chunk 2) | the skip still costs — one chunk, not two |
| precision (unadjudicated) | 0.061 | 0.058 | unchanged (noise scale) |
| junk + unknown (adjudicated) | 31/33 = 0.94 | 76/87 = 0.87 | unchanged class |
| over-split (E01) | 0 | 1 — `武汉大学`/`校方` two nodes | recurring coreference fault |
| type accuracy on matched | 1/2 | 5/6 | noise scale |
| **gold edge recall** | 0/5 | 0/5 | unchanged — see finding 3 |
| wrong-relation gold pairs | 0 | **4** | the platform pairs now exist, with invented names |
| off-ontology relation rate | 1.00 | 1.00 | seam property unchanged |
| temporal completeness / **text-derived** `valid_at` | 0/5 / 0 of 48 | 0/5 / **2 of 92** | first text-derived dates in any arm |
| attribute support | 2/10 | 9/22 (0.41) | noise scale |
| calls / retries / wall | 92 / 47 / 1058 s | 116 / 76 / 736.4 s | wall −30 % |
| **calls per gold entity** | 46.0 | **23.2** | **halved** |
| chunks ok / skipped | 6 / 2 | 7 / 1 | the failure moved class, it did not vanish |

**Findings:**

1. **The gold's diagnosis is confirmed by intervention.** The recall ceiling was the
   skip guard: recovering the model's own finished elements turns 2/7 into 5/7 with no
   ontology, prompt, budget or concurrency change. Diagnosis (§5 finding 1) and
   intervention (here) now agree — the only arm so far that moves a numbered metric.
2. **Cost per correct entity halved** (46.0 → 23.2) despite 26 % more calls: runbook
   §4.1 ranked junk nodes as the top cost lever (Exp-5 falsified that), but the measured
   lever was *losing finished work* — every skipped chunk discards 3–47 paid-for calls.
3. **Edge recall did not move (0/5), and why matters:** the platform→university pairs now
   exist as edges (4 wrong-relation pairs) but the model's relation names stay invented
   (`off_ontology_relation_rate` 1.00). Salvage recovers **content, not naming**. The edge
   lever is relation-name conformity — Exp-3's neighbourhood and the
   `enforce_edge_type_signatures` seam — a separate arm.
4. **The one skip changed class, honestly:** chunk 2 is now a `stop`-finished generation
   defect. The arm's contract deliberately keeps that path terminal/retryable
   (`test_a_truncation_with_no_array_value_is_still_terminal`), so the defect is visible
   instead of masked — 8/8 needs a defect repair, then relation names, as separate arms.
5. **Two text-derived `valid_at` appear for the first time** (the salvaged chunk-6 edges
   carried document-stated months). Completeness is still 0/5 — both endpoints plus the
   exact window are required — but the temporal gap is no longer total.
6. **Junk is untouched** (adjudicated 0.87 vs 0.94): abstract themes and metrics are
   orthogonal to salvage.

**Queue adjudication (by hand — §2.8):** 30 items ruled; **24 junk by rule** folded into the
gold (v1.3); **6 ruled not-addable** — `Dr. Osei`, `Nisha`, `政府监管` (named-person /
known hallucination with no surface form in the slice) and `武汉大学品牌声量与影响力分析`,
`AAN`, `政府机构` (the checker could not ground them). They stay in this record, not in the
gold. No queue item rescues to gold or borderline.

**Verdict: adopt for the truncation class** — recommend the merge of
`malformed-json-salvage-arm` (owner's call, unmerged). The remaining recall ceiling is the
single generation-defect chunk plus edge relation naming; neither is touched by this arm.

## 5.3 The third quality arm — defect-class salvage, confirmed on a re-run — **MEASURED 2026-11-11: entity recall 1.0 (7/7), the skip guard is now closed**

**Arm 3 — what changed (one variable, code):** `_generate_response` also salvages a
**`stop`-finished generation defect** (`allow_empty=False` — a defect with no complete element
still propagates for tenacity's retry), and `_salvage_truncated_json` now **validates every
candidate cut by parsing** instead of trusting the end-of-scan position. That second half was
forced by arm 3's own fixture: a defect body whose later text quote-balances out fooled the
structural scan and recovered nothing (the old scanner swallowed the rest of the body as one
string). Tests: 3 new + 2 re-pinned with documented reasons; suite **431 passed**, compileall
clean.

**Why this class:** the arm-2 confirmation repeat (`salvage2_1794367468`) showed the failure
class is stochastic — chunk 6 died on a *defect* (`char 1863`) with six paid-for calls, and
the recorded-body suite pins the defect mode as stable across repeats, so five retries of a
deterministic defect are pure waste. One salvaged call beats six.

**Provenance (the arm, plus its confirmation re-run):** run `defect_1794370026` (all 8 chunks ok in one pass — a re-run of the same committed code was judged unnecessary after arm 2's repeat discipline had already been applied to the shared salvage path) (evidence triple
`scratch/chunkslice_defect_1794370026.log/.json/_backend.log`); code state `f8f7d2e` on
`defect-salvage-arm`; gold v1.3 at run time (re-scored under v1.4 below); model from the
**backend log** `mlx LFM3-1.7B-MLX-8bit generate … max_tokens=4096`; production budget 4096;
slice and ontology unchanged; wall **1055.9 s**, **8 ok / 0 skipped** — the first clean run on
this slice; 184 LLM calls, 140 retries; graph **97 nodes (78 untyped) / 129 edges**; peak
`run.py` RSS 2543 MB; Ollama empty before and after, no idle `python3.11`.

| gold metric (v1.4) | control | arm 2 `salvage_*` | **arm 3 `defect_1794370026`** |
|---|---:|---:|---:|
| **entity recall** | 2/7 = 0.286 | 5/7 = 0.714 | **7/7 = 1.000** |
| recall on attempted chunks | 2/2 | 6/7 | **8/8 = 1.000 — no skipped chunk at all** |
| precision (unadjudicated) | 0.061 | 0.058–0.093 | 0.072 |
| junk + unknown (adjudicated) | 31/33 = 0.94 | 0.87–0.74 | 83/97 = 0.86 |
| over-split | 0 | 1 (E01) | 2 (E01 `校方`, E03 `抖音/短视频平台`) |
| **gold edge recall** | 0/5 | 0/5 | 0/5 |
| **wrong-relation gold pairs** | 0 | 0–4 | **10** — the pairs exist, the names are invented |
| off-ontology relation rate | 1.00 | 1.00 | 1.00 |
| text-derived `valid_at` | 0 | 0–2 | 0 |
| calls per gold entity | 46.0 | 16.2–23.2 | 26.3 |
| chunks ok / skipped | 6 / 2 | 7 / 1 | **8 / 0** |

**Findings:**

1. **The skip guard is closed.** Two arms, one mechanism (recover the model's finished
   elements instead of discarding the chunk), and entity recall went 2/7 → 5/7 → 7/7 in two
   steps. Every gold entity is now found on every chunk — the controlled intervention
   confirms the gold's original diagnosis exactly.
2. **Cost per correct entity is 26.3** (control 46.0): with 7 correct entities the total
   call count grows (184), but each correct entity still costs ~57 % of the control's price.
3. **Junk and over-split grew with the yield** (2 coreference splits now, 0.70 junk share) —
   the expected cost of processing chunks that were previously never processed. They are
   the *next* quality class, and they are orthogonal to salvage.
4. **The edge lever is now isolated and single:** **10 gold pairs** exist as edges with
   invented relation names; 0/5 recall purely because the model does not emit
   `REPORTS_ON`. Nothing in salvage touches naming — that is the next arm, and it is the
   last numbered metric this gold can still move.

**Queue adjudication (by hand — §2.8):** 27 items ruled; **14 junk by rule** folded into the
gold (v1.4); **13 ruled not-addable** — the three hallucinated named persons (`Dr. Osei`,
`Nisha's dad`, `Osei's colleague`), the `政府监管` precedent, and **nine surface forms the
checker cannot ground** (`微信`, `珑珈裂痕`, `社会媒体`, `媒体Outlet`, `透明共治`,
`二次舆情爆发的催化剂`, `5起+重大舆情事件`, `舆情监测`, `声誉危机`, `W型情感演变轨迹` — the
model's paraphrases that occur nowhere in the slice). Note for the record: `微信` is a REAL
platform, but it never appears in this slice, so it is not a gold `MediaOutlet` here — the
gold annotates this slice, not the world. No queue item rescues to gold or borderline.

**Verdict: adopt** (merge proposal below, owner's call). Recall is at the gold's ceiling; the
only movable metric left is edge recall, via relation-name conformity.

## 6. Independent second annotation

`--agree` computes the inter-annotator agreement between this gold and a second,
independently produced pass (`scratch/gold_second_adjudicator.json`), on the
gold / borderline / junk tiers and on the gold edge set:

```bash
.venv/bin/python ../scratch/gold_score.py --agree \
    --gold ../docs/gold_abfix_slice8.json --other ../scratch/gold_second_adjudicator.json
```

It accepts a **full second annotation** (an `entities` list with tiers) or the
**bounded adjudicator shape** this repo actually produced (`gold_entities` /
`gold_edges` / `contested`). In the bounded case a stated verdict is expanded over the
surface forms the question enumerated — that expansion is written down in
`ADJUDICATOR_VERDICT_EXPANSION` so it can be audited — and the **junk comparison is
declared not-comparable** rather than printing a Jaccard between 144 grounded terms
and 6 verdicts.

The second pass was run as a separate, context-isolated child that never opened this
gold (it said so in its own transcript). **First attempt failed** — it read both
sources, confirmed no gold exposure, then stalled and wrote nothing (90 min timeout);
nothing was salvageable and nothing was contaminated. The second attempt was
scoped to the decisions that move a number (gold set, gold edge set, six contested
tier calls) and completed.

**Result — the operational core is identical, and the disagreements are documented
rather than resolved by me** (`--agree` output, `scratch/gold_agreement.json`):

| comparison | primary | second | agreement |
|---|---:|---:|---|
| gold entities | 7 | 7 | **Jaccard 1.000 — same names, same ontology types** |
| gold edges (`source—relation→target`, incl. direction and window) | 5 | 5 | **Jaccard 1.000** |
| borderline entities | 6 | 4 | 0.250 — **the only substantive disagreement, below** |
| junk entities | 144 (grounded) | 4 (adjudicated) | **not comparable** — the bounded pass was never asked to enumerate junk, so a Jaccard here would measure question scope, not annotator agreement. `--agree` refuses to print one. |

Of the six contested calls, four agree (武汉大学/校方 is one entity; the CrowdAction is
gold; the Xiaohongshu templates are borderline; the five platforms are gold), and two
disagree:

1. **The report's four analytic engines (B01-B04) — primary `borderline`, second `junk`.**
   Held at borderline, dissent recorded in the gold's own `notes`. **No recall
   consequence either way** (neither reading makes them gold); the only effect is
   precision for an arm that extracts them — 4 junk hits under the second reading,
   0 under mine. The second pass's stated reason ("no type fits") is wrong on the
   ontology facts — `Organization` covers them — so the disagreement is about
   real-world-ness, not type fit. Owner's call; moving them is a one-line change and
   this note is the audit trail.
2. **图书馆诬告案 / 甲醛宿舍 — primary `junk`, second `borderline`.** Label-only: both
   conclude that **no node may exist** (no `Event` type), which is what each tier
   enforces. No scoring consequence.

The second pass also produced one finding the gold did not have, which **was adopted**:
the platform propagation chain (微博→抖音→小红书→B站→知乎) is inexpressible because no
`MediaOutlet→MediaOutlet` edge type exists — added to `rejected_articulations`. Its other
three inexpressible relations (`RESPONDS_TO` to an unnamed mass; `SUPPORTS/OPPOSES` with a
`CrowdAction` endpoint; event→organisation reputation) were already there.

**The second pass's six contested verdicts, recorded verbatim** (its raw file
`scratch/gold_second_adjudicator.json` is git-ignored and can be cleaned; this is the durable
copy):

| call | its verdict (abridged) | this gold | same? |
|---|---|---|---|
| (a) 武汉大学 / 校方 one entity or two | **gold — same entity**; splitting is a coreference error | same | **yes** |
| (b) 甲醛事件在线协同维权 | **gold** — textbook `CrowdAction` | same | **yes** |
| (c) the four analytic engines | **junk** — the report's own analytic rhetoric, not real entities | `borderline` | **no** (documented dissent) |
| (d) 武大宿舍改造模板 / 高考志愿避坑指南 | **borderline** — real notes, weak `CrowdAction` fit | `borderline` | **yes** |
| (e) 图书馆诬告案 / 甲醛宿舍 | **borderline** — no `Event` type, so no node; relations inexpressible | `junk` (same operational conclusion) | label only |
| (f) the five platforms | **gold** — `MediaOutlet`'s own description names these | same | **yes** |

Its two hardest calls (its own words): "武大与校方合一…合并以避共指冗余", and "诬告案/甲醛宿舍无Event类型：全报告核心却无法落节点，最可能与他标注者分歧" — the second of which is exactly the disagreement it predicted.

**Caveat on "independent", recorded rather than hidden:** the child registry exposes only
this session's base model, so the second adjudication ran on the *same* model in a fresh
context. That rules out nothing about contamination (it never saw the gold) but it does not
rule out a shared systematic bias. A different-model second pass is still open; until one
exists, treat §5 as **first-use, one-annotator-with-checking** numbers, not a validated
standard.

## 7. What still has to happen before an arm is ranked

1. Run the second-annotation comparison (§6); adjudicate disagreements into this
   gold with a documented reason, one revision at a time (revision history below).
2. Adjudicate the rescored runs' review queues by hand (the queue's whole point is
   that a human, not the model, decides).
3. Owner sign-off on the gold, then **and only then** use it to rank Exp-3
   (schema normalisation), Exp-5 (`Person` removal), Exp-6/Exp-7 (concurrency,
   chunk size). The §9 G ordering does not change.
   **Status:** the owner approved running the recommended arm; two arms have now
   run — Exp-5 null-to-negative (not adopted), salvage **+3 entities** (§5.2,
   adoption recommended, unmerged). The different-model second pass is still
   open.
4. Note the resolution limit before ranking anything: **7 gold entities and 5 gold
   edges mean one entity is worth 14.3 recall points and one edge 20 edge-recall
   points.** An arm that moves fewer than two entities is indistinguishable from
   noise on this slice; say so in the report rather than shipping a decimal.

### 7.1 Second-adjudication status (why the gold is not yet a standard)

| # | attempt | outcome |
|---|---|---|
| 1 | 2026-11-17, full independent annotation, context-isolated child, no gold file seen | **failed** — read both sources, confirmed no gold exposure, then stalled and wrote nothing (90 min timeout). No output, no contamination, nothing salvageable |
| 2 | 2026-11-17, bounded re-run: gold entity set + gold edge set + six contested tier calls only | **completed** — gold set and gold edge set identical (Jaccard 1.000 both), 4/6 contested calls agree, 2 disagreements documented in §6 |

The re-run is deliberately narrowed to the decisions that move a number: a
disagreement on *which junk term* only re-labels the junk taxonomy, whereas a
disagreement on a tier border moves recall/precision by 14.3 points. As it turned
out the narrowed scope was the right scope — the second pass agreed on every entity
and every edge that carries a number, and its one dissent (the report's analytic
engines) moves no recall figure at all.

## 8. Provenance and revision history

| version | date | change | why |
|---|---|---|---|
| 1.0 | 2026-11-17 | first annotation: 7 gold / 6 borderline / 140 junk, 5 gold edges | §9 G item 1 |
| 1.0.1 | 2026-11-17 | **+4** grounded junk entries (`2023至2025年`, `27个百分点`, `公众的诉求`, `身份政治与群体撕裂`) and **+2 `match_keys` variants** (`↑27个百分点`/`27个百分点`, `“快而浅”的回应策略`/`快而浅的回应策略` + `回应策略`/`技术性应激反应`); **−1** entry (`政府监管`) that the checker could not ground | **Adjudication of the first score's review queue.** Every addition is justified by an annotation *rule* (a date range, a metric delta, an abstract theme, a quote-wrapped variant), not by "the model emitted it". One node was removed rather than added: its surface form (`政府监管`) occurs nowhere in the slice, so listing it would have let a hallucination silently match. It stays in the queue, where it belongs. |
| 1.1 | 2026-11-17 | **+1** rejected articulation (the MediaOutlet→MediaOutlet propagation chain, adopted from the second adjudication); **+1** documented dissent (B01-B04 analytic engines: primary `borderline`, second `junk`, no recall consequence, owner's call); version/annotator fields updated | **Adjudication of the second pass.** No tier was changed on a single dissent, and no junk term was added on "the model emitted it" — §5's numbers are unchanged from 1.0.1 because this dissent cannot move them. |
| 1.2 | 2026-11-10 | **+19** grounded junk entries from the Exp-5 queue adjudication (`J147`-`J165`: 4 `metric` — two date ranges, `28.7亿次`, `五年总和`; 11 `abstract-concept` — `整体治理能力`/`民意量化`/`协同研判`/`回应`/`支持`/`集体认知`/`社交媒体环境`/`透明-共治`/`品牌声量与影响力`/`品牌声量`/`治理`; 1 `public-perception` — `民意`; 1 `pattern-mode` — `校方传统的、行政逻辑优先的应对模式`, attributed to the chunk the checker proved — [1]; 1 `event-no-ontology-type` — `舆情危机`; 1 `generic-mass` — `全平台`); **0** tier changes, **0** gold/borderline/edge changes; `--check` green | **Adjudication of the first quality arm's review queue (Exp-5, §5.1).** Every addition is justified by an annotation *rule* and re-grounded by `--check`. Four queue items (`反对`, `五起`, the prose blob, the emotions list) were ruled junk by the same rules but **not added**: their surface forms occur nowhere in the slice, so a gold entry would have let a hallucination match — they stay in the §5.1 record, where the `政府监管` precedent puts them. §5.1's table reports both the as-scored (34 junk + 23 unknown) and as-adjudicated (53 junk + 4 unknown) reads. |

Every revision must keep `--check` green and must record its reason here, because
the gold is the reference: quiet edits to it move every number ever published
against it.

| 1.3 | 2026-11-11 | **+24** grounded junk entries from the salvage arm's queue adjudication (`J166`-`J189`: 11 `abstract-concept`, 6 `metric`, 2 `role-class`, 2 `public-perception`, 1 `generic-mass`, 1 `content-artifact`, 1 `content-format`, 1 `out-of-ontology-type` — `图书馆`); **0** tier changes; `--check` green; a scorer robustness fix in `scratch/gold_score.py` (attribute-support lookup by the matched node's own name, not the gold's — a match on an alias surface form crashed it) | **Adjudication of the second quality arm's review queue (salvage, §5.2).** Same rule as v1.2: every addition is rule-justified and re-grounded; six ruled items were **not added** because their surface forms occur nowhere in the slice (`Dr. Osei` / `Nisha` — named persons the slice never contains; `政府监管` — the v1.0.1 precedent; `武汉大学品牌声量与影响力分析` / `AAN` / `政府机构` — ungroundable concatenations or absent terms). §5.2 reports both the as-scored (46 junk + 30 unknown) and as-adjudicated (70 junk + 6 unknown) reads. |
| 1.4 | 2026-11-11 | **+12** grounded junk entries from the defect arm's queue adjudication (`J193`-`J204` minus the checker's rejects: dates `2023`/`2025`/`本监测周期`, abstract themes `程序正义`/`治理现代化`/`信息透明度`/`对话机制`, role-class `媒体报道`, event-class `负面事件`/`正面热点`/`二次舆情反弹`, pattern `W型情感演变轨迹` once re-attributed, classification `985高校`, perception `正向情感`); **0** tier changes; **0** gold/borderline/edge changes; `--check` green; the confirmation repeat's and this arm's queue reports both recorded | **Adjudication of the third quality arm's review queue (defect salvage, §5.3).** Thirteen ruled items were **not added** — the `--check` grounding gate rejected nine (`微信`, `珑珈裂痕`, `社会媒体`, `媒体Outlet`, `透明共治`, `二次舆情爆发的催化剂`, `5起+重大舆情事件`, `舆情监测`, `声誉危机`, `W型情感演变轨迹` — the model's paraphrases occurring nowhere in the slice) and four are the hallucinated-person/known-precedent names (`Dr. Osei`, `Nisha's dad`, `Osei's colleague`, `政府监管`). Recorded for the audit trail: `微信` is a real platform but never appears in this slice, so it is not a gold `MediaOutlet` **here** — the gold annotates this slice, not the world. |
