# Graph Build bake-off — protocol + the Graphiti baseline

> **Date:** 2026-11-07 (issue #36, spec #30, parent #20). **Method note:** every
> number below is copied from a run log in `scratch/bakeoff/` (git-ignored by
> design) or from the machine-readable `.json` summary beside it; the harness
> that produced each run is named so anyone can re-measure. No figure below is
> estimated or carried over from another machine. Verdicts on the five fixed
> queries are the implementer's judgement against the recorded expected facts,
> with the answer line quoted — no vibes-only scoring.

Environment for all runs: macOS Apple Silicon, 16 GB, `GRAPH_BACKEND=graphiti`
(local Graphiti + embedded Kuzu + local models, no cloud), MLX in-process via
`mlx-vlm` in `backend/.venv`. The local small model and the local embedder are
the only two artefacts involved; nothing was downloaded (0 `download` lines in
either backend log). Ollama is stopped and asserted closed before and after
every run.

## 1. Purpose — the owner's question

Spec #30 / `HANDOFF.md` §4 record the owner's go-ahead for the bake-off
(execution tickets #36 → #37 → #38 → #39 → #40). The bake-off exists to answer one
question, in the owner's words:

> *"will changing from graphiti to cognee or semantica improve the speed of the
> graph build?"*

So this ticket fixes the **shared conditions** every candidate is measured under
(§2–§4) and delivers the **Graphiti baseline** (§5) that #37 (Cognee) and #38
(Semantica) are compared against. The decision is recorded here plus an ADR
either way — "kept Graphiti because X" is a decision too.

## 2. The identical document (fixed contract)

An extract of the owner's acceptance document, **not the whole 25 pages**: the
whole document chunks to 53 pieces, and #36's own estimate is that three such
runs would cost hours each. The acceptance criterion asks for a sha256-recorded
extract.

| Field | Value |
|---|---|
| Source PDF | `/Users/chenjinshen/Downloads/Prediction/武汉大学品牌声誉深度分析报告.pdf` (25 pages, 2 893 754 bytes) |
| PDF sha256 | `96b600dde44afaf041ed2d9f9265843517e069b104c603080664934c2f63e877` |
| Extraction method | the app's own path: `backend/app/utils/file_parser.py` → `FileParser.extract_text` (PyMuPDF/fitz `page.get_text`, pages joined by `\n\n`), then `TextProcessor.preprocess_text` (CRLF normalise, blank-line squeeze, per-line strip), then `split_text_into_chunks` |
| Chunker settings | `chunk_size=500`, `overlap=50` — exactly `Config.DEFAULT_CHUNK_SIZE` / `Config.DEFAULT_CHUNK_OVERLAP`, the pipeline's defaults |
| Chunks in the whole document | 53 (whole preprocessed text: 20 833 chars; raw `extract_text`: 20 858 chars, sha256 `dc5ca124d51f514d07661550fda5dc2a42c1cf6b0bfc2d081c571bb5588fe507`) |
| **Chunks taken** | **the first 8** (indices 0–7), i.e. the report's §1.0 summary through §2.1 volume trend |
| **Total chars** | **3 375** |
| **Extract sha256** | **`31d125a5c983ae3836cef6bad3b821a239127b46d51ac6aaef1765a66c30c00a`** |
| Extract file | `scratch/bakeoff/ident_doc_extract.txt` (UTF-8) |
| Chunk list (ingest input) | `scratch/bakeoff/ident_doc_extract.chunks.json` (per-chunk text, chars, sha256) |

Per-chunk sha256 (the harness re-verifies all of them at start-up, so a run cannot
drift from this contract):

| idx | chars | sha256 |
|---|---|---|
| 0 | 463 | `69b2fb59d4844bff2460e1cb4efdc3348af80d5db1c2486eca29fea8aeef9a2a` |
| 1 | 218 | `9713fa1f007cfbc16254edc09fe9062a9c74a6fe07e12f819fe3066d32c461cc` |
| 2 | 460 | `6586199c45663b3360c75de61a62c658fea6f6ac706163b4070c68e56723ebaa` |
| 3 | 414 | `31f6cebfdb512d453b039e03da13a003a131249f1611f1480e3f76bcec31fc01` |
| 4 | 455 | `df8fb9d8c1782ce51ac85be7f6438ab24d7c4ba0899b8f3522bbb82331ec7d6d` |
| 5 | 426 | `3b7c3580391b668bb62889383beb15b42c93d9e1aa210e0a0e581bb44780b02b` |
| 6 | 444 | `9a037c94cd306c69d04528dc0ddadaace235c9d4be366da7e5ccb6bd7d922d80` |
| 7 | 495 | `3b877a199a35ef3470eedc0b9cd21757cb483fd8719c04227f4d020580be5124` |

The PDF and the extract are **never committed** (`scratch/` is git-ignored;
`git status` stayed clean across this ticket apart from this file).

Graphiti runs these chunks through the shipped ontology of the acceptance run
(`Person` / `Organization` / `Location`, bare models, no edge types — persisted
to `backend/data/graphiti/<graph_id>/ontology.json`, byte-identical to
`mirofish_d02bf2e34d9b48f1/ontology.json`), so the baseline introduces no new
ontology decision. Candidates without a Zep-style ontology step run their own
native ingest path; #37/#38 must record what they ran.

## 3. The protocol — shared conditions for all three candidates

1. **One identical document per candidate.** The same 8 sha256-verified chunks,
   same order, same `chunk_size=500` / `overlap=50`. No candidate may re-chunk,
   re-order, truncate or "clean" the extract.
2. **Isolated venvs for Cognee and Semantica — never the project venv.** The
   candidate trees already exist at the repo's parent dir: `Prediction/cognee`
   (Apache-2.0, 396 MB) and `Prediction/semantica` (MIT, 90 MB), with
   `Prediction/graphiti` (40 MB) as the incumbent source reference. `backend/.venv`
   is off-limits to them; the incumbent (Graphiti) is measured in the project venv
   because that is where it ships.
3. **Artifacts only from the shared stores.** `Omnidroids/models` (weights) and
   `Omnidroids/.cache/hf` (`HF_HOME`, confined — the launcher exports it). **No
   downloads in this ticket or in #37/#38** (neither explicitly requires a new
   artefact). A candidate that wants an extra artefact stops and reports.
4. **One shared LLM + one shared embedder for every candidate.**
   - LLM: the local small model **`LFM3-1.7B-MLX-4bit`** (the incumbent `small`
     role, `models/registry.json`), selected with `GRAPHITI_LLM_MODEL=<name>`.
     A shell `LLM_MODEL_NAME` is **clobbered by `.env`** (`load_dotenv(override=True)`,
     `MODEL_EVAL.md` §7c) — never use it.
   - Embedder: **`Snowflake/snowflake-arctic-embed-s`**, 384-d, local
     sentence-transformers, already configured (`GRAPHITI_EMBEDDING_DIM=384`).
5. **Ollama closed and asserted closed before every run** (and after). The
   harness refuses to run if `127.0.0.1:11434` answers.
6. **A FRESH graph id per run.** No leftovers, no resume of a previous
   candidate's graph. Graphiti baseline ids: `bakeoff_graphiti_base_<epoch>`.
7. **M1/M2/M3 untouched in every path.** `_CappedLLMClient`'s 4096-token leaf
   clamp, `_TruncatedJSONError`'s fail-fast and `_ingest_record`'s per-chunk skip
   + batch-succeeded terminator (`backend/app/utils/graphiti_zep.py`) are measured
   as shipped. Nobody relaxes them to make a candidate look fast; a candidate that
   cannot live with them records that as its result.
8. **One model resident at a time**; hard unload afterwards (`mem_check` rule).
9. **SIGTERM only — never `kill -9`** (Kuzu WAL → SIGBUS on the next open).
10. **A winner must sit behind Omnidroids' graph interface** (`client.graph.*` /
    `client.batch.*`), never leaking its SDK into services.

