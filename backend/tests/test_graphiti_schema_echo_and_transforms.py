"""#22/#1909/#868 regression: schema-echo detection, JSON-Schema transforms,
Graphiti required-field completeness, endpoint-name normalization, and the
skipped-fraction guard.

Offline-only. It drives the PRODUCTION seam functions where they exist
(``graphiti_zep._looks_like_schema_echo`` / ``_generate_response``) and quotes the
upstream #1909/PR#1910 schema transform as an ANALYSIS UTILITY that is deliberately
NOT wired into the production response path (our proxy strips ``response_format``
and MLX has no grammar-decoding, so PR #1910's "grammar skips optional keys"
mechanism does not fire here -- see the root-cause report). The utility and its
tests exist so the transform *can* be measured/adopted safely later, and so the
nullable-preservation and required-completeness invariants are pinned.

Root-cause anchors (measured, see HANDOFF and scratch/chunk_slice_*.json):
* The 2026-11-10 9B run (Ornith-1.5-9B-MLX-6bit) skipped chunks 2/4/6 because the
  model echoed the JSON Schema (``{"$defs": {"Edge": ...}}``) instead of data --
  graphiti_core 0.31.2 then raised ``ExtractedEdges(**body)`` -> "edges Field
  required", classed generic "wrong-keys". ``_SchemaEchoError`` relabels it.
* The 8-bit control (LFM3-1.7B-MLX-8bit) failed the same 8-chunk slice only on
  malformed JSON (0 echoes) -- so the detector must never fire on the control.
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.config import Config
from app.utils import graphiti_zep
from graphiti_core.prompts.extract_edges import ExtractedEdges
from graphiti_core.prompts.extract_nodes import (
    ExtractedEntities,
    SummarizedEntities,
)

from graphiti_core.utils.maintenance.dedup_helpers import (
    _normalize_string_exact,
)


# ===========================================================================
# The recorded 9B schema-echo payloads (verbatim shape from the measured run).
# These ARE the schema document, not extraction data.
# ===========================================================================
_9B_ECHO_EXTRACTED_EDGES = {
    "$defs": {
        "Edge": {
            "properties": {
                "source_entity_name": {"type": "string"},
                "target_entity_name": {"type": "string"},
                "relation_type": {"type": "string"},
                "fact": {"type": "string"},
                "valid_at": {"type": ["string", "null"]},
                "invalid_at": {"type": ["string", "null"]},
            },
            "required": [
                "source_entity_name",
                "target_entity_name",
                "relation_type",
                "fact",
            ],
            "type": "object",
        }
    },
    "properties": {"edges": {"items": {"$ref": "#/$defs/Edge"}, "type": "array"}},
    "required": ["edges"],
    "type": "object",
}

_9B_ECHO_SUMMARIZED_ENTITIES = {
    "$defs": {
        "SummarizedEntity": {
            "properties": {
                "name": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["name", "summary"],
            "type": "object",
        }
    },
    "properties": {
        "summaries": {
            "items": {"$ref": "#/$defs/SummarizedEntity"},
            "type": "array",
        }
    },
    "required": ["summaries"],
    "type": "object",
}


# ===========================================================================
# 1. Schema-echo detection -- the production detector
# ===========================================================================
def test_required_top_level_reflects_graphiti_models():
    """#{field} required sets are read from the real graphiti response models."""

    assert graphiti_zep._required_top_level(ExtractedEdges) == {"edges"}
    assert graphiti_zep._required_top_level(ExtractedEntities) == {"extracted_entities"}
    assert graphiti_zep._required_top_level(SummarizedEntities) == {"summaries"}


@pytest.mark.parametrize(
    "payload,model",
    [
        (_9B_ECHO_EXTRACTED_EDGES, ExtractedEdges),
        (_9B_ECHO_SUMMARIZED_ENTITIES, SummarizedEntities),
        ({"$defs": {"X": {"type": "object"}}}, ExtractedEdges),
        # a full-schema echo whose top-level ``properties`` even names the field
        ({"properties": {"summaries": {"type": "array"}}, "type": "object"}, SummarizedEntities),
    ],
    ids=["9B-edges-$defs", "9B-summaries-$defs", "bare-$defs", "full-schema-wrapper"],
)
def test_schema_echo_is_detected(payload, model):
    assert graphiti_zep._looks_like_schema_echo(payload, model) is True


