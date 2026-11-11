"""Clamp-truncation salvage at the local-extraction seam (HANDOFF §9 G.1 arm 2).

Offline-only. Unit-tests the production ``graphiti_zep._salvage_truncated_json``
and drives the real ``_generate_response`` the way
``test_graphiti_schema_echo_and_transforms.py`` does (patched Config + a fake
chat transport), because the salvage lives in that leaf call.

Why this exists — measured on the frozen slice (2026-11-10):
* chunk 6's ``ExtractedEdges`` call died at the M1 clamp mid-string
  (``finish_reason=length``, ``Unterminated string ... char 10037``) after 367
  lines of COMPLETE edge objects. The entity and resolution calls for that chunk
  had already succeeded; per-chunk fault isolation then discarded the whole
  chunk, and with it the gold platforms it carries.
* chunk 7's edges call leaked the model's own reasoning into its first element
  (``Expecting ',' delimiter: line 6 column 46``). Its entity call HAD extracted
  抖音/小红书/B站 — also discarded.

The gold standard attributes the entire recall ceiling to these skips, so the
arm is: on a certified truncation (``finish_reason`` in {None, "length"}), keep
the complete prefix of the model's own output instead of dropping the chunk.
Nothing is invented; an echo-shaped salvage still fails fast as schema-echo; an
un salvageable truncation still raises ``_TruncatedJSONError`` unchanged.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.config import Config
from app.utils import graphiti_zep
from graphiti_core.prompts.extract_edges import ExtractedEdges
from graphiti_core.prompts.extract_nodes import ExtractedEntities


# ---------------------------------------------------------------------------
# Recorded failure shapes (the measured bodies, reduced to their structure)
# ---------------------------------------------------------------------------

_EDGE_OK_1 = {
    "source_entity_name": "武汉大学",
    "target_entity_name": "图书馆",
    "relation_type": "INVOLVES_OR_IS_PART_OF",
    "fact": "诬告案宣判后，图书馆成为舆论关注的焦点",
    "valid_at": None,
}
_EDGE_OK_2 = {
    "source_entity_name": "微博",
    "target_entity_name": "武汉大学",
    "relation_type": "REPORTS_ON",
    "fact": "微博平台在监测周期内多次报道该校事件",
    "valid_at": "2023-09",
}
# chunk 6's shape: two complete edges, then the model is cut mid-string.
_CHUNK_6_BODY = (
    '{"edges": ['
    + json.dumps(_EDGE_OK_1, ensure_ascii=False)
    + ", "
    + json.dumps(_EDGE_OK_2, ensure_ascii=False)
    + ', {"source_entity_name": "校方", "target_entity_name": "武汉大学", '
    '"relation_type": "REPORTS_ON", "fact": "一个很长的事实陈述，在 4096-token clamp 处被截'
)
# chunk 7's shape: the model's reasoning leaks into the FIRST element.
_CHUNK_7_BODY = (
    '{ "edges": [ { "source_entity_name": "武汉大学", "target_entity_name": "图书馆", '
    '"relation_type": "INVOLVES_OR_IS_PART_OF"? Wait need to map from text. Let\'s '
    're-check the ontology before writing the relation_type'
)
_COMPLETE_EDGES = json.dumps({"edges": [_EDGE_OK_1, _EDGE_OK_2]}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers (same fake-transport pattern as the schema-echo suite)
# ---------------------------------------------------------------------------

def _prompts() -> List[Any]:
    return [
        SimpleNamespace(role="system", content="system instructions"),
        SimpleNamespace(role="user", content="USER EPISODE <ENTITY TYPES>[]"),
    ]


def _fake_transport(body: str, *, finish_reason: str):
    calls: List[Dict[str, Any]] = []

    async def _create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=body),
                    finish_reason=finish_reason,
                )
            ]
        )

    return calls, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))


def _seam_client(monkeypatch) -> Any:
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MAX_TOKENS", 4096)
    monkeypatch.setattr(Config, "GRAPHITI_STRUCTURED_OUTPUT_MODE", "json_schema")
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MODEL", "")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "SENTINEL")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "http://127.0.0.1:1/v1")
    return graphiti_zep._build_llm_client()


def _generate(client, messages, response_model):
    return asyncio.run(client._generate_response(messages, response_model, 2048))


# ---------------------------------------------------------------------------
# The unit: _salvage_truncated_json
# ---------------------------------------------------------------------------

def test_complete_body_is_returned_unchanged():
    """A body that parses cleanly must survive salvage element-for-element.

    The seam only calls this after ``json.loads`` fails, but the function is
    unit-tested directly, so its contract on complete bodies is pinned here."""
    payload, kept, cut, empty = graphiti_zep._salvage_truncated_json(_COMPLETE_EDGES)
    assert payload == {"edges": [_EDGE_OK_1, _EDGE_OK_2]}
    assert kept == 2 and empty is False
    assert json.dumps(payload, ensure_ascii=False)  # re-serialises


def test_deep_truncation_keeps_the_complete_prefix():
    """Chunk 6's shape: the clamp cut mid-string, the earlier edges are intact."""
    result = graphiti_zep._salvage_truncated_json(_CHUNK_6_BODY, allow_empty=True)
    assert result is not None
    payload, kept, cut, empty = result
    assert kept == 2 and empty is False
    assert payload == {"edges": [_EDGE_OK_1, _EDGE_OK_2]}
    # the truncated tail is dropped, not repaired: the cut sits before it
    assert cut < len(_CHUNK_6_BODY)
    assert "被截" not in json.dumps(payload, ensure_ascii=False)