## 4. The five fixed queries (identical for all three candidates)

Defined **before** any run, from the recorded extract, with 2–4 expected key
facts each. The same five queries and the same facts are the contract for #37 and
#38; each candidate reports its own answer text so the verdicts can be re-judged.

| id | query | expected key facts |
|---|---|---|
| Q1 | 武汉大学的舆情危机从单一事件演变成了什么性质的质疑？涉及哪些方面？ | (1) 从单一的后勤服务或个体冲突事件，演变为对其整体治理能力、程序正义、学术诚信乃至大学精神的系统性质疑 · (2) 公众情感从初期的震惊、愤怒，演变为深度的失望、嘲讽乃至羞耻感，形成“985滤镜破碎”的集体认知 · (3) 校方行政逻辑优先的应对模式僵化迟缓，未平息风波反而屡次成为二次舆情爆发的催化剂，暴露“本领恐慌”与系统性短板 |
| Q2 | 武汉大学五大热点事件的全平台总阅读量是多少？相当于过去五年的多少倍？ | (1) 五大热点事件累计产生超过28.7亿次的全平台阅读量 · (2) 相当于过去五年相关话题阅读量总和的1.9倍 · (3) 监测周期为2023年9月至2025年8月 |
| Q3 | 甲醛宿舍事件期间公众的情感是如何演变的？ | (1) 初始是恐惧和焦虑，校方发布“合格”通报后迅速转化为强烈的愤怒 · (2) 第三方检测报告“实锤”后，愤怒与恐惧情绪交织达到顶峰 · (3) 整体呈现“W型”情感演变轨迹，是官方回应失效、公信力丧失的直接体现 |
| Q4 | 微博在武汉大学舆情传播中扮演什么角色？产生了多少个热搜词条？ | (1) 微博是舆情首发和引爆的主阵地，贡献了68%的首发声量 · (2) 本周期内共产生47个相关热搜词条，其中43次登上“热搜第一” · (3) 热搜“通胀”严重：极端修辞驱逐中性描述，社会议题突围成本激增183% · (4) 至少7个热搜包被证实为付费推广 |
| Q5 | 小红书和抖音在武汉大学后勤类舆情的曝光与放大中分别起了什么作用？ | (1) 小红书成为Z世代特别是女性用户“维权”和“避坑”的主要阵地，鼠患、甲醛等后勤问题均首曝于此 · (2) 小红书图文并茂、模板化传播，如“武大宿舍改造模板”“高考志愿避坑指南”被大量复制和二次创作 · (3) 抖音/短视频贡献了67%的视频类声量，负面舆情的视觉再生产平均延迟仅2.1小时，传播比纯文本快3-5倍 |