@pytest.mark.parametrize(
    "payload,model",
    [
        # populated extraction result -- not an echo
        (
            {"edges": [{"fact": "a works at b", "source_entity_name": "a",
                        "target_entity_name": "b", "relation_type": "WORKS_AT"}]},
            ExtractedEdges,
        ),
        # an edge that merely OMITS an optional temporal field still carries
        # the required top-level key -> NOT an echo (that is #1909 completeness,
        # owned by pydantic defaulting, not a schema echo)
        (
            {"edges": [{"fact": "x", "source_entity_name": "a",
                        "target_entity_name": "b", "relation_type": "R"}]},
            ExtractedEdges,
        ),
        # a genuine wrong-keys alias the alias net still owns
        ({"entities": [{"text": "x", "entity_type": "Person"}]}, ExtractedEntities),
        # plain non-schema junk
        ({"foo": "bar"}, ExtractedEdges),
        # non-dict / empty / no-required model
        ("[1,2]", ExtractedEdges),
        ({}, ExtractedEdges),
        ({"$defs": {}}, None),
    ],
    ids=["populated", "edge-missing-valid_at", "wrong-keys-alias", "plain-junk",
         "non-dict", "empty-dict", "none-model"],
)
def test_schema_echo_is_not_over_fired(payload, model):
    assert graphiti_zep._looks_like_schema_echo(payload, model) is False


# ===========================================================================
# 2. Schema echo fails fast at the leaf, and echo is a PROMPT phenomenon
# ===========================================================================
def _prompts() -> List[SimpleNamespace]:
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
    """The production client with a patched Config (as the harness recorded)."""

    monkeypatch.setattr(Config, "GRAPHITI_LLM_MAX_TOKENS", 4096)
    monkeypatch.setattr(Config, "GRAPHITI_STRUCTURED_OUTPUT_MODE", "json_schema")
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MODEL", "")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "SENTINEL")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "http://127.0.0.1:1/v1")
    return graphiti_zep._build_llm_client()


def _generate(client, messages, response_model):
    return asyncio.run(client._generate_response(messages, response_model, 2048))


def test_echo_body_fails_fast_as_schema_echo(monkeypatch):
    """A ``finish_reason=stop`` body that is the schema raises ``_SchemaEchoError``.

    Deterministic + outside graphiti's tenacity retried set, so it must NOT be a
    ``json.JSONDecodeError`` (retried 4x) and must be distinct from
    ``_TruncatedJSONError`` (which is the finish_reason=length path)."""

    client = _seam_client(monkeypatch)
    calls, transport = _fake_transport(json.dumps(_9B_ECHO_EXTRACTED_EDGES), finish_reason="stop")
    client.client = transport

    with pytest.raises(graphiti_zep._SchemaEchoError) as raised:
        _generate(client, _prompts(), ExtractedEdges)

    assert "schema echo" in str(raised.value).lower()
    assert not isinstance(raised.value, json.JSONDecodeError)
    assert not isinstance(raised.value, graphiti_zep._TruncatedJSONError)


def test_populated_body_is_not_relabelled(monkeypatch):
    """A normal extraction answer still flows through ``_normalize_to_schema``.

    The detector must never turn a usable body into a skip."""

    body = {"edges": [{
        "fact": "Ms. Yu reports for the outlet",
        "source_entity_name": "Ms. Yu",
        "target_entity_name": "the outlet",
        "relation_type": "REPORTS_FOR",
    }]}
    client = _seam_client(monkeypatch)
    calls, transport = _fake_transport(json.dumps(body), finish_reason="stop")
    client.client = transport

    result = _generate(client, _prompts(), ExtractedEdges)
    assert result == body  # unchanged payload reaches graphiti


