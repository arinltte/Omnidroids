# SPDX-License-Identifier: AGPL-3.0-or-later
"""HANDOFF §9 B guard: the chunk-size default, and the chunker at that size.

The owner approved an experiment, not a value: **chunk size 500 -> 1500**, on the
reasoning that a chunk carries only ~5 % of the tokens of the fixed-overhead
prompts it triggers, so 53 chunks of a mean 443 chars should cost ~3x what 19
would. **Measured 2026-11-09 on a 4-chunk slice of the acceptance document
(LFM3-1.7B-MLX-4bit, chunk_size 1500, `scratch/chunkslice_1500_1p7b_*`):**

| | |
|---|---|
| chunks on the document | 53 -> **15** (the ~3x did happen) |
| ok / skipped on the slice | **1 ok / 3 skipped** (baseline run: 9 / 21 of 30) |
| mean seconds per chunk | 109.2 (baseline ~120) |
| mean seconds per LLM call | 28.1 |
| **edge calls truncated at M1's clamp** | **2 of 5 = 40 %** (baseline 17 / 29 = 59 %) |

40 % is above the ~20 % line the owner set as the stop condition, the yield did
not hold (1/4 landed, and the failure mode broadened from edge calls to node
calls), so **1500 was not adopted and 500 stayed**. The full record lives in
HANDOFF §9 B; this file only pins what the measurement decided and keeps the
chunker honest at that size, so the experiment is not re-run blind:

1. **The default** — ``Config.DEFAULT_CHUNK_SIZE`` is 500 and the overlap did
   not move with it (``DEFAULT_CHUNK_OVERLAP`` stays 50; a silently widened
   overlap would change which content a chunk covers).
2. **The per-project parameter stays consistent** — a new ``Project`` carries
   the same default as the config, so ``_build_graph``'s fallback chain
   (``app/api/graph.py``: ``chunk_size or Config.DEFAULT_CHUNK_SIZE``) resolves
   one value, and the API's own documented default (``app/api/graph.py``'s
   docstring) agrees with it.
3. **The chunker is coherent at that size** — on a hermetic zh corpus: no empty
   chunk, no chunk longer than the size, and the overlap is honoured exactly
   (each chunk starts where the previous chunk's tail begins, never a gap and
   never more than ``chunk_overlap`` of duplicated text). This is what makes
   "53 chunks instead of 19" a cost, not a content change.
4. **The chunks still pass the batch validator** — including its 10 000-char
   per-item cap, which is the limit a larger chunk would eventually meet.

Pure filesystem/text assertions: no app import of a live model, no network, no
Kuzu. The measured numbers themselves live in ``HANDOFF.md`` §9 B.
"""

import re
from pathlib import Path

from app.config import Config
from app.models.project import Project, ProjectStatus
from app.services.graph_builder import GraphBuilderService
from app.services.text_processor import TextProcessor

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_API = REPO_ROOT / "backend" / "app" / "api" / "graph.py"

# The value the 2026-11-09 measurement decided (HANDOFF §9 B): 1500 was tried
# and NOT adopted, so 500 is the pinned default. Changing this is an owner
# decision backed by a measurement, not a refactor.
DECIDED_CHUNK_SIZE = 500
# The overlap that shipped with 500 and must not move with the size.
DECIDED_CHUNK_OVERLAP = 50
# The measured, rejected alternative. Kept as a constant so the rejection is
# checkable: if a session re-raises the default to this without re-measuring,
# the default pin below fails with a message naming it.
REJECTED_CHUNK_SIZE = 1500

# A hermetic zh corpus: deterministic, sentence-boundary separated like the
# acceptance document, long enough to produce several chunks at either size.
_CORPUS_UNITS = [
    "第{n}节记录了品牌声誉评估的方法、样本构成与统计口径，评估组在说明中强调数据来源必须列明，"
    "任何结论都应当注明其适用范围与不确定性。",
    "受访机构包括高校、媒体与监管部门，访谈记录按主题归类，涉及舆情响应、信息公开与合作机制三个方面。",
    "报告同时给出时间线：事件发生、首次回应、集中讨论与阶段性结论，各阶段均标注了可核验的公开材料。",
    "方法论部分再三提醒，样本量、抽样框与问卷措辞会直接影响指标的解释，需要在附录中完整披露。\n\n",
]