**How retrieval is called:** through the seam's own search (`client.graph.search`,
`scope="edges"`, `limit=10`) — the same call `zep_tools` makes. Latency is the
wall time of that call; the full answer text (every returned fact) is saved to the
run's `.queries.json`.

**Verdict rule** (applied by the implementer, quoted below, so a reviewer can
re-judge):
* **relevant** — the returned facts answer the query and at least one expected key
  fact is matched verbatim or in substance;
* **partial** — the query's subject is addressed by the returned facts but none of
  the expected key facts is matched;
* **irrelevant** — no expected key fact is matched and the returned facts do not
  address the query's subject.

## 5. Metrics (definitions)

| Metric | Definition |
|---|---|
| **Wall time** | from `batch.process` to the batch's terminal state, as the pipeline experiences it (`_ingest_record` start → `_wait_for_batch` return) |
| **LLM calls per chunk** | calls counted at the capped client's `generate_response` entry — **calls, not tokens** — attributed to a chunk by the batch record's per-chunk save boundaries (100 ms polling). Attempts (tenacity re-entries of `_generate_response`) are counted separately, so `retries = attempts − calls` |
| **Graph quality** | node count + edge count read from the graph, plus entity-label counts and a sample of entity names/edge fact types |
| **Peak RSS** | psutil thread sampling this process (embedder + Kuzu) and the backend child (the served model) at 100 ms; reported as `self_mb` / `backend_mb` / `total_mb` |
| **Retrieval latency + relevance** | per query: wall time of the seam's search, and the verdict rule of §4 |
| **Crash-resume** | SIGTERM mid-ingest → restart → the batch stays non-terminal on disk with its unfinished items still `pending`, and `_ensure_ingestion` re-enters it on the next read (the boot-reconciliation path is what marks the *task* `interrupted`) → resume completes. SIGTERM only, never `-9` |
| **JSON-with-local-LM reliability** | parse failures (`json.JSONDecodeError` out of the leaf), truncation fail-fasts (`_TruncatedJSONError`, M3 — not retried by design) and retry counts, read off the seam's own error classes |