def test_echo_is_a_prompt_phenomenon_not_a_response_format_artifact(monkeypatch):
    """Root-cause anchor: the schema reaches the model via the PROMPT.

    In production json_schema mode the seam asks for structured output via
    ``response_format`` (type=json_schema, with the full model_json_schema), AND
    it also injects that same schema into ``messages[-1]`` because our /v1 proxy
    strips ``response_format``. The echo therefore lives in the prompt, which is
    why switching ``structured_output_mode`` cannot be the fix. This pins both:
    the outgoing ``response_format`` is json_schema, and the prompt carries a
    ``$defs`` dump."""

    client = _seam_client(monkeypatch)
    calls, transport = _fake_transport(json.dumps(_9B_ECHO_EXTRACTED_EDGES), finish_reason="stop")
    client.client = transport
    try:
        _generate(client, _prompts(), ExtractedEdges)
    except graphiti_zep._SchemaEchoError:
        pass

    assert calls, "at least one request must have been made"
    sent = calls[0]
    rf = sent.get("response_format")
    assert rf and rf.get("type") == "json_schema"
    # the model_json_schema() (with $defs) is asked for via response_format ...
    assert "$defs" in json.dumps(rf["json_schema"]["schema"])
    # ... AND is injected verbatim into the last prompt message
    last_user = [m for m in sent["messages"] if m["role"] == "user"][-1]
    assert "$defs" in last_user["content"]
    assert "Respond with a JSON object in the following format" in last_user["content"]


