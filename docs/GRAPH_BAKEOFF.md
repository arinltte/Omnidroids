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

### 6b. Cognee (#37) — measured on the same document, in an isolated venv

Harness: `scratch/bakeoff/bakeoff_baseline.py`'s patterns re-implemented for
cognee in **`scratch/bakeoff/bakeoff_cognee.py`** (git-ignored) — same Ollama
assertion, same sha256 re-verification at start-up, same free-port backend child
with SIGTERM-only cleanup, same 100 ms psutil RSS sampling, tee'd log. Two
complete runs of the identical document exist. **Run 2 is the run of record**
(ingest completed, full graph); run 1 corroborates it and records the failure
class that made the second run necessary. Both ran on `phase6-integration` @
`513fb2c`.

**Install row.**

| Field | Value |
|---|---|
| venv | `Prediction/cognee/.venv-bakeoff` — Python **3.11.15**, created with `uv venv --python 3.11 .venv-bakeoff`; the project venv is never touched |
| install command | `uv pip install -e . --python .venv-bakeoff/bin/python` (**170 packages**, `cognee 1.6.6` installed from the tree in place) + `uv pip install psutil --python .venv-bakeoff/bin/python` (for §5's peak-RSS sampler; cognee does not depend on it) |
| resolved versions | `cognee 1.6.6` (local tree) · `litellm 1.108.0` · `openai 2.54.0` · `fastembed 0.9.1` · `ladybug 0.19.0` (the embedded graph store, the Kuzu fork) · `lancedb 0.39.0` / `pylance 12.0.0` (vector store) · `onnxruntime 1.31.1` · `pandas 2.3.3` · `dlt 1.30.0` · `enola-cli 0.4.30` · `sqlalchemy 2.0.57` · `psutil 7.2.2` |
| downloads | **0** — `HF_HUB_OFFLINE=1` for the whole run; the embedder loads from the shared `.cache/hf` (`snowflake/snowflake-arctic-embed-s`, 0.28 s) and the model is served by the project backend out of `Omnidroids/models` |
| Ollama | closed and asserted closed before and after every run (`:11434` refuses) |

**How the LLM and the embedder were wired (the crux).** Cognee resolves exactly
one settings file (`COGNEE_ENV_FILE` → cwd-and-parents → package dir) and loads it
with `override=True`, so the harness writes a per-run file and names it
(`…/cognee_1794056509.json` → `env_file`):

    LLM_PROVIDER=openai
    LLM_MODEL=openai/LFM3-1.7B-MLX-4bit      # litellm's routing prefix, not a cloud route
    LLM_ENDPOINT=http://127.0.0.1:<port>/v1   # this run's project-backend /v1 proxy
    LLM_API_KEY=local-not-a-cloud-key        # the proxy is local and key-less
    EMBEDDING_PROVIDER=fastembed
    EMBEDDING_MODEL=snowflake/snowflake-arctic-embed-s
    EMBEDDING_DIMENSIONS=384
    GRAPH_DATABASE_PROVIDER=ladybug   VECTOR_DATABASE_PROVIDER=lancedb

Two workarounds were needed, both recorded because a reviewer would otherwise
repeat them:

1. **Cognee's `.env` search finds this repo's own `Omnidroids/.env`** (the harness
   runs from `scratch/bakeoff`, and the search walks up to the project root) and
   loads it with `override=True`. That file carries `LLM_API_KEY`, `LLM_BASE_URL`
   and `LLM_MODEL_NAME` — exactly the keys that would silently re-point cognee at
   a cloud endpoint. `COGNEE_ENV_FILE` is therefore mandatory, not cosmetic.
2. **litellm refuses a bare model name** — the first smoke attempt failed with
   `litellm.BadRequestError: LLM Provider NOT provided. You passed
   model=LFM3-1.7B-MLX-4bit`, so `LLM_MODEL` carries the `openai/` prefix. The
   model that actually serves is still the registry `small` model: each run's
   warm-up completion returns `model=LFM3-1.7B-MLX-4bit` (2.2 s) and the backend
   log records `INFO: MODEL LOADED LFM3-1.7B-MLX-4bit [mlx]` … `INFO: MODEL
   UNLOADED LFM3-1.7B-MLX-4bit` once per run.

Everything else is cognee's own path: `cognee.add()` per verified chunk (in the
recorded order, one document per chunk), then one `cognee.cognify([dataset],
chunk_size=500)`; `chunk_size=500` keeps every chunk inside arctic's 512-token
window and inside cognee's own paragraph chunker, which the harness then verifies
did not re-chunk.

| | run 2 — **run of record** | run 1 — corroboration |
|---|---|---|
| log / summary / queries | `cognee_1794056509.log` / `.json` / `.queries.json` | `cognee_1794055410.{log,json,queries.json}` |
| dataset / store | `bakeoff_cognee_1794056509` / `../cognee/.bakeoff_store_1794056509/` | `bakeoff_cognee_1794055410` / `…store_1794055410/` |
| **install + smoke** | install above; smoke = 1 chunk through the same API (`cognee_1794054357.json`): add 1.3 s, cognify 198.4 s, 4 LLM calls / 13 prompts, `SearchType.GRAPH_COMPLETION` answer in 22.2 s. (Earlier smoke attempts in the same session cost 15–28 s of cognify — the spread is the 1.7B's JSON variance again.) | same |
| **wall time** | **229.9 s** (add 1.5 s + cognify 228.5 s) | 533.8 s — aborted by the terminal failure |
| chunks ingested verbatim | **8 / 8** (`no_rechunking: chunk_nodes 8, expected 8, verbatim 8` — each stored `DocumentChunk` text equals the sha256-verified chunk) | **8 / 8** (identical) |
| chunk-skip rate | **0 %** — no chunk is ever skipped; a per-chunk failure fails the whole pipeline instead | 0 % skipped, **but the extraction stage never finished** |
| LLM calls / attempts / retries | **9 / 10 / 1** (8 × `KnowledgeGraph` + 1 × `str` connection test) | **9 / 13 / 4** |
| provider round trips | **13** (`litellm.acompletion` prompts) | **29** |
| LLM seconds | 910.0 (concurrent calls summed) | 2 769.8 |
| tokens (prompt / completion) | 22 865 / 16 785 | 54 122 / 41 513 |
| JSON parse failures | 1 logged `validation retry 1/3` for `KnowledgeGraph`; **0 terminal failures** | 8 logged validation retries **+ 1 terminal `ValidationError`** |
| **peak RSS** | **self 774.1 MB + backend 2 126.4 MB = 2 900.6 MB** (2 005 samples) | self 772.0 + backend 2 122.0 = **2 893.8 MB** (4 821 samples) |
| **graph quality** | **105 nodes / 222 edges** — `Entity` 63, `EntityType` 10, `Timestamp` 8, `TextSummary` 8, `DocumentChunk` 8, `TextDocument` 8; edges `contains` 80, `is_a` 64, `made_from` 8, `is_part_of` 8 + 42 distinct semantic relations (`causes`, `manifests_in`, `diffusion_chain`, `has_impact_index`, `plays_role_of`, …) | **16 nodes / 8 edges** — `DocumentChunk` 8, `TextDocument` 8, `is_part_of` 8; **0 entities, 0 entity edges** |
| model served | `LFM3-1.7B-MLX-4bit` (registry `small`; one resident model, clean unload) | same |

**Run 1's terminal failure, verbatim** (the class that makes the second run
necessary — cognee exits 422 rather than skipping a chunk):

    CognifyFailedError: Cognify failed for dataset 'bakeoff_cognee_1794055410':
    ValidationError: 1 validation error for KnowledgeGraph
      Invalid JSON: trailing comma at line 131 column 5

