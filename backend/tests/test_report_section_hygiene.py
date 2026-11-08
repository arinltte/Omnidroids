# SPDX-License-Identifier: AGPL-3.0-or-later
"""Section-body hygiene + the assembled word band (#53).

The measured defect — spec #49's real step-4 report came out at 357 whitespace
words / 368 prose words against the stated 250-350 band, because two of its four
sections shipped nothing but a leaked tool call (``report49_summary_1794124681.json``
measures sections 03/04 at 25 and 24 words each). Three defects in
``ReportAgent._generate_section_react`` combined to let that happen:

* case 3 (*neither tool calls nor ``Final Answer:"``*) takes the whole response
  as the section body, and ``_strip_fake_tool_results`` strips only fabricated
  tool *results*, never calls;
* ``_parse_tool_calls`` drops a call whose ``name`` is not in
  ``VALID_TOOL_NAMES``, so an unknown-named call is not a tool call by the
  parser's reckoning and falls straight into case 3;
* the force-final fallback (``max_iterations`` exhausted) takes the whole
  response again.

And nothing measured the band: the 250-350 band lived only in
``docs/ACCEPTANCE_12.md`` and the spec, so an out-of-band report shipped silently.

These tests drive the real ``ReportAgent`` methods with a scripted stub LLM (no
weights are loaded anywhere), following the recorded-collaborator pattern of
``tests/test_step_model_assignment.py``. ``report_agent.py`` had no test coverage
before this file.
"""

import re

import pytest

from app.config import Config
from app.services.report_agent import (
    ReportAgent,
    ReportManager,
    ReportOutline,
    ReportSection,
    ReportStatus,
    ReportWordBandError,
    SectionBodyArtefactError,
)

# The band as stated for the report gate (#34, restated by #49).
BAND_MIN = 250
BAND_MAX = 350

# A well-formed call: parsed, executed, and counted against ``min_tool_calls``.
TOOL_CALL = (
    "调用\n"
    '{"name": "insight_forge", "parameters": {"query": "实验室关闭事件的传播"}}\n'
    "\n"
)

# CLEAN_UP_MARKER