## 6. The Graphiti baseline (measured)

Two complete runs of the identical document exist. **Run 2 is the baseline of
record** (full per-chunk attribution, 9/9 monitor boundaries, the seam's own
WARNING stream teed into its log). Run 1 corroborates it.

| | run 2 (baseline of record) | run 1 (corroboration) |
|---|---|---|
| log | `scratch/bakeoff/baseline_graphiti_1794046080.log` | `scratch/bakeoff/baseline_graphiti_1794044987.log` |
| summary | `…1794046080.json` | `…1794044987.json` |
| graph id | `bakeoff_graphiti_base_1794046080` | `bakeoff_graphiti_base_1794044987` |
| batch | `local_1a091163ca574b0d` (op `59ecb03ff3317015…`) | `local_61d62fa25666427b` (op `a90f869d601c679e…`) |
| **wall time** | **1 032.4 s (17.2 min)** | 752.4 s (12.5 min) |
| chunks ok / skipped | **2 / 6** (indices 1, 2, 4, 5, 6, 7) | 2 / 6 (indices 0, 1, 2, 5, 6, 7) |
| **skip rate** | **75 % — all 6 of one class: `malformed-json`, i.e. M3's `_TruncatedJSONError` after M1's 4096-token leaf clamp** | 75 % (5 × `malformed-json` + 1 × `other-validation`) |
| LLM calls / attempts / retries | **47 / 47 / 0** | 42 / 44 / 2 |
| LLM seconds | 1 509.1 (concurrent: `GRAPHITI_MAX_COROUTINES=2`) | 1 033.1 |
| tokens (prompt / completion) | 103 453 / 80 198 | 87 903 / 55 935 |
| JSON parse failures / truncation fail-fast | 0 / **6** | 2 / 5 |
| **peak RSS** | **self 1 103.2 MB + backend 1 750.4 MB = 2 853.5 MB** (`ingest.peak_rss` in `…1794046080.json`, 9 061 samples at 100 ms) | self 1 086.5 + backend 1 767.6 = 2 854.2 MB |
| **graph quality** | **40 nodes / 47 edges**, labels `Entity` 40, `Person` 9, `Organization` 3, `Location` 1 | 31 nodes / 20 edges, labels `Entity` 31, `Person` 6, `Organization` 2, `Location` 1 |
| model served | `LFM3-1.7B-MLX-4bit` (registry `small` role; `GRAPHITI_LLM_MODEL` MATCH) | same |

Per chunk, run 2 (the baseline):

| idx | chars | status | class | wall s | LLM calls |
|---|---|---|---|---|---|
| 0 | 463 | succeeded | – | 57.5 | 3 |
| 1 | 218 | skipped | `malformed-json` (M3 truncation) | 48.3 | 1 |
| 2 | 460 | skipped | `malformed-json` (M3 truncation) | 74.8 | 3 |
| 3 | 414 | succeeded | – | 587.6 | 28 |
| 4 | 455 | skipped | `malformed-json` (M3 truncation) | 66.6 | 3 |
| 5 | 426 | skipped | `malformed-json` (M3 truncation) | 60.1 | 3 |
| 6 | 444 | skipped | `malformed-json` (M3 truncation) | 62.9 | 3 |
| 7 | 495 | skipped | `malformed-json` (M3 truncation) | 71.7 | 3 |