That left only the indexing stage's 16 nodes on disk. Run 2 ran the identical
command to completion: the 1.7B's JSON validity, not the harness, decides which
run you get — the same variance class that caps the Graphiti baseline.

Per chunk, run 2 (extraction span inside the concurrent
`extract_graph_from_data` gather — the spans overlap and are not additive; the
calls column is exact):

| idx | sha256 prefix | LLM calls | extraction span s |
|---|---|---|---|
| 0 | `69b2fb59d484` | 1 | 110.9 |
| 1 | `9713fa1f007c` | 1 | 37.0 |
| 2 | `6586199c4566` | 1 | 79.8 |
| 3 | `31f6cebfdb51` | 1 | 62.5 |
| 4 | `df8fb9d8c178` | 1 | 125.1 |
| 5 | `3b7c3580391b` | 1 | 169.6 |
| 6 | `9a037c94cd30` | 1 | 98.8 |
| 7 | `3b877a199a35` | 1 | 226.1 |

### Retrieval on the five fixed queries (cognee, `SearchType.GRAPH_COMPLETION`, `top_k=10`)

Latency is the wall time of cognee's own `cognee.search()` call. Each call also
spends part of that latency on cognee's session-memory analysis, which in run 1's
log fails open after a 30 s timeout (`Concurrent turn analysis failed open:
exception_type=TimeoutError`) — that overhead is part of the measured latency, not
excluded from it. Run 2 (run of record):