class _ScriptedLLM:
    """Stub ``LLMClient``: ``chat`` hands out canned responses, recording each call.

    A non-callable entry is consumed by one call; a callable is a responder that
    is asked for every call (``responder(call_index)``), which is what the
    whole-report tests need to script a multi-section run.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages=None, **kwargs):
        call_index = len(self.calls)
        self.calls.append({"messages": [dict(m) for m in (messages or [])], **kwargs})
        if not self.responses:
            return None
        response = self.responses[0]
        if callable(response):
            return response(call_index)
        return self.responses.pop(0)

    def chat_json(self, messages=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in (messages or [])], **kwargs})
        return {}


class _StubZepTools:
    """Stands in for ``ZepToolsService``: no graph client, no key, no network."""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def get_simulation_context(self, **kwargs):
        return {
            "graph_statistics": {
                "total_nodes": 6,
                "total_edges": 9,
                "entity_types": {"Person": 3},
            },
            "total_entities": 6,
            "related_facts": [],
        }


def _agent(monkeypatch, responses):
    """A real ``ReportAgent`` whose only LLM is a scripted stub."""
    llm = _ScriptedLLM(responses)
    monkeypatch.setattr("app.services.report_agent.LLMClient", lambda *a, **k: llm)
    monkeypatch.setattr("app.services.report_agent.ZepToolsService", _StubZepTools)
    agent = ReportAgent(
        graph_id="graph_test",
        simulation_id="sim_test",
        simulation_requirement="requirement",
    )
    return agent, llm


def _react_agent(monkeypatch, responses, title="传播链的逆转"):
    """An agent plus the section/outline its ReAct loop is driven with."""
    agent, llm = _agent(monkeypatch, responses)
    section = ReportSection(title=title, content="")
    outline = ReportOutline(
        title="实验室关闭事件的传播",
        summary="一场由社交媒体引爆的机构信任危机预演",
        sections=[section],
    )
    return agent, llm, section, outline


def _drive_section(agent, section, outline):
    """Call the real ReAct seam for one section (the highest seam without a model)."""
    return agent._generate_section_react(
        section=section,
        outline=outline,
        previous_sections=[],
        section_index=1,
    )


# -- (a) a section body that is only a tool call ------------------------------
# The measured leak, not an invented one: sections 03 and 04 of report #49
# (scratch/report49_1794124681.md, section word counts 25 and 24) were this body
# and nothing else -- a bare JSON tool call with whitespace around it, which is
# what `assemble_full_report` had already wrapped in `## <title>`.
LEAKED_TOOL_CALL = (
    '\n\n{"name": "panorama_search", "parameters": {"query": "Priya Nair editor '
    'accountability provincial education ministry safety reporting rules Tomas '
    'Berg written report inspection", "include_expired": true}}\n\n'
)
LEAKED_TOOL_CALL_REASON = "only JSON"


def test_the_measured_leak_is_a_valid_tool_call_that_ships_when_final(monkeypatch):
    """The real mechanism, so nobody guards the wrong shape.

    The leaked body IS a well-formed tool call -- ``_parse_tool_calls`` returns it
    and ``panorama_search`` is in ``VALID_TOOL_NAMES`` -- so the ReAct loop
    executes it every iteration and the section budget runs out. The leak is that
    the loop's exhausted path and the force-final fallback both take the WHOLE
    response as the section body (``_strip_fake_tool_results`` strips only
    fabricated tool *results*, never calls), so a tool call that arrives as the
    final response is section prose. An "unknown-named call" was the wrong
    hypothesis: the name here is a valid one.
    """
    agent, _llm = _agent(monkeypatch, [LEAKED_TOOL_CALL])

    calls = agent._parse_tool_calls(LEAKED_TOOL_CALL)
    assert calls and calls[0]["name"] == "panorama_search"
    assert ReportAgent._strip_fake_tool_results(LEAKED_TOOL_CALL).strip().startswith("{")


def test_a_section_body_that_is_only_a_tool_call_is_rejected(monkeypatch):
    """The final response being a tool call must fail the section, loudly."""
    agent, llm, section, outline = _react_agent(
        monkeypatch,
        [LEAKED_TOOL_CALL] * (ReportAgent.MAX_TOOL_CALLS_PER_SECTION + 1),
    )

    with pytest.raises(SectionBodyArtefactError) as excinfo:
        _drive_section(agent, section, outline)

    message = str(excinfo.value)
    assert section.title in message                  # names the section
    assert LEAKED_TOOL_CALL_REASON in message        # names the reason
    assert agent._section_body_artefact_reason(LEAKED_TOOL_CALL) == LEAKED_TOOL_CALL_REASON
    assert section.content == ""                     # nothing was rendered
    assert "panorama_search" not in (section.content or "")


def test_a_leaked_tool_call_is_regenerated_once_then_fails_loudly(monkeypatch):
    """Never shipped, never silently dropped: one retry, then a loud failure."""
    agent, llm, section, outline = _react_agent(
        monkeypatch,
        [LEAKED_TOOL_CALL] * (ReportAgent.MAX_TOOL_CALLS_PER_SECTION + 2),
    )

    with pytest.raises(SectionBodyArtefactError):
        _drive_section(agent, section, outline)

    # the exhausted loop's calls, plus exactly one regeneration -- no retry storm
    assert len(llm.calls) == ReportAgent.MAX_TOOL_CALLS_PER_SECTION + 2
    # the artefact is recorded in the message history, not dropped without a trace
    recorded = [
        message
        for call in llm.calls
        for message in call["messages"]
        if "panorama_search" in str(message["content"])
    ]
    assert recorded


def test_a_regenerated_prose_body_ships_instead_of_the_artefact(monkeypatch):
    """The one regeneration is the recovery path: prose after the retry ships."""
    prose = "Final Answer: " + "模拟显示信任危机沿着可预测的路径扩散。 " * 8
    agent, llm, section, outline = _react_agent(
        monkeypatch,
        [LEAKED_TOOL_CALL] * (ReportAgent.MAX_TOOL_CALLS_PER_SECTION + 1) + [prose],
    )

    body = _drive_section(agent, section, outline)

    assert "panorama_search" not in body
    assert body.startswith("模拟显示")
    assert len(llm.calls) == agent.MAX_TOOL_CALLS_PER_SECTION + 2


# -- (b) JSON-only and whitespace-only bodies ---------------------------------

JSON_ONLY_BODY = '{"tool": "vector_search", "params": {"query": "角色分化"}}'
WHITESPACE_ONLY_BODY = "   \n\n\t "


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (LEAKED_TOOL_CALL, LEAKED_TOOL_CALL_REASON),
        (JSON_ONLY_BODY, "only JSON"),
        (WHITESPACE_ONLY_BODY, "empty/whitespace-only"),
    ],
)
def test_a_non_prose_section_body_is_neither_accepted_nor_rendered(
    body, reason, monkeypatch
):
    agent, llm, section, outline = _react_agent(
        monkeypatch,
        [body] * (ReportAgent.MAX_TOOL_CALLS_PER_SECTION + 2),
    )

    assert agent._section_body_artefact_reason(body) == reason

    with pytest.raises(SectionBodyArtefactError) as excinfo:
        _drive_section(agent, section, outline)

    assert reason in str(excinfo.value)
    assert section.content == ""


def test_prose_that_happens_to_mention_a_tool_is_not_an_artefact(monkeypatch):
    """The guard is narrow: real prose that names a tool is still a section body."""
    prose = (
        "研究团队调用了 vector_search 检索传播链条，并根据返回结果判断：匿名帖子在"
        "时间线上领先于官方通报，媒体随后跟进问责，政府最后介入，整起事件因此从内部"
        "安全通报升级为一场公开的机构信任危机。"
    )
    agent, _llm = _agent(monkeypatch, [prose])

    assert agent._section_body_artefact_reason(prose) is None


# -- (c) the word band on the assembled report ---------------------------------

# The measured #49 shape: 92 / 211 / 25 / 24 words per section, where the two
# small sections are the leaked tool calls.
OVER_BAND_SECTIONS = [
    ("传播与扩散", 92),
    ("角色与应对", 211),
    ("短期风险", 25),
    ("长期建议", 24),
]
UNDER_BAND_SECTIONS = [
    ("传播与扩散", 20),
    ("角色与应对", 18),
    ("短期风险", 22),
    ("长期建议", 24),
]
IN_BAND_SECTIONS = [
    ("传播与扩散", 68),
    ("角色与应对", 69),
    ("短期风险", 69),
    ("长期建议", 68),
]


def _assemble(sections):
    """The shape ``ReportManager.assemble_full_report`` writes: title, summary,
    rule, then one ``## title`` block per section."""
    parts = ["# 实验室关闭事件的传播", "", "> 摘要", "", "---", ""]
    for title, count in sections:
        parts += [f"## {title}", "", "word " * count, ""]
    return "\n".join(parts)


