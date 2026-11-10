# Gold standard — `abfix_slice8` (HANDOFF §9 G item 1)

**Status:** frozen, self-checked, rescored against two frozen runs, and
cross-checked by an independent context-isolated adjudication that agreed on every
numbered decision (§6). **Two things still gate its use as a standard:** the owner's
sign-off, and a different-model second pass (the child registry only exposes this
session's model).

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

Every revision must keep `--check` green and must record its reason here, because
the gold is the reference: quiet edits to it move every number ever published
against it.