def test_mid_first_element_truncation_is_empty_only_when_allowed():
    """A body cut inside its FIRST element recovers nothing without the flag."""
    body = _COMPLETE_EDGES[: _COMPLETE_EDGES.index('", "target') ] + '"target'
    assert graphiti_zep._salvage_truncated_json(body, allow_empty=False) is None
    payload, kept, cut, empty = graphiti_zep._salvage_truncated_json(body, allow_empty=True)
    assert payload == {"edges": []} and kept == 0 and empty is True


def test_reasoning_leak_body_salvages_empty_under_the_clamp():
    """Chunk 7's shape: the model's prose breaks the first element.

    Salvage keeps the chunk alive with zero edges from that call -- the entity
    call had already succeeded, and per-chunk fault isolation would otherwise
    discard it."""
    result = graphiti_zep._salvage_truncated_json(_CHUNK_7_BODY, allow_empty=True)
    assert result is not None
    payload, kept, cut, empty = result
    assert payload == {"edges": []} and kept == 0 and empty is True
    # without the clamp certificate, the same body is NOT salvaged
    assert graphiti_zep._salvage_truncated_json(_CHUNK_7_BODY, allow_empty=False) is None


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        '{"summary": "a truncated scalar with no array value',
        '{"first": [{"x": 1}], "second": [{"y": 2',  # two top-level arrays
        "",  # empty body
    ],
)
def test_unsalvageable_shapes_return_none(body):
    """The function must stay conservative: it never guesses content."""
    assert graphiti_zep._salvage_truncated_json(body, allow_empty=True) is None


def test_trailing_garbage_after_a_complete_body_is_dropped():
    """A complete payload followed by junk keeps the payload (proxy-parity:
    the first complete JSON value wins, the tail does not rescue anything)."""
    body = _COMPLETE_EDGES + " trailing garbage}"
    result = graphiti_zep._salvage_truncated_json(body, allow_empty=True)
    assert result is not None
    payload, kept, cut, empty = result
    assert payload == {"edges": [_EDGE_OK_1, _EDGE_OK_2]} and kept == 2 and empty is False