def _prose_words(text):
    """The band counting convention the #34/#49 evidence used: markdown
    scaffolding dropped (including `*`, without which `**bold**` counts as one
    token), remaining whitespace-separated tokens counted. The tests must count
    the way the evidence counted -- see the counter's fidelity test below."""
    return len(re.sub(r"[#>*`\-]", " ", text).split())


def test_an_out_of_band_assembly_is_caught_and_names_the_offending_sections():
    report = _assemble(OVER_BAND_SECTIONS)
    measured = _prose_words(report)
    assert measured > BAND_MAX  # the measured miss, reproduced synthetically

    with pytest.raises(ReportWordBandError) as excinfo:
        ReportAgent.check_report_word_band(report, OVER_BAND_SECTIONS)

    message = str(excinfo.value)
    assert str(measured) in message                     # the measured count
    assert f"{BAND_MIN}-{BAND_MAX}" in message          # the band it missed
    # Both directions are named, because #49's miss and its anomaly were
    # DIFFERENT sections: the report was over band overall (the 211-word section)
    # while two sections were 25 and 24 words -- the leaked tool calls. An
    # operator needs to see both halves to find the defect, so the message lists
    # every section outside its fair share in each direction rather than blaming
    # whichever half the direction of the miss happened to pick.
    assert "短期风险" in message and "长期建议" in message   # the short ones
    assert "角色与应对" in message                          # the over-band one
    assert "Below it" in message and "above it" in message  # both directions labelled