LLM calls by prompt (run 2): `dedupe_edges.resolve_edge` 24 ·
`dedupe_nodes.nodes` 6 · `extract_edges.edge` 7 · `extract_nodes.extract_text` 8 ·
`extract_nodes.extract_summaries_batch` 2 — i.e. by response model
`EdgeDuplicate` 24, `ExtractedEntities` 8, `ExtractedEdges` 7,
`NodeResolutions` 6, `SummarizedEntities` 2.

**The batch is `succeeded` and the build still fails.** M2's fault-tolerant
terminator keeps any batch with ≥1 succeeded chunk terminal-succeeded, so the
record reads `status=succeeded, done=2, skipped=6, error="skipped 6/8 chunks:
[1, 2, 4, 5, 6, 7]"`; the service's own gate then rejects it:

    RuntimeError: Zep batch local_1a091163ca574b0d: skipped 6/8 chunks
    (fraction 75% exceeds limit 30%); skipped_indices=[1, 2, 4, 5, 6, 7]

i.e. the real step-1 pipeline would abort this ingestion, not ship a 2/8 graph.
That is the honest incumbent number and the reason the bake-off exists. A third
run was started and aborted mid-ingest by the run-level deadline; it is not
evidence and is not cited.

### Retrieval on the five fixed queries (run 2, `scope="edges"`, `limit=10`)

Only chunks 0 and 3 landed in the graph, so the 47 edges come from §1.0/§1.1
(the summary) and §1.3 (the conclusions). Read the verdicts with that in mind —
it is the incumbent's coverage, not a retrieval bug.