def test_escaped_quotes_and_nested_arrays_are_respected():
    """String state must survive escaped quotes and nested containers.

    A fact containing a quote and a brace must not end the scan early."""
    body = (
        '{"edges": ['
        '{"fact": "他说\\"你好{\\" 然后离开", "relation_type": "SAYS"},'
        '{"fact": "未完'
    )
    result = graphiti_zep._salvage_truncated_json(body, allow_empty=True)
    assert result is not None
    payload, kept, cut, empty = result
    assert kept == 1
    assert payload["edges"][0]["fact"] == '他说"你好{" 然后离开'


# ---------------------------------------------------------------------------
# The seam: _generate_response
# ---------------------------------------------------------------------------

def test_seam_returns_salvaged_edges(monkeypatch):
    client = _seam_client(monkeypatch)
    _calls, transport = _fake_transport(_CHUNK_6_BODY, finish_reason="length")
    client.client = transport

    result = _generate(client, _prompts(), ExtractedEdges)
    assert result == {"edges": [_EDGE_OK_1, _EDGE_OK_2]}


def test_seam_salvages_empty_and_keeps_the_chunk(monkeypatch):
    """Chunk 7's leak yields an edge-less success -- the chunk is not skipped."""
    client = _seam_client(monkeypatch)
    _calls, transport = _fake_transport(_CHUNK_7_BODY, finish_reason="length")
    client.client = transport

    result = _generate(client, _prompts(), ExtractedEdges)
    assert result == {"edges": []}


def test_unsalvageable_truncation_still_fails_fast(monkeypatch):
    """Nothing salvageable -> ``_TruncatedJSONError`` exactly as before."""
    client = _seam_client(monkeypatch)
    # no array value at all: nothing to salvage, fail fast exactly as before
    body = '{"summary": "a very long scalar answer truncated at the clamp mid-s'
    _calls, transport = _fake_transport(body, finish_reason="length")
    client.client = transport

    with pytest.raises(graphiti_zep._TruncatedJSONError):
        _generate(client, _prompts(), ExtractedEdges)


def test_generation_defect_stop_finished_is_not_salvaged(monkeypatch):
    """A broken body with ``finish_reason=stop`` is a generation defect, not a
    truncation: the existing retry path is unchanged."""
    client = _seam_client(monkeypatch)
    body = _CHUNK_6_BODY  # broken, but the server says the generation finished
    _calls, transport = _fake_transport(body, finish_reason="stop")
    client.client = transport

    with pytest.raises(json.JSONDecodeError):
        _generate(client, _prompts(), ExtractedEdges)


def test_truncated_echo_body_still_fails_as_schema_echo(monkeypatch):
    """An echo-shaped body that happens to salvage must NOT become data."""
    client = _seam_client(monkeypatch)
    body = (
        '{"$defs": [{"Edge": {"properties": {"relation_type": {"type": "string"'
    )
    _calls, transport = _fake_transport(body, finish_reason="length")
    client.client = transport

    with pytest.raises(graphiti_zep._SchemaEchoError):
        _generate(client, _prompts(), ExtractedEdges)


def test_entity_payloads_salvage_the_same_way(monkeypatch):
    """``ExtractedEntities`` returns a dict, not a list: a salvage of that shape
    must reach graphiti intact (the arm's entity calls were never the failure,
    but the code path is shared)."""
    client = _seam_client(monkeypatch)
    body = (
        '{"extracted_entities": ['
        '{"name": "抖音/短视频平台", "entity_type_id": 4}, '
        '{"name": "小红书", "entity_type_id": 4}, '
        '{"name": "B站", "entity_type'
    )
    _calls, transport = _fake_transport(body, finish_reason="length")
    client.client = transport

    result = _generate(client, _prompts(), ExtractedEntities)
    assert result == {
        "extracted_entities": [
            {"name": "抖音/短视频平台", "entity_type_id": 4},
            {"name": "小红书", "entity_type_id": 4},
        ]
    }