def test_an_under_band_assembly_names_every_short_section():
    report = _assemble(UNDER_BAND_SECTIONS)

    with pytest.raises(ReportWordBandError) as excinfo:
        ReportAgent.check_report_word_band(report, UNDER_BAND_SECTIONS)

    message = str(excinfo.value)
    assert str(_prose_words(report)) in message
    for title, _count in UNDER_BAND_SECTIONS:
        assert title in message


# -- (d) a normal report passes both hygiene and the band ---------------------

def test_a_normal_report_passes_the_band_check():
    """The regime the 9B's fixture gate cleared at 275 words."""
    report = _assemble(IN_BAND_SECTIONS)

    assert BAND_MIN <= _prose_words(report) <= BAND_MAX
    ReportAgent.check_report_word_band(report, IN_BAND_SECTIONS)  # no raise


def test_the_counter_reproduces_the_measured_numbers_exactly():
    """Fidelity over invention: the gate must measure what the evidence measured.

    scratch/report49_summary_1794124681.json records whitespace_words=357 and
    prose_words=368 for the saved report body, and both band verdicts False. The
    counter in ReportAgent is what the gate now uses, so if it disagrees with the
    evidence the gate would pass a report that was measured out of band. An
    earlier version of the scaffolding regex omitted `*`, counted 328, and would
    have waved the real miss through.
    """
    body = REAL_REPORT_BODY
    if body is None:
        import pytest

        pytest.skip("the #49 report body is not on disk")

    assert ReportAgent._count_report_words(body) == 368
    assert not (250 <= ReportAgent._count_report_words(body) <= 350)


def test_the_word_band_knob_defaults_are_the_stated_band():
    assert (Config.REPORT_WORD_COUNT_MIN, Config.REPORT_WORD_COUNT_MAX) == (BAND_MIN, BAND_MAX)


def test_the_band_gate_reads_the_knob_from_config(monkeypatch):
    """(e) the band is config-backed, and the verdict follows the knob."""
    report = _assemble(OVER_BAND_SECTIONS)

    with pytest.raises(ReportWordBandError):
        ReportAgent.check_report_word_band(report, OVER_BAND_SECTIONS)

    monkeypatch.setattr(Config, "REPORT_WORD_COUNT_MAX", 500)
    ReportAgent.check_report_word_band(report, OVER_BAND_SECTIONS)  # now in band

    monkeypatch.setattr(Config, "REPORT_WORD_COUNT_MIN", 300)
    with pytest.raises(ReportWordBandError):
        ReportAgent.check_report_word_band(_assemble(IN_BAND_SECTIONS), IN_BAND_SECTIONS)


PROSE_SECTION = (
    "在这场由匿名帖子引爆的机构信任危机中，各利益相关方的反应决定了危机的最终形态。"
    "大学官方在时间线上落后于社交媒体，媒体借机发起问责，政府随后介入调查。"
    "匿名社区的抢先曝光瓦解了机构的首发优势，使公众讨论的焦点从事件本身转向回应时机。"
    "当机构无法解释公众为何先于通报获知消息时，事件性质便由技术通报升级为机构失职。"
    "这一传播顺序的逆转预示：在社交媒体高度发达的环境中，任何内部信息都可能被提前曝光。"
    "机构危机管理的重心因此需要从应对事件转向应对传播节奏，并在第一时间给出可核查的说明。"
    "各利益相关者的角色也随之一分为二：一方被动防御、一方主动问责，形成清晰的行动阵营。"
)