| id | latency | facts | verdict | matched facts | quoted answer line |
|---|---|---|---|---|---|
| Q1 | 0.05 s | 10 | **relevant** | Q1-F1 (all four aspects) | "武汉大学的舆情危机已从单一的后勤服务或个体冲突事件，演变为对其整体治理能力、程序正义、学术诚信乃至大学精神的系统性质疑。" (answers #3, #4, #6) |
| Q2 | 0.03 s | 10 | **partial** | none of Q2-F1/F2/F3 | "武汉大学的品牌声誉在监测周期内呈现出显著的“声望悖论”与“周期性断裂”特征。" (#3/#4) — the 28.7亿次 / 1.9× facts live in chunk 4, which was skipped; "2023至2025年" survives only as a node label, never as a returned fact |
| Q3 | 0.03 s | 10 | **irrelevant** | none | "校友是舆情场的高度极化和碎片化参与者之一。" (#1) — no returned fact mentions 甲醛 / 合格通报 / 第三方检测 / W型 (source chunk 5 skipped) |
| Q4 | 0.03 s | 10 | **irrelevant** | none | "校方与学生的对立是舆情场的主要对立。" (#8–#10) — no 微博 / 68% / 47个热搜 / 183% / 7个付费热搜包 (source chunks 6–7 skipped) |
| Q5 | 0.03 s | 10 | **irrelevant** | none | "武汉大学的舆情危机已从单一…系统性质疑。" (#1/#2/#5) — no 小红书 / 抖音 / 2.1小时 / 3-5倍 (source chunk 7 skipped) |

Full answer text for every query: `scratch/bakeoff/baseline_graphiti_1794046080.queries.json`
(run 1: `…1794044987.queries.json`).

## 7. What the baseline does NOT yet demonstrate

* **Crash-resume** (§5) was not measured in #36 — no SIGTERM-mid-ingest → restart
  → resume cycle was run on the identical document. What exists today is design
  evidence only: `_ensure_ingestion` re-enters a non-terminal batch from its
  on-disk record, and the §12.4 acceptance run's batch
  (`mirofish_d02bf2e34d9b48f1/batches/local_a84bf4218fe24790.json`, items 6–18
  carrying "cannot schedule new futures after shutdown") is a real SIGTERM
  artifact of that kind. **Where it is covered:** #37 and #38 must measure
  crash-resume for their candidates under the same procedure; a Graphiti
  re-measurement needs a resume entry point in the harness (the harness has no
  `--resume-batch`; building one was out of #36's scope).
* **End-to-end step-1 success.** The baseline measures the incumbent's *real*
  behaviour on this extract — a 75 % chunk-skip rate — so "Graphiti built a graph
  in 17 min" is **not** a claim this doc makes: 2 of 8 chunks landed and the
  service's skip gate rejected the batch.
* **Determinism.** `temperature=0.0` is dropped by the MLX runner (checkpoint
  sampler applies), so every number above is a sample, not a constant; the two
  runs above differ in which chunks failed and by how much.
* **Small-model decision.** #36 does not re-open the `small`-role assignment
  (owner decision, spec #30): the bake-off is about the **backend**, with the same
  local small model for every candidate.

## 8. Evidence logs (all git-ignored, all in the main tree)

| Artefact | Path |
|---|---|
| identical document (extract) | `scratch/bakeoff/ident_doc_extract.txt` |
| identical document (chunks + sha256) | `scratch/bakeoff/ident_doc_extract.chunks.json` |
| harness | `scratch/bakeoff/bakeoff_baseline.py` |
| **baseline run 2 log** | `scratch/bakeoff/baseline_graphiti_1794046080.log` |
| baseline run 2 summary (all numbers) | `scratch/bakeoff/baseline_graphiti_1794046080.json` |
| baseline run 2 backend log (model load/unload) | `scratch/bakeoff/baseline_graphiti_1794046080.backend.log` |
| baseline run 2 query answers | `scratch/bakeoff/baseline_graphiti_1794046080.queries.json` |
| corroborating run 1 (log / json / queries / backend) | `scratch/bakeoff/baseline_graphiti_1794044987.{log,json,queries.json,backend.log}` |
| the seam's own dated log (raw LLM bodies + skips, both runs) | `backend/logs/2026-11-07.log` |
| the graphs themselves (Kuzu + batch records) | `backend/data/graphiti/bakeoff_graphiti_base_{1794046080,1794044987}/` |

**Traceability caveat (honest accounting):** the call counts in the tables above
come from the harness's JSON summaries, which count every entry into the capped
client. Run 2's tee captured 45 of the seam's 47 `graphiti LLM response`
WARNING lines — an `id()`-keyed de-dup in the tee handler silently dropped two
(they are all present in the seam's own dated log, whose file handler is not
affected). The harness was corrected afterwards (a per-record marker instead of
`id()`); the counts and every number in §6 are unaffected.

Run 2's backend log records one model resident and a clean hard unload:

    [18:08:10] INFO: MODEL LOADED LFM3-1.7B-MLX-4bit [mlx] rss_before=180MB rss_after=1696MB est=1087MB
    [18:25:14] INFO: MODEL UNLOADED LFM3-1.7B-MLX-4bit rss_before=1714MB rss_after=1714MB

## 9. Reproduce

```bash
# Graphiti baseline (Ollama must be stopped; assert :11434 closed first)
cd Omnidroids/backend
GRAPHITI_LLM_MODEL=LFM3-1.7B-MLX-4bit \
  .venv/bin/python ../scratch/bakeoff/bakeoff_baseline.py
# → scratch/bakeoff/baseline_graphiti_<epoch>.{log,json,queries.json,backend.log}
```

The harness re-verifies the extract's sha256 and every chunk's sha256 at
start-up, asserts Ollama is closed, refuses a non-fresh graph id, drives the
shipped `GraphBuilderService` (create → ontology → `add_text_batches` →
`_wait_for_batch`) over the real `GraphitiZepClient`, and only *observes*
`_CappedLLMClient` — it patches no seam behaviour. `#37` (Cognee) and `#38`
(Semantica) re-run §3's conditions with their own isolated venvs and report the
§5 metrics on this same document and the same five queries.