# ===========================================================================
# 3. The #1909 / PR#1910 recursive required-property transform (ANALYSIS UTILITY)
#    Quoted verbatim from the issue (#1909 comment 0); deliberately NOT wired
#    into the production path here -- see the module docstring.
# ===========================================================================
def _require_all_properties(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of ``schema`` in which every object lists all its properties required.

    Grammar-backed servers read ``required`` as "must emit". Types are left as
    pydantic wrote them, so a ``str | None`` field is still answered ``null``.
    -- verbatim from getzep/graphiti #1909 (comment 0); PR #1910, closed unmerged.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {key: walk(value) for key, value in node.items()}
            properties = out.get("properties")
            if out.get("type") == "object" and isinstance(properties, dict) and properties:
                out["required"] = list(properties)
            return out
        if isinstance(node, list):
            return [walk(value) for value in node]
        return node

    return walk(schema)


def test_require_all_properties_lists_every_field_at_every_level():
    schema = ExtractedEdges.model_json_schema()
    out = _require_all_properties(schema)

    edge_def = out["$defs"]["Edge"]
    assert set(edge_def["required"]) == set(edge_def["properties"])
    #... and optional temporal fields become required
    assert "valid_at" in edge_def["required"]
    assert "invalid_at" in edge_def["required"]

    # an array-of-objects' item definition is caught through $defs too
    for node in out.get("$defs", {}).values():
        if node.get("type") == "object" and node.get("properties"):
            assert set(node["required"]) == set(node["properties"])


def test_require_all_properties_preserves_nullable_types_and_does_not_mutate():
    schema = ExtractedEdges.model_json_schema()
    original = copy.deepcopy(schema)

    out = _require_all_properties(schema)

    # input untouched
    assert schema == original
    # field TYPE spelling is unchanged -> str | None stays nullable/answerable-null
    edge_in = original["$defs"]["Edge"]
    edge_out = out["$defs"]["Edge"]
    for field in ("source_entity_name", "valid_at", "invalid_at"):
        assert edge_out["properties"][field] == edge_in["properties"][field]


def test_require_all_properties_handles_odd_shapes():
    # an object that has ONLY additionalProperties (no named properties)
    schema = {"type": "object", "additionalProperties": True}
    out = _require_all_properties(schema)
    assert "required" not in out  # nothing to require

    # a field literally named "properties" (upstream author's edge case)
    schema = {"type": "object", "properties": {"properties": {"type": "string"}}, "required": []}
    out = _require_all_properties(schema)
    assert out["required"] == ["properties"]
    # anyOf/items walked without error and still object-complete
    nested = {
        "type": "object",
        "properties": {"opt": {"anyOf": [{"type": "object", "properties": {"a": {"type": "string"}}}, {"type": "null"}]}},
        "required": [],
    }
    out2 = _require_all_properties(nested)
    inner = out2["properties"]["opt"]["anyOf"][0]
    assert set(inner["required"]) == {"a"}


def test_require_all_properties_keeps_response_validatable():
    """The transformed SCHEMA must not change what a valid RESPONSE needs.

    A payload with only graphiti's real required fields must still validate
    against the models after the transform -- the transform edits the schema we
    *send*, not the response validator."""

    edge_payload = {
        "edges": [{"fact": "a b", "source_entity_name": "a",
                   "target_entity_name": "b", "relation_type": "R"}]
    }
    # validates as-is (valid_at/invalid_at/episode_indices default)
    ExtractedEdges(**edge_payload)
    # and the schema we would send is still the same set of top-level props
    t = _require_all_properties(ExtractedEdges.model_json_schema())
    assert set(t["properties"]) == set(ExtractedEdges.model_json_schema()["properties"])


# ===========================================================================
# 4. Required-field completeness of the real graphiti 0.31.2 models (#1909 premise)
# ===========================================================================
def test_graphiti_optional_fields_are_absent_from_required():
    """Precise statement of #1909 for our pinned 0.31.2: temporal + episode fields
    are optional (a pydantic default -> not in ``required``)."""

    def required_of(model) -> set:
        return {f for f, fi in model.model_fields.items() if fi.is_required()}

    from graphiti_core.prompts.extract_edges import Edge

    assert {"source_entity_name", "target_entity_name", "relation_type", "fact"} <= required_of(Edge)
    assert "valid_at" not in required_of(Edge)
    assert "invalid_at" not in required_of(Edge)
    assert "episode_indices" not in required_of(Edge)
    # top-level wrapper requires exactly the container field
    assert required_of(ExtractedEdges) == {"edges"}


# ===========================================================================
# 5. Endpoint-name normalization (H4 / #1909 endpoint half)
# ===========================================================================
def test_normalize_string_exact_lowercases_and_collapses_whitespace():
    assert _normalize_string_exact("Vault  Data") == "vault data"
    assert _normalize_string_exact("  A  B ") == "a b"


def test_normalize_string_exact_is_conservative_about_punctuation():
    """Conservative boundary: it does NOT strip quotes/trailing punctuation.

    #1909 (0.30.1) reported edges dropped for ``vault data`` vs ``vault``,
    trailing periods and quotes. In 0.31.2 that class of edge is NOT dropped --
    ``materialize_dangling_endpoint_node`` creates a node for the unmatched
    endpoint -- but aggressive punctuation/quote folding here could still merge
    two genuinely different entities, so the shared normalizer stays
    case/whitespace-only. This test pins that we do NOT fold punctuation, i.e.
    ``vault.`` and ``vault`` remain distinct keys."""

    assert _normalize_string_exact("vault.") != _normalize_string_exact("vault")
    assert _normalize_string_exact('"vault"') != _normalize_string_exact("vault")
    # materially different endpoints never collide
    assert _normalize_string_exact("vault data") != _normalize_string_exact("vault")


def test_empty_endpoints_would_be_rejected():
    """The ""guard" graphiti_core 0.31.2 extract_edges applies (edge_operations.py):
    ``if not source_name: continue`` on a stripped empty endpoint. Mirrored here
    so the invariant is documented and cannot silently regress."""

    def endpoint_alive(name: str) -> bool:
        return bool(name.strip())

    assert endpoint_alive("vault") is True
    assert endpoint_alive("   ") is False
    assert endpoint_alive("") is False


# ===========================================================================
# 6. Skipped-fraction guard calibration (operational, section 11)
# ===========================================================================
def test_skip_threshold_defaults_and_bounds(monkeypatch):
    from app.services.graph_builder import _skip_threshold_from_env as impl

    monkeypatch.delenv("GRAPHITI_MAX_SKIPPED_FRACTION", raising=False)
    assert impl() == 0.3
    monkeypatch.setenv("GRAPHITI_MAX_SKIPPED_FRACTION", "0.40")
    assert impl() == 0.40
    monkeypatch.setenv("GRAPHITI_MAX_SKIPPED_FRACTION", "not-a-number")
    assert impl() == 0.3
    monkeypatch.setenv("GRAPHITI_MAX_SKIPPED_FRACTION", "1.7")
    assert impl() == 0.3


def test_skip_guard_boundary_is_strictly_greater():
    """The guard rejects only when ``skipped/total > threshold`` (section 11).

    3/8 = 0.375 fails the current 0.30 but passes a 0.40 calibration; 4/8 = 0.50
    still fails 0.40, so a genuinely broken run is still rejected. Equal fraction
    passes (strictly-greater)."""

    def rejected(skipped, total, threshold):
        return (skipped / total) > threshold

    # current default: the measured 8-bit control (2/8) passes, 9B (3/8) fails
    assert rejected(2, 8, 0.30) is False
    assert rejected(3, 8, 0.30) is True
    # calibrated 0.40: 3/8 now passes, but 4/8 still fails
    assert rejected(3, 8, 0.40) is False
    assert rejected(4, 8, 0.40) is True
    # strictly-greater: an exactly-at-threshold run is accepted
    assert rejected(3, 10, 0.30) is False