def _zh_corpus(target_chars: int) -> str:
    """Build the deterministic corpus: units repeated, then truncated.

    Truncation keeps the corpus at a length that is not a multiple of the chunk
    size, so the final (short) chunk is exercised too.
    """

    parts = []
    index = 0
    total = 0
    while total < target_chars:
        unit = _CORPUS_UNITS[index % len(_CORPUS_UNITS)].format(n=index + 1)
        parts.append(unit)
        total += len(unit)
        index += 1
    corpus = "".join(parts)
    return corpus[:target_chars]


def _chunk_spans(text: str, chunks: list, chunk_size: int) -> list:
    """Locate every chunk in the corpus and return (start_of_chunk, end_of_prev).

    Searching from ``prev_end - chunk_size`` (rather than ``prev_end``) is the
    correct lower bound: the chunker starts the next chunk up to ``chunk_overlap``
    characters *before* the previous chunk's stripped end, so a search starting
    at ``prev_end`` would miss exactly the overlap the invariant must measure.
    """

    spans = []
    cursor = 0
    previous_end = None
    for chunk in chunks:
        position = text.find(chunk, cursor)
        assert position != -1, f"chunk not found in corpus (searching from {cursor})"
        spans.append((position, previous_end))
        cursor = max(0, position + len(chunk) - chunk_size)
        previous_end = position + len(chunk)
    return spans


# --- 1. the default itself -------------------------------------------------
def test_default_chunk_size_is_the_measured_decision():
    assert Config.DEFAULT_CHUNK_SIZE == DECIDED_CHUNK_SIZE, (
        f"Config.DEFAULT_CHUNK_SIZE is {Config.DEFAULT_CHUNK_SIZE}, not the "
        f"measured {DECIDED_CHUNK_SIZE} (HANDOFF §9 B)"
    )
    if Config.DEFAULT_CHUNK_SIZE == REJECTED_CHUNK_SIZE:
        raise AssertionError(
            f"DEFAULT_CHUNK_SIZE is back at {REJECTED_CHUNK_SIZE}, which the "
            "2026-11-09 measurement did NOT adopt: 1 ok / 3 skipped on the slice "
            "(baseline 9 / 21 of 30) and 40 % of edge calls truncated at M1's "
            "clamp, above the owner's ~20 % stop line. Re-measure, with the "
            "truncation fixed, before re-raising it — see HANDOFF §9 B."
        )


def test_default_chunk_size_is_not_the_unmeasured_alternative():
    """The rejected 1500 may come back only with a new measurement."""

    assert Config.DEFAULT_CHUNK_SIZE != REJECTED_CHUNK_SIZE, (
        f"DEFAULT_CHUNK_SIZE is {REJECTED_CHUNK_SIZE} — the measured rejection was "
        "reverted without a new measurement (1 ok / 3 skipped, 40 % of edge calls "
        "truncated). HANDOFF §9 B carries the numbers."
    )


def test_default_chunk_overlap_did_not_move_with_the_size():
    """The overlap is a separate knob: moving it changes content coverage."""

    assert Config.DEFAULT_CHUNK_OVERLAP == DECIDED_CHUNK_OVERLAP, (
        f"Config.DEFAULT_CHUNK_OVERLAP is {Config.DEFAULT_CHUNK_OVERLAP}, not "
        f"{DECIDED_CHUNK_OVERLAP}. The §9 B approval covers the size only."
    )


# --- 2. the per-project parameter stays consistent ------------------------
def _new_project() -> Project:
    """A project as ``ProjectManager.create_project`` builds one (chunk knobs defaulted)."""

    return Project(
        project_id="proj_test",
        name="chunk default",
        status=ProjectStatus.CREATED,
        created_at="2026-11-09T00:00:00+00:00",
        updated_at="2026-11-09T00:00:00+00:00",
    )


def test_project_default_matches_the_config_default():
    project = _new_project()
    assert project.chunk_size == Config.DEFAULT_CHUNK_SIZE, (
        f"Project.chunk_size defaults to {project.chunk_size} but "
        f"Config.DEFAULT_CHUNK_SIZE is {Config.DEFAULT_CHUNK_SIZE}; a new project "
        "would then override the config default with the stale per-project one"
    )
    assert project.chunk_overlap == Config.DEFAULT_CHUNK_OVERLAP


def test_project_default_survives_a_dict_round_trip():
    project = _new_project()
    reloaded = Project.from_dict(project.to_dict())
    assert reloaded.chunk_size == Config.DEFAULT_CHUNK_SIZE
    assert reloaded.chunk_overlap == Config.DEFAULT_CHUNK_OVERLAP