| id | latency | facts | verdict | matched facts | quoted answer line |
|---|---|---|---|---|---|
| Q1 | 31.3 s | 1 | **relevant** | Q1-F1 | "武汉大学的舆情危机从单一事件演变为系统性质疑，主要涉及以下方面：1. **结构性失衡**：结构性失衡、程序正义缺位…" |
| Q2 | 31.2 s | 1 | **relevant** | Q2-F1, Q2-F2 | "五大热点事件的全平台总阅读量是 28.7 亿+，相比过去五年的总和是 190% 倍。" (the report's "↑190% (vs. 五年总和)" IS the 1.9× fact) |
| Q3 | 22.6 s | 1 | **partial** | none of Q3-F1/F2/F3 | "**关于：甲醛宿舍事件期间公众的情感是如何演变的？**… 1. **从"主阵地"到"热搜第一"的传播爆发**" — the subject is named, but no 恐惧/合格/愤怒/顶峰/W型 |
| Q4 | 31.4 s | 1 | **relevant** | Q4-F1, Q4-F2 | "**微博的角色**：…基于"热搜"和"热搜词条"的概念…""爆的主阵地，贡献了68%的首发声量"与"本周期内共产生47个相关热搜词 条"" |
| Q5 | 31.2 s | 1 | **relevant** | Q5-F1/F2/F3 | "**抖音/短视频平台**是负面舆情的核心传播渠道，贡献了**67%**的视频类声量。负面舆情的视觉再生产平均延迟为**2.1小时**，传播速度比纯文本快3-5倍。" (+ 小红书, 鼠患, 避坑, 模板) |

Full answer text for every query: `scratch/bakeoff/cognee_1794056509.queries.json`
(run 1: `…1794055410.queries.json`, whose 4 relevant / 1 irrelevant verdicts were
reached from chunk text because run 1's graph held no entities).

Note the cost: **22–31 s per query against the baseline's 0.03–0.05 s**, because
each cognee search runs its own LLM completion.

### Reliability and crash-resume (§5)

| Behaviour | What the measurement recorded |
|---|---|
| JSON-with-local-LM | Cognee has **no fail-fast truncation class**; `litellm_native` instead re-prompts with the validation error up to 3× per call. Run 2: 13 prompts for 9 logical calls, 1 logged `validation retry 1/3`, **0 terminal failures**. Run 1: 29 prompts for 9 logical calls, 8 logged retries, **1 terminal `ValidationError`** (`Invalid JSON: trailing comma…`) plus one `CancelledError` in the failure ledger. §5's definition adapted accordingly: the parse-failure count is cognee's own `validation retry` warnings in its dated log; the terminal failure is the `ValidationError` out of `acreate_structured_output`; there is nothing analogous to M3's `_TruncatedJSONError` |
| Crash-resume | Measured on a small fresh store (2 chunks, `--mode crash`, `cognee_1794055197`): SIGTERM at t+20 s mid-extraction → the child exits `-15` (signal honoured; no `-9` anywhere). The interrupted run stays **non-terminal on disk** — `pipeline_runs` keeps a `cognify_pipeline` row with `DATASET_PROCESSING_STARTED … end=None`. Re-running `cognify()` on the same dataset then: skips `indexing_pipeline` ("no item needs it (already processed)"), starts a **new** `cognify_pipeline` run, re-extracts both items ("entities and relationships extracted for 2 item(s), 0 already were") and completes. **Cognee has no resume entry point** — an interrupted ingest is redone, not resumed. `cognee/modules/cognify/recovery.py` reconciles abandoned runs only after `COGNEE_STALE_RUN_RECOVERY_MIN_AGE_SECONDS` (default 3600 s), which this test did not wait out |

**The honest verdict sentence.** On the identical document with the shared small
model and the shared embedder, **Cognee ingests all 8 chunks verbatim (no
re-chunking, no skips) and, when the 1.7B's JSON is valid, builds a real graph —
105 nodes / 222 edges in 230 s — and answers 4 of the 5 fixed queries correctly
at 22–31 s per query**, where the Graphiti baseline needed 1 032 s, skipped 6 of
8 chunks and answered 1 of 5. The same cognee command can also die mid-extraction
on a malformed-JSON chunk (run 1: 534 s, 0 entities), so its ingest is
**all-or-nothing rather than fault-tolerant**, and it answers from a mix of graph
and chunk context — #39 must weigh that against Graphiti's partial-but-guaranteed
graph, and against the 5-query latency gap.

### 6c. Semantica (#38) — measured on the same document, in an isolated venv

Harness: `scratch/bakeoff/bakeoff_baseline.py`'s patterns re-implemented for
semantica in **`scratch/bakeoff/bakeoff_semantica.py`** (git-ignored) — same Ollama
assertion, same sha256 re-verification at start-up, same free-port backend child
with SIGTERM-only cleanup, same 100 ms psutil RSS sampling, tee'd log. Modes
`smoke` / `run` / `crash` / `child`. Two complete runs of the identical document
exist. **Run 2 is the run of record** (verbatim-storage check, clean call
counters); run 1 corroborates it. Both ran on `phase6-integration` @ `4963ff8`,
branch `bakeoff/38-semantica-identical-doc`.

**Install row.**

| Field | Value |
|---|---|
| venv | `Prediction/semantica/.venv-bakeoff` — Python **3.11.15**, created with `uv venv --python 3.11 .venv-bakeoff`; the project venv and `backend/.venv` are never touched |
| install command | `uv pip install -e '.[llm-openai,embeddings-local]' --python .venv-bakeoff/bin/python` (**74 packages**, `semantica 0.8.0` installed from the tree in place) + `uv pip install psutil --python .venv-bakeoff/bin/python` (for §5's peak-RSS sampler; semantica does not depend on it) → **75 packages**, frozen verbatim to `scratch/bakeoff/semantica_venv_freeze.txt` |
| resolved versions | `semantica 0.8.0` (local tree) · `openai 3.46.0` · `sentence-transformers 6.2.0` · `fastembed 0.9.1` · `onnxruntime 1.31.1` · `tokenizers 0.23.3` · `transformers 5.22.2` · `torch 2.15.0` · `numpy 2.4.6` · `pandas 3.1.1` · `scikit-learn 1.9.1` · `scipy 1.17.1` · `rdflib 7.6.0` · `networkx 3.6.1` · `pydantic 2.14.3` · `huggingface-hub 1.33.0` · `grpcio 1.85.1` · `pyarrow 26.0.1` · `protobuf 7.36.2` · `pillow 13.0.0` · `psutil 7.2.2` |
| downloads | **0 model downloads** — `HF_HUB_OFFLINE=1` (and `TRANSFORMERS_OFFLINE=1`) for every run and for every crash child; the embedder loads from the shared `.cache/hf` (`Snowflake/snowflake-arctic-embed-s`, 0.12 s, 384-d) and the model is served by the project backend out of `Omnidroids/models`. Each run's backend log records **0 `download` lines**. PyPI wheels for the install itself were fetched once, before any run |
| Ollama | closed and asserted closed before and after every run, and inside every crash child (`:11434` refuses throughout) |

**How the LLM and the embedder were wired (the crux).** Semantica has its own
OpenAI-compatible provider (`OpenAIProvider` in
`semantica/semantic_extract/providers.py`), so the route is direct: the harness
hands `GraphBuilder` the provider, the model and a `base_url`, and semantica's own
provider pool records exactly what it resolved (read out of
`semantica.semantic_extract.providers._provider_pool` after the run):

    key ('openai', ('api_key','local-not-a-cloud-key'),
         ('base_url','http://127.0.0.1:65121/v1'),
         ('model','LFM3-1.7B-MLX-4bit'),
         ('ner_method','llm'), ('relation_method','llm'),
         ('triplet_method','llm'))
      → OpenAIProvider, model=LFM3-1.7B-MLX-4bit,
        base_url=http://127.0.0.1:65121/v1, api_key_is_local=True

The embedder is the shared arctic model through semantica's own
`EmbeddingGenerator`:

    from semantica.embeddings import EmbeddingGenerator
    from semantica.vector_store import VectorStore
    store = VectorStore(backend="inmemory", dimension=384)
    store.embedder = EmbeddingGenerator(method="sentence_transformers",
                                        model_name="Snowflake/snowflake-arctic-embed-s")

Five things a reviewer would otherwise repeat, all recorded:

1. **`VectorStore`'s default embedder is the wrong model and cannot load
   offline.** It builds `TextEmbedder(method="fastembed",
   model_name="BAAI/bge-small-en-v1.5")`; that model is not in the shared cache,
   so with `HF_HUB_OFFLINE=1` it fails — `Could not find the model tar.gz file at
   …/fastembed_cache/bge-small-en-v1.5 and local_files_only=True` → `Failed to load
   FastEmbed model 'BAAI/bge-small-en-v1.5' … Using fallback embedding method` —
   and silently degrades to a hash embedding. Replacing the store's embedder with
   the shared arctic model is therefore mandatory, not cosmetic.
2. **Semantica's own KG-analytics components hit the same default.**
   `ContextGraph`'s advanced-analytics block constructs its own
   `EmbeddingGenerator()` (`Node2Vec embeddings disabled: gensim is required…`,
   `Structural embeddings disabled: gensim is required…`, plus the fastembed
   failure above). Neither is on the measured path — the vector store and the
   graph reads use the shared embedder and the extracted graph — but the run's
   log carries those lines and they are not hidden.
3. **`instructor` is not installed** (it is not part of the `llm-openai` extra),
   so `generate_typed` takes its documented fallback: a repair loop of up to 3
   attempts that first calls `generate_structured` and then plain `generate` plus
   `_parse_json`. That is the measured JSON path; with `instructor` present the
   same call would go through `instructor.from_openai(mode=Mode.JSON)`.
4. **Cloud keys are scrubbed from the harness's own environment.** Semantica
   resolves an OpenAI key through `explicit kwarg → Config singleton →
   OPENAI_API_KEY env var`, and this repo's `.env` carries `LLM_API_KEY`,
   `LLM_BASE_URL` and `LLM_MODEL_NAME` — the same class of silent re-pointing #37
   had to defend against. The harness deletes those variables (and every other
   provider key) and pins `OPENAI_API_KEY=local-not-a-cloud-key`. In both runs the
   environment it inherited already had none of them (`scrubbed_env_keys: []`)
   and the pool record above shows `api_key_is_local=True`. Unlike cognee,
   **semantica has no `.env`/dotenv search of its own** (no `load_dotenv` or
   `find_dotenv` anywhere in the package), so there is no settings-file walk to
   trip over — the scrub is kept because the env-var fallback is the same risk.
5. **A base_url and no `response_format`.** Because `base_url` is set, semantica
   drops `response_format=json_object` from `generate_structured` (the project's
   /v1 proxy does not implement it) and asks for plain JSON instead; the warm-up
   completion returns `model=LFM3-1.7B-MLX-4bit` (2.2 s) and each run's backend
   log records one resident model and a clean unload:

       [00:41:33] INFO: MODEL LOADED LFM3-1.7B-MLX-4bit [mlx] rss_before=179MB rss_after=1696MB est=1087MB
       [00:52:28] INFO: MODEL UNLOADED LFM3-1.7B-MLX-4bit rss_before=1250MB rss_after=1250MB

Everything else is semantica's own path. **Ingest:** one
`GraphBuilder.build(chunk_text, ner_method="llm", relation_method="llm",
triplet_method="llm")` per verified chunk, in the recorded order — the chunk text
is passed as a single string source and `max_text_length` is left unset (provider
default 64 000 chars against 218–495-char chunks), so semantica's extractor never
re-chunks; the extracted entities/relationships are then handed to
`AgentContext.memory.store(chunk, entities=…, relationships=…)` — the same call
`AgentContext.store()` makes for a document — which embeds the chunk into the
vector store and adds the entities/relationships to the `ContextGraph`
(`VectorStore(backend="inmemory")`, no external service). **Retrieval:**
`AgentContext.retrieve(query, use_graph=True, expand_graph=True,
max_results=10)` — semantica's GraphRAG seam (vector leg + graph leg + memory
leg, ranked and merged).

| | run 2 — **run of record** | run 1 — corroboration |
|---|---|---|
| log / summary / queries | `semantica_1794069690.{log,json,queries.json}` | `semantica_1794068587.{log,json,queries.json}` |
| store (candidate tree) | `../semantica/.bakeoff_run_1794069690/` (`graph.json` + `checkpoint.json`) | `../semantica/.bakeoff_run_1794068587/` |
| **install + smoke** | install above; smoke = 1 chunk through the same API (`semantica_1794068164.json`): build 36.8 s, 15 entities / 8 relationships, 16 nodes / 8 edges, 5 queries at 0.042–0.051 s. (The same single chunk cost 56.6 s / 25 entities / 9 relationships as chunk 0 of the measured run, and 43.4 s / 19 entities / 10 relationships / 20 nodes / 10 edges as the crash test's first chunk — the spread is the 1.7B's variance again.) | same |
| **wall time** | **651.7 s (10.9 min)** | 465.4 s (7.8 min) |
| chunks ingested verbatim | **8 / 8** — `no_rechunking: verbatim 8/8`: the sha256 of each stored memory item's content equals the contract's per-chunk sha256, and `max_text_length` never fired | 8 / 8 accepted, 0 skipped; the verbatim sha256 check was added to the harness *after* run 1, so run 1 has no such measurement |
| chunk-skip rate | **0 %** — no chunk is skipped and no chunk aborts the run; a failure would have been recorded per chunk in the summary | 0 % (same) |
| LLM calls / prompts / retries | **29 / 35 / 6** (calls = adapter entries the extractors make: `EntitiesResponse` 8, `RelationsResponse` 8, `TripletsResponse` 8, `generate_structured` 5 — the relations fallback; prompts = `chat.completions.create` on the wire; `retries = prompts − calls`) | 27 adapter entries (23 typed + 4 `generate_structured`) / 32 prompts / 5 retries, recomputed from its recorded wire-level records (see the traceability note below) |
| LLM seconds | 536.7 (typed calls only) | 409.9 |
| tokens (prompt / completion) | 23 069 / 53 304 | 20 710 / 39 066 |
| JSON parse failures / terminal failures | **7 / 0** (6 of the 7 surfaced as `ProcessingError` out of the adapter entry and were recovered by the fallback path or the next attempt) | **7 / 0** |
| **peak RSS** | **self 653.9 MB + backend 1 748.1 MB = 2 401.8 MB** (5 769 samples at 100 ms) | self 662.2 + backend 1 766.6 = **2 428.8 MB** (4 052 samples) |
| **graph quality** | **118 nodes / 47 edges** — labels `entity` 32, `CONCEPT` 31, `UNKNOWN` 18, `EVENT` 12, `ORG` 11, `DATE` 7, `PRODUCT` 3, `GPE` 2, `PERSON` 2 | **122 nodes / 56 edges** — labels `UNKNOWN` 45, `entity` 31, `CONCEPT` 20, `ORG` 8, `EVENT` 8, `PRODUCT` 4, `GPE` 3, `DATE` 3 |
| model served | `LFM3-1.7B-MLX-4bit` (registry `small`; one resident model, clean unload) | same |

Sample of what the 1.7B actually built (run 2, semantica's own
`ContextGraph`):

    武汉大学 -has_brand_reputation-> 声誉基石
    武汉大学 -is_top985_university-> 顶尖985高校
    武汉大学 -has_cultural_symbol:樱花-> 强大文化符号
    武汉大学 -experiences_brand_reputation_volatility-> 声誉在短期内经历
    武汉大学 -is_systemic_criticism_target-> 整体治理能力、程
    单一的后勤服务或 -演变为-> 其整体治理能力、

…and, from the triplet extractor, a tail of RDF-ish predicates the 1.7B invented
(`http://example.org/...`, `http://example.org/related_to` ×2,
`http://example.org/contributed_68%25_of_first-source_voice_volume`) — **18 of run
2's 43 distinct edge types are such URIs** (19 of its 47 edges), which is honest
noise in the edge-type distribution and belongs in the graph-quality judgement;
run 1 had 15 URI edge types of 48.

Per chunk, run 2:

| idx | sha256 prefix | chars | status | wall s | LLM calls | entities | relationships |
|---|---|---|---|---|---|---|---|
| 0 | `69b2fb59d484` | 463 | succeeded | 56.6 | 4 | 25 | 9 |
| 1 | `9713fa1f007c` | 218 | succeeded | 71.1 | 3 | 5 | 2 |
| 2 | `6586199c4566` | 460 | succeeded | 58.3 | 3 | 3 | 1 |
| 3 | `31f6cebfdb51` | 414 | succeeded | 123.1 | 4 | 15 | 1 |
| 4 | `df8fb9d8c178` | 455 | succeeded | 110.6 | 4 | 9 | 7 |
| 5 | `3b7c3580391b` | 426 | succeeded | 88.6 | 4 | 20 | 11 |
| 6 | `9a037c94cd30` | 444 | succeeded | 59.7 | 3 | 14 | 15 |
| 7 | `3b877a199a35` | 495 | succeeded | 83.6 | 4 | 7 | 1 |

### Retrieval on the five fixed queries (semantica, `AgentContext.retrieve`, `max_results=10`)

Latency is the wall time of semantica's own `context.retrieve()` call — no LLM
completion happens inside it, which is why it is 0.2 s rather than cognee's 22–31 s.
**Read the verdicts with what the retrieval returns in mind:** the 9 "facts" are
whole chunk texts (semantica's memory items, i.e. the verbatim chunks), so with an
8-chunk corpus every query gets essentially the whole document ranked by
similarity — and the ranking is only weakly discriminative (top score 0.96–0.99,
the next five within 0.92–0.96, the tail at 0.66–0.68). The graph leg
was measured on its own for each query and returned **0 facts for all five**
(`ContextGraph.query` is a substring match on whitespace-split tokens, and these
queries are Chinese), so this is chunk recall, not graph reasoning. Run 2 (run of
record); run 1 returned the same facts in the same order at the same latencies.

| id | latency | facts | verdict | matched facts | quoted answer line |
|---|---|---|---|---|---|
| Q1 | 0.249 s | 9 | **relevant** | Q1-F1/F2/F3 | "单一的后勤服务或个体冲突事件，演变为对其整体治理能力、程序正义、学术诚信乃至大学精神的系统性质疑。…公众情感从初期的震惊、愤怒，迅速演变为深度的失望、嘲讽乃至羞耻感，形成了“985滤镜破碎”的集体认知" (rank 1, score 0.992) |
| Q2 | 0.231 s | 9 | **relevant** | Q2-F1, Q2-F2, Q2-F3 | "…累计产生了超过28.7亿次的全平台阅读量，这一数字相当于过去五年相关话题阅读量总和的1.9倍" (rank 4, score 0.953; rank 7, score 0.929 also carries "2023年9月至2025年8月") |
| Q3 | 0.214 s | 9 | **relevant** | Q3-F1, Q3-F2, Q3-F3 | "…初始的恐惧和焦虑在校方发布“合格”通报后，迅速转化为强烈的愤怒，并在第三方检测报告“实锤”后，愤怒与恐惧情绪交织达到顶峰" (rank 3, score 0.947; the “W型” fact at rank 6, score 0.932) |
| Q4 | 0.220 s | 9 | **relevant** | Q4-F1, Q4-F2, Q4-F3, Q4-F4 | "…的主阵地，贡献了68%的首发声量。本周期内共产生47个相关热搜词条，其中43次登上“热搜第一”。…社会议题突围成本激增183%。同时，商业购买痕迹明显，至少7个热搜包被证实为付费推广" (rank 5, score 0.934 — all four expected facts in one returned chunk) |
| Q5 | 0.213 s | 9 | **relevant** | Q5-F1, Q5-F2, Q5-F3 | "小红书：成为Z世代，特别是女性用户“维权”和“避坑”的主要阵地。鼠患、甲醛等后勤问题均首曝于此。…如“武大宿舍改造模板”、“高考志愿避坑指南”等笔记被大量复制和二次创作" and "抖音/短视频平台：…贡献了67%的视频类声量。负面舆情的视觉再生产平均延迟仅为2.1小时，传播速度比纯文本快3-5倍" (the same returned chunk, rank 5, score 0.935) |

Full answer text for every query: `scratch/bakeoff/semantica_1794069690.queries.json`
(run 1: `…1794068587.queries.json`).

### Reliability and crash-resume (§5)

| Behaviour | What the measurement recorded |
|---|---|
| JSON-with-local-LM | Semantica has **no truncation fail-fast class** and no fixed retry budget at the extractor level. With `instructor` absent, `generate_typed` runs its own repair loop (up to 3 attempts): `generate_structured` first (which, because `base_url` is set, omits `response_format=json_object`), then plain `generate` + `_parse_json`. Run 2: 29 adapter calls for 35 prompts on the wire, **7 `ProcessingError` parse failures** — 6 surfaced out of the adapter entry — all recovered, **0 terminal failures**, 0 chunks skipped, 0 provider failures. Run 1: the same 7 / 0. §5's definition adapted accordingly: the parse-failure count is semantica's own `_parse_json` raising (its warning line `generate_structured failed (Failed to parse JSON …); retrying with plain generate() + JSON parse.` is in the log); `retries = prompts − calls`; there is nothing analogous to M3's `_TruncatedJSONError` |
| Crash-resume | Measured on a small fresh store (2 chunks, `--mode crash`, `semantica_1794070896`): SIGTERM at **t+47.4 s** — as soon as the first of the two chunks was checkpointed, i.e. mid-extraction of the second (a fixed t+20 s would always land inside the first chunk, which takes 43–123 s here) → the child exits **-15** (signal honoured; no `-9` anywhere). What survived on disk: the harness's own `checkpoint.json` + `graph.json` holding **chunk 0's graph (20 nodes / 10 edges)**; chunk 1's in-flight extraction was lost entirely. The restart (`--resume`) reloads that JSON through semantica's own `load_from_file` (0/0 → 20/10), and **the harness — not semantica — decides that chunk 0 is done**; chunk 1 is re-extracted from zero (48.9 s, 2 adapter calls) and the store ends at 23 nodes / 12 edges, `chunks_done=[0, 1]`. **Semantica has no resume entry point**: no ingest record, no per-chunk progress, nothing to reconcile — an interrupted ingest is redone, not resumed, and the only reason anything survived is that the caller saved a graph file |

**The honest verdict sentence.** On the identical document with the shared small
model and the shared embedder, **Semantica ingests all 8 chunks verbatim (no
re-chunking, no skips) and builds a real graph — 118 nodes / 47 edges in 652 s —
and answers 5 of the 5 fixed queries correctly at 0.21–0.25 s each**, where the
Graphiti baseline needed 1 032 s, skipped 6 of 8 chunks and answered 1 of 5, and
Cognee needed 230 s, skipped 0 and answered 4 of 5 at 22–31 s each. But the 5/5
is **chunk recall, not graph reasoning**: semantica's own graph-search leg
returned 0 facts for all five queries, so the retrieval being measured is vector
similarity over whole chunks; its per-chunk cost is the highest of the three
(3–4 LLM calls per chunk, 652 s for 3 375 chars, and the 1.7B's JSON validity
still cost 7 recovered parse failures); and it has **no crash-resume entry point
at all** — the only durable artefact is a graph file the caller chose to write.
#39 must weigh that against Graphiti's partial-but-guaranteed graph and Cognee's
all-or-nothing ingest.

**Traceability caveat (honest accounting).** Run 1's call counters were recorded
before the harness was corrected: its `provider_round_trips` (37) double-counted
the 5 plain-`generate` fallbacks that its own `chat.completions.create` wrapper
also counted, and its `llm_calls` (50) counted the `generate_structured`
attempts made *inside* `generate_typed`'s repair loop as if they were separate
logical calls. The harness was corrected for run 2 (a re-entrancy guard, and one
wire-level counter), and run 1's numbers above are recomputed from its recorded
records: 27 adapter entries, 32 wire prompts, 5 retries. Run 1's wall time, chunk
outcomes, graph quality, peak RSS, JSON failures and every retrieval number are
unaffected.

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
  `--resume-batch`; building one was out of #36's scope). **Where it is covered:**
  #37 measured it for **Cognee** under the same procedure (§6b): a SIGTERM
  mid-cognify leaves the interrupted pipeline run non-terminal on disk
  (`DATASET_PROCESSING_STARTED`, no `ended_at`) and cognee has **no resume entry**
  — a re-`cognify()` starts a new run, skips the already-done indexing step and
  redoes the extraction. That is the candidate's measured behaviour, not a
  harness limitation; the same procedure still owes a Graphiti re-measurement.
  **#38 measured it for Semantica** under the same procedure (§6c): a SIGTERM
  mid-extraction leaves nothing of semantica's own on disk — the interrupted
  chunk's work is lost and there is no ingest record, no per-chunk progress and
  no resume entry point, so a restart redoes the interrupted chunk from zero and
  keeps only what an external graph file happened to save (20 nodes / 10 edges
  from the completed chunk, reloaded through `ContextGraph.load_from_file`).
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

## 8. Decision (this ticket, #39 — 2026-11-08)

The decision is **keep Graphiti** — no swap. Full record:
`docs/adr/0001-graph-build-backend.md` (this repo's first ADR; `docs/adr/README.md`
indexes it). Answering the owner's question — *"will changing from graphiti to
cognee or semantica improve the speed of the graph build?"* — **no, not in the way
that matters**: Cognee builds a usable graph 4.5× faster (229.9 s vs the
incumbent's rejected 1 032.4 s) but moves that cost into every query (22–31 s each
vs 0.03–0.05 s) and ingests all-or-nothing with no resume entry point; Semantica's
5/5 answers do not use its graph at all (its graph leg returned 0 facts on all
five queries). The incumbent's only measured defect — 6/8 chunks skipped by M3's
truncation fail-fast — is the outgoing 1.7B small model's JSON-clamp class, and
#45 replaces that model by construction. A backend swap is the irreversible move;
the model swap is one ticket away and already scheduled.

### 8.1 The decision table (every row cites its log)

Run of record per candidate: `baseline_graphiti_1794046080` (§6),
`cognee_1794056509` (§6b), `semantica_1794069690` (§6c) — all under
`scratch/bakeoff/`, plus the corroborating runs cited in §6/§6b/§6c. Crash-resume
tests: `cognee_1794055197` / `semantica_1794070896`.

| Criterion | Graphiti (baseline) | Cognee | Semantica |
|---|---|---|---|
| **Wall time for a USABLE graph** | **no usable graph**: 1 032.4 s → 2/8 chunks, 75 % skip, the service gate rejects the batch (`RuntimeError: skipped 6/8 chunks (fraction 75% exceeds limit 30%)`) — step 1 aborts. `baseline_graphiti_1794046080.log/.json` | **229.9 s** → usable: 8/8 chunks, 105/222 graph. `cognee_1794056509.json` | **651.7 s** → usable: 8/8 chunks, 118/47 graph. `semantica_1794069690.json` |
| Chunks ingested verbatim | 2 / 8 (indices 0, 3; §6 per-chunk table) | 8 / 8 (`no_rechunking: chunk_nodes 8, expected 8, verbatim 8`; stored text sha256-equal to the contract) | 8 / 8 (`no_rechunking: verbatim 8/8`; stored memory-item content sha256-equal) |
| Skip rate | **75 %** — all 6 of one class: `malformed-json` = M3 `_TruncatedJSONError` after M1's 4096-token leaf clamp; batch stays terminal-succeeded, the gate then rejects it | **0 % skipped** — but no skip semantics: one bad chunk kills the whole cognify (run 1 `cognee_1794055410.log`: terminal `ValidationError`, 533.8 s, 0 entities) | **0 % skipped**, 0 terminal failures — but via a repair loop (up to 3 attempts), no fail-fast class |
| Graph quality (and what it means) | 40 nodes / 47 edges — from only 2 chunks (§1.0/§1.1/§1.3); labels `Entity` 40, `Person` 9, `Organization` 3, `Location` 1 | 105 nodes / 222 edges — 63 `Entity` + structural nodes; 42 distinct semantic relations (`causes`, `diffusion_chain`, `has_impact_index`, …) but 1.7B junk entities (`acme`, `alice`, `58%`, section headings as entities) and 105 connected components | 118 nodes / 47 edges — but **18 of 43 distinct edge types are invented `http://example.org/…` URIs** (19 of 47 edges): honest noise in the edge-type distribution |
| 5-query relevance (§4 verdicts) | **1 relevant / 1 partial / 3 irrelevant** — confounded: only chunks 0, 3 landed, so Q2–Q5's source chunks were never in the graph (coverage, not a retrieval bug) | **4 relevant / 1 partial** — from a graph+chunk mix (its own §6b sentence records this) | **5 / 5 relevant — but chunk recall**: the vector leg returns whole verbatim chunks; shown separately below |
| Query latency | 0.03–0.05 s, precomputed edges | **22–31 s** each — every search runs its own LLM completion (baseline: 0.03–0.05 s; §6b records the spread and the session-analysis overhead inside it) | 0.21–0.25 s — no LLM inside retrieve; see the caveat row |
| Retrieval-semantics caveats | Graph answers are computed at ingest; query latency is independent of any query-time model | Answers are LLM completions over retrieved context — retrieval quality partly from chunk text, not only the graph | **Graph-search leg returned 0 facts on all five queries** — `ContextGraph.query` is a whitespace-token substring match and these queries are Chinese; ranking weakly discriminative even at 8 chunks (top 0.96–0.99, next five 0.92–0.96, tail 0.66–0.68) — degrades further on the 53-chunk document |
| JSON-with-local-LM reliability | 0 parse failures / **6 truncation fail-fasts** — loud, bounded, per-chunk (M3, not retried by design); 0 retries | no fail-fast class; litellm re-prompts with the validation error up to 3× — run 2: 13 prompts / 9 calls / 0 terminal; run 1: 29 prompts / 9 calls / **1 terminal** | no fail-fast class; own repair loop up to 3 attempts (`instructor` absent) — **7 `ProcessingError` parse failures, all recovered / 0 terminal**, both runs |
| Crash-resume behaviour (§5) | **not measured** — gap; design evidence only: `_ensure_ingestion` re-enters a non-terminal batch; the harness has no `--resume-batch` (out of #36's scope) | measured: SIGTERM → child exits −15; run stays **non-terminal on disk** (`DATASET_PROCESSING_STARTED … end=None`); re-`cognify()` starts a new run, skips indexing, **re-extracts** — **no resume entry point**; stale-run recovery only after 3 600 s | measured: SIGTERM → child exits −15; **nothing of its own survives** except the harness's `graph.json` (20 nodes / 10 edges from the completed chunk); restart reloads the file and **the harness — not semantica — decides what is done** — **no resume entry point at all** |
| Peak RSS (100 ms sampling) | 2 853.5 MB (self 1 103.2 + backend 1 750.4; 9 061 samples) | **2 900.6 MB — highest** (self 774.1 + backend 2 126.4; 2 005 samples) | 2 401.8 MB — lowest (self 653.9 + backend 1 748.1; 5 769 samples) |
| Integration cost | **ships** behind `client.graph.*` / `client.batch.*` — zero | full #40-sized swap: isolated venv (3.11.15, 170 packages), litellm routing, per-run `COGNEE_ENV_FILE` (mandatory — cognee's `.env` walk otherwise re-points at a cloud key), dual stores (ladybug + lancedb), new adapter seam | full #40-sized swap: isolated venv (75 packages, torch 5.22 / transformers 5.22 stack), mandatory embedder-swap workaround (its default embedder cannot load offline), new adapter seam |

### 8.2 Why the decision turns where it does

The three rows that are structural to a backend (not to the outgoing model) all
favour Graphiti, and the rows that favour a swap are all model-shaped:

1. **The incumbent's only defect is the shared model failure.** 6/8 truncation
   fail-fasts from one class — bodies past M1's 4096-token clamp — the same 1.7B
   that killed Cognee's run 1 (terminal trailing-comma `ValidationError`) and cost
   Semantica 7 recovered parse failures. #45 replaces this model; its bar (6/6
   full mode, 0 skips) is measured on the decided backend's extraction path, and
   the 9B already reached 6/6 with the dedupe split (#30).
2. **Query-time cost.** Steps 3–5 are query-heavy; Cognee's 22–31 s per query vs
   the incumbent's 0.03–0.05 s precomputed edges is ~400–1000×, and Semantica's
   fast queries are vector chunk recall with a graph leg that returned 0 facts.
3. **Failure-mode shape and resume.** The incumbent fails loud and bounded
   (per-chunk skip + terminal record + gate rejection — the build never ships
   silently broken); Cognee is all-or-nothing; neither candidate has a resume
   entry point, while Graphiti's `_ensure_ingestion` re-entry is at least designed
   for it (unmeasured — see 8.4).

### 8.3 What #40 must do next

#40 is "serve the decided backend behind the graph interface" and, per its own
text, on a keep-Graphiti decision it verifies the seam is the shipped default and
closes on that evidence: a small end-to-end ingest + search through
`client.graph.*` / `client.batch.*`, and a grep-able guarantee that no backend SDK
is imported outside its adapter. **No swap wiring, no backend-tree deletion** —
`Prediction/cognee` and `Prediction/semantica` stay on disk as the re-runnable
comparison set (HANDOFF §4's delete rule is for owner cancellation of the
bake-off, which this is not). The winning-backend sequence then runs to its end:
#45 picks the `small` model on this backend's extraction path, and the dedupe
prompt split (`fix/22-hybrid-dedupe-statement`) resolves with this decision —
Graphiti wins, so it lives.

### 8.4 Folded-in: the §7 gap list, status after this decision

| #36 gap (§7) | Status after #39 |
|---|---|
| **Crash-resume** — unmeasured for Graphiti; candidates owed a measurement | **Per candidate:** Graphiti — still unmeasured, design evidence only (`_ensure_ingestion` re-entry; harness lacks `--resume-batch`); a Graphiti re-measurement needs that harness entry point, which is #40-or-later scope, not a decision blocker. Cognee — measured (§6b): non-terminal on disk, no resume entry, re-cognify redoes. Semantica — measured (§6c): nothing of its own survives; only the caller's `graph.json` did |
| **End-to-end step-1 success** | Open, and assigned: it is a *model* gap now. With `LFM3-1.7B-MLX-4bit` the 75 % skip + gate rejection stands (`baseline_graphiti_1794046080.json`); #45 owns it (bar 6/6, 0 skips) |
| **Determinism** | Unchanged: `temperature=0.0` is dropped by the MLX runner, so every number in §6/§6b/§6c is a sample — the strongest reason a swap would have needed the model settled first |
| **Small-model decision** | Explicitly out of scope here (#36, #30): this doc decided the backend with the incumbent small model as the measured LLM; #45 decides the model on the decided backend |

### 8.5 Sensitivity — what a BETTER small model at #45 changes

Every reliability row above is a function of the outgoing 1.7B, and a different
4-bit candidate changes all of them. With a model whose JSON fits the clamp:
Graphiti's skip rate has a measured path to 0 (the 9B reached 6/6 full mode with
the dedupe split — #30); its coverage-driven 1/5 relevance rises as more chunks
land; Cognee's 1.7B junk entities and Semantica's invented-URI edges fall too,
and Cognee's all-or-nothing terminal-failure exposure shrinks. What does **not**
change with the model: Cognee's 22–31 s per-query completion cost, Semantica's
0-fact graph leg on substring-matched Chinese queries, both candidates' absent
resume entry points, and both swaps' integration cost. That split is the decision:
the model-shaped rows are already scheduled to change at #45, the structural rows
are not — so the incumbent keeps the seat and #40 verifies the seam.

## 9. Evidence logs (all git-ignored, all in the main tree)

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
| **cognee harness** (git-ignored, `--mode smoke\|run\|inspect\|crash\|child`) | `scratch/bakeoff/bakeoff_cognee.py` |
| **cognee run 2 log (run of record)** | `scratch/bakeoff/cognee_1794056509.log` |
| cognee run 2 summary (every number in §6b) | `scratch/bakeoff/cognee_1794056509.json` |
| cognee run 2 backend log (model load/unload) | `scratch/bakeoff/cognee_1794056509.backend.log` |
| cognee run 2 query answers | `scratch/bakeoff/cognee_1794056509.queries.json` |
| corroborating cognee run 1 (log / json / queries / backend) | `scratch/bakeoff/cognee_1794055410.{log,json,queries.json,backend.log}` |
| cognee smoke test (1 chunk: wiring + API proof) | `scratch/bakeoff/cognee_1794054357.{log,json}` |
| cognee crash-resume test (parent log + child log) | `scratch/bakeoff/cognee_1794055197.{log,crash_child.log,json}` |
| cognee stores (ladybug graphs + lancedb vectors, candidate tree) | `../cognee/.bakeoff_store_{1794056509,1794055410}/` |
| **semantica harness** (git-ignored, `--mode smoke\|run\|crash\|child`) | `scratch/bakeoff/bakeoff_semantica.py` |
| semantica isolated-venv freeze (75 packages, the install row's evidence) | `scratch/bakeoff/semantica_venv_freeze.txt` |
| **semantica run 2 log (run of record)** | `scratch/bakeoff/semantica_1794069690.log` |
| semantica run 2 summary (every number in §6c) | `scratch/bakeoff/semantica_1794069690.json` |
| semantica run 2 backend log (model load/unload, 0 downloads) | `scratch/bakeoff/semantica_1794069690.backend.log` |
| semantica run 2 query answers | `scratch/bakeoff/semantica_1794069690.queries.json` |
| corroborating semantica run 1 (log / json / queries / backend) | `scratch/bakeoff/semantica_1794068587.{log,json,queries.json,backend.log}` |
| semantica smoke test (1 chunk: wiring + API proof) | `scratch/bakeoff/semantica_1794068164.{log,json,backend.log}` |
| semantica crash-resume test (parent log + ingest/resume child logs) | `scratch/bakeoff/semantica_1794070896.{log,json,backend.log}` + `../semantica/.bakeoff_crash_1794070896/{crash_child.log,resume_child.log,graph.json,checkpoint.json}` |
| semantica stores (in-memory graph + vector store, checkpointed JSON) | `../semantica/.bakeoff_run_{1794069690,1794068587}/` |

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

## 10. Reproduce

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

```bash
# #37 Cognee (isolated venv, NEVER the project venv; Ollama closed)
cd Prediction/cognee
uv venv --python 3.11 .venv-bakeoff
uv pip install -e . --python .venv-bakeoff/bin/python
uv pip install psutil --python .venv-bakeoff/bin/python   # §5 peak-RSS sampling
# smoke test first (1 chunk: wiring, ingest API, search):
.venv-bakeoff/bin/python ../Omnidroids/scratch/bakeoff/bakeoff_cognee.py --mode smoke
# the measured run (8 sha256-verified chunks, fresh store, 5 fixed queries):
.venv-bakeoff/bin/python ../Omnidroids/scratch/bakeoff/bakeoff_cognee.py --mode run
# §5 crash-resume procedure (SIGTERM mid-cognify → restart → resume):
.venv-bakeoff/bin/python ../Omnidroids/scratch/bakeoff/bakeoff_cognee.py --mode crash
# → scratch/bakeoff/cognee_<epoch>.{log,json,queries.json,backend.log}
```

The cognee harness re-verifies the same extract and chunk sha256s, asserts Ollama
is closed, starts the project backend on a free port (SIGTERM-only cleanup),
points cognee's OpenAI-compatible LLM client at that /v1 endpoint, embeds with
the shared `snowflake-arctic-embed-s` through cognee's own `fastembed` engine
(`HF_HUB_OFFLINE=1`), and counts LLM calls at cognee's own adapter entry
(`NativeLiteLLMAdapter.acreate_structured_output`) plus every provider round trip
(`litellm.acompletion`). No seam behaviour is patched; the store, dataset, `.env`
file and graph are fresh per run.

```bash
# #38 Semantica (isolated venv, NEVER the project venv; Ollama closed)
cd Prediction/semantica
uv venv --python 3.11 .venv-bakeoff
uv pip install -e '.[llm-openai,embeddings-local]' --python .venv-bakeoff/bin/python
uv pip install psutil --python .venv-bakeoff/bin/python   # §5 peak-RSS sampling
# smoke test first (1 chunk: wiring, ingest API, search):
.venv-bakeoff/bin/python ../Omnidroids/scratch/bakeoff/bakeoff_semantica.py --mode smoke
# the measured run (8 sha256-verified chunks, fresh store, 5 fixed queries):
.venv-bakeoff/bin/python ../Omnidroids/scratch/bakeoff/bakeoff_semantica.py --mode run
# §5 crash-resume procedure (SIGTERM mid-extract → restart → resume):
.venv-bakeoff/bin/python ../Omnidroids/scratch/bakeoff/bakeoff_semantica.py --mode crash
# → scratch/bakeoff/semantica_<epoch>.{log,json,queries.json,backend.log}
```

The semantica harness re-verifies the same extract and chunk sha256s, asserts
Ollama is closed (in the parent and in every crash child), starts the project
backend on a free port (SIGTERM-only cleanup), points semantica's own
`OpenAIProvider` at that /v1 endpoint through `GraphBuilder(base_url=…)`, embeds
with the shared `Snowflake/snowflake-arctic-embed-s` through semantica's own
`EmbeddingGenerator` (`HF_HUB_OFFLINE=1`), and counts LLM calls at semantica's own
adapter entries (`OpenAIProvider.generate_typed` / `generate_structured`) plus
every prompt on the wire (`openai`'s `chat.completions.create`). No seam
behaviour is patched; the extractor, graph, vector store, `ContextGraph` and
checkpoint are fresh per run.
