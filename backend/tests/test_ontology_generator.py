from app.config import Config
from app.services.ontology_generator import ONTOLOGY_SYSTEM_PROMPT, OntologyGenerator


class RecordingLLMClient:
    def __init__(self):
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "entity_types": [],
            "edge_types": [],
            "analysis_summary": "ok",
        }


def _generator_for_test() -> OntologyGenerator:
    generator = OntologyGenerator(llm_client=object())
    generator.MAX_TEXT_LENGTH_FOR_LLM = 2000
    generator.LONG_TEXT_CHUNK_SIZE = 500
    generator.LONG_TEXT_CHUNK_OVERLAP = 0
    generator.MAX_LONG_TEXT_CHUNKS = 3
    generator.MIN_LONG_TEXT_EXCERPT = 120
    return generator


def test_short_ontology_context_keeps_original_text():
    generator = _generator_for_test()

    context = generator._build_document_context(["short document body"])

    assert context == "short document body"
    assert "长文本自动分块摘要" not in context


def test_long_ontology_context_samples_across_document():
    generator = _generator_for_test()
    long_text = "BEGIN" + ("a" * 1050) + "MIDDLE" + ("b" * 1050) + "END"

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "长文本自动分块摘要" in context
    assert "BEGIN" in context
    assert "MIDDLE" in context
    assert "END" in context
    assert "分块 1/" in context
    assert "分块 3/" in context
    assert "分块 5/" in context


def test_very_long_ontology_context_selects_representative_chunks():
    generator = _generator_for_test()
    chunks = ["BEGIN"] + [
        f"CHUNK{i:02d}-" + (str(i) * 490)
        for i in range(12)
    ] + ["FINALEND"]
    long_text = "".join(chunks)

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "BEGIN" in context
    assert "FINALEND" in context
    assert context.count("--- 文档 1 / 分块") == generator.MAX_LONG_TEXT_CHUNKS


def test_ontology_generation_uses_the_configured_token_budget():
    """#51: the budget must be explicit and config-backed, not a dropped kwarg."""

    llm = RecordingLLMClient()
    generator = OntologyGenerator(llm_client=llm)

    result = generator.generate(
        document_texts=["A short source document."],
        simulation_requirement="Simulate the public discussion.",
    )

    assert result["analysis_summary"] == "ok"
    assert llm.calls[0]["max_tokens"] == Config.ONTOLOGY_LLM_MAX_TOKENS
    assert llm.calls[0]["max_attempts"] == 2


# -- the 兜底 fallback is a residual bucket, not a junk magnet -------------------
# Measured on the real 53-chunk build (proj_d21d40127acd, 8-bit, 2026-11-11):
# 17/53 chunks skipped, every one "truncated/unparseable JSON response" — the
# edge payloads outgrew M1's 4096 clamp. The payloads are inflated by junk
# entities the extractor dumps into the 兜底 fallback (dates, metrics, slogan
# fragments), and every junk edge multiplies into per-edge dedupe calls — the
# measured multiplier: 789 of the run's 933 LLM calls were dedupe (37% of wall).

_EN_JUNK_CLASSES = ("date", "time", "number", "metric", "slogan", "event")


def test_enforced_fallback_descriptions_name_the_junk_classes():
    """The code-enforced Person/Organization descriptions ride into every
    extraction prompt via ``__doc__`` (graphiti_zep._build_graphiti_types), so
    they must name what a fallback is NOT or the extractor keeps minting
    ``2025/11/28 18:16`` and ``全平台总阅读量`` as a Person."""

    generator = _generator_for_test()
    result = generator._validate_and_process({
        "entity_types": [
            {"name": "Student", "description": "A student.", "attributes": []},
            {"name": "Professor", "description": "A professor.", "attributes": []},
        ],
        "edge_types": [],
        "analysis_summary": "ok",
    })

    by_name = {e["name"]: e["description"].lower() for e in result["entity_types"]}
    assert "Person" in by_name and "Organization" in by_name
    for junk in _EN_JUNK_CLASSES:
        assert junk in by_name["Person"], f"Person fallback must forbid {junk!r}"
        assert junk in by_name["Organization"], f"Organization must forbid {junk!r}"


def test_system_prompt_fallback_guidance_names_the_junk_classes():
    """The prompt the ontology LLM reads must carry the same prohibition: the
    run that failed generated its own Person 兜底 ('任何自然人个体的兜底类型'),
    which is the magnet the model followed."""

    prompt = ONTOLOGY_SYSTEM_PROMPT
    assert "Person" in prompt            # the fallback requirement itself stands
    for junk in ("日期", "数字", "指标", "口号", "事件"):
        assert junk in prompt, f"the prompt must name {junk!r} as a non-entity"