def test_graph_api_documents_the_config_default():
    """``app/api/graph.py``'s request docstring states the default it resolves.

    The endpoint resolves ``data.get('chunk_size', project.chunk_size or
    Config.DEFAULT_CHUNK_SIZE)``, so the documented number has to equal the
    config default or the API docs lie about what an omitted field means.
    """

    text = GRAPH_API.read_text(encoding="utf-8")
    documented = re.search(r'"chunk_size"\s*:\s*(\d+)', text)
    assert documented, (
        f"{GRAPH_API}: the request docstring no longer documents a chunk_size "
        "default — restore it (it is the third site that names the knob)"
    )
    assert int(documented.group(1)) == Config.DEFAULT_CHUNK_SIZE, (
        f"{GRAPH_API}: documents chunk_size default {documented.group(1)} but "
        f"Config.DEFAULT_CHUNK_SIZE is {Config.DEFAULT_CHUNK_SIZE} — the API "
        "docs and the resolved default have drifted apart"
    )


# --- 3. the chunker is coherent at the decided size --------------------
def test_chunker_at_the_default_emits_coherent_chunks():
    corpus = _zh_corpus(target_chars=9000)
    chunks = TextProcessor.split_text(
        corpus, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)

    assert len(chunks) > 3, (
        f"only {len(chunks)} chunks from 9000 chars at "
        f"{Config.DEFAULT_CHUNK_SIZE} — the corpus or the splitter is wrong"
    )
    empty = [i for i, chunk in enumerate(chunks) if not chunk.strip()]
    over_long = [
        (i, len(chunk)) for i, chunk in enumerate(chunks)
        if len(chunk) > Config.DEFAULT_CHUNK_SIZE
    ]
    assert not empty, f"empty chunk(s) emitted at {empty} — a chunk with no content"
    assert not over_long, (
        f"chunk(s) longer than the size: {over_long} (the size is a cap, not a hint)"
    )

    spans = _chunk_spans(corpus, chunks, Config.DEFAULT_CHUNK_SIZE)
    for index, (start, previous_end) in enumerate(spans):
        if index == 0:
            assert start == 0, f"chunk 0 starts at {start}, not the corpus start"
            continue
        gap = start - previous_end
        assert gap <= 0, (
            f"chunk {index} starts {gap} chars after chunk {index - 1} ends: a gap "
            f"of that size is dropped content, not an overlap "
            f"(overlap={Config.DEFAULT_CHUNK_OVERLAP})"
        )
        assert gap >= -Config.DEFAULT_CHUNK_OVERLAP, (
            f"chunk {index} overlaps chunk {index - 1} by {-gap} chars, more than "
            f"the configured {Config.DEFAULT_CHUNK_OVERLAP}: the seam is wider "
            "than the knob the owner approved"
        )


# --- 4. the decision's evidence stays in the repo ---------------------
def test_handoff_records_the_measured_chunk_size_decision():
    """HANDOFF §9 B must keep carrying the 1500 measurement, not the hope.

    Mirrors ``test_windows_untested_note.py``: a prose guard over the steering
    document, because the number in ``app/config.py`` means nothing without the
    reason it sits there. If §9 B is edited back to "not yet measured", this
    fails rather than letting the repo forget that 1500 was tried.
    """

    handoff = (REPO_ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    section = handoff.split("### B.", 1)[-1].split("### C.", 1)[0]
    assert "1500" in section, (
        "HANDOFF §9 B no longer mentions the 1500 alternative — the measured "
        "rejection this guard records has been edited away"
    )
    outcome = re.search(r"not adopted|rejected|revert", section, re.IGNORECASE)
    assert outcome, (
        "HANDOFF §9 B no longer states the outcome of the 1500 measurement "
        "(not adopted / rejected / revert) — the record must say what was decided"
    )
    assert re.search(r"\d+\s*%", section), (
        "HANDOFF §9 B no longer carries a measured percentage — the figure that "
        "decided 1500 (40 % of edge calls truncated at M1's clamp) is the whole "
        "argument, so it belongs here and not only in an issue comment"
    )


def test_default_chunks_pass_the_production_batch_limits():
    """The batch validator runs before any graph mutation; it must not move."""

    corpus = _zh_corpus(target_chars=9000)
    chunks = TextProcessor.split_text(
        corpus, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)
    GraphBuilderService.validate_batch_chunks(chunks, batch_size=350)
    assert max(len(chunk) for chunk in chunks) < 10_000, (
        "a chunk at the new default is past the 10 000-character batch item cap"
    )