def test_a_normal_prose_section_passes_the_hygiene_guard_and_ships(monkeypatch):
    """Case 3 with real prose still ships: the guard only rejects artefacts."""
    agent, llm, section, outline = _react_agent(
        monkeypatch,
        [TOOL_CALL, TOOL_CALL, TOOL_CALL, PROSE_SECTION],
    )

    body = _drive_section(agent, section, outline)

    assert body == PROSE_SECTION.strip()
    assert agent._section_body_artefact_reason(body) is None
    assert len(llm.calls) == 4  # no regeneration was needed


# -- the gate is applied where the report is assembled ------------------------

OUTLINE_TITLES = ["传播与扩散", "角色与应对", "短期风险", "长期建议"]


def _whole_report_agent(monkeypatch, tmp_path, section_body):
    """An agent whose outline is canned and whose four sections share one body.

    Each section costs four ``chat`` calls (three tool calls to clear
    ``min_tool_calls``, then one body); the assembly that follows makes no
    further call, so ``call_index % 4`` scripts a whole multi-section run.
    """
    def respond(call_index):
        return TOOL_CALL if call_index % 4 < 3 else "Final Answer: " + section_body

    llm = _ScriptedLLM([respond])
    llm.chat_json = lambda messages=None, **kwargs: {
        "title": "实验室关闭事件的传播",
        "summary": "一场由社交媒体引爆的机构信任危机预演",
        "sections": [{"title": title} for title in OUTLINE_TITLES],
    }
    monkeypatch.setattr("app.services.report_agent.LLMClient", lambda *a, **k: llm)
    monkeypatch.setattr("app.services.report_agent.ZepToolsService", _StubZepTools)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"))

    agent = ReportAgent(
        graph_id="graph_test",
        simulation_id="sim_test",
        simulation_requirement="requirement",
    )
    return agent, llm


def test_generate_report_never_marks_an_out_of_band_report_completed(monkeypatch, tmp_path):
    """The leak's real consequence: an out-of-band assembly must not ship."""
    agent, llm = _whole_report_agent(monkeypatch, tmp_path, "word " * 25)

    report = agent.generate_report()

    assert report.status == ReportStatus.FAILED
    # the message carries the count measured on the report that was assembled
    assert str(agent._count_report_words(report.markdown_content)) in report.error
    assert "REPORT_WORD_COUNT_MIN" in report.error   # the knob an operator turns
    for title in OUTLINE_TITLES:                     # every short section named
        assert title in report.error
    # the section files were written; only the verdict stopped the ship
    assert (tmp_path / "reports" / report.report_id / "section_01.md").exists()
    assert (tmp_path / "reports" / report.report_id / "full_report.md").exists()


def test_generate_report_completes_an_in_band_report(monkeypatch, tmp_path):
    agent, llm = _whole_report_agent(monkeypatch, tmp_path, "word " * 68)

    report = agent.generate_report()

    assert report.status == ReportStatus.COMPLETED, report.error
    assert report.markdown_content
    assert BAND_MIN <= agent._count_report_words(report.markdown_content) <= BAND_MAX


# The measured body the band gate must count the way the evidence counted
# (git-ignored scratch/, kept as run evidence; absent in a clean checkout).
from pathlib import Path as _Path

_EVIDENCE = _Path(__file__).resolve().parents[2] / "scratch" / "report49_1794124681.md"
REAL_REPORT_BODY = (
    _EVIDENCE.read_text(encoding="utf-8") if _EVIDENCE.exists() else None
)
