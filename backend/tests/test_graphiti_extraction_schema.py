# SPDX-License-Identifier: AGPL-3.0-or-later
"""#22: the local Graphiti seam must hand graphiti schema-conforming JSON.

The §12.4 acceptance run skipped 19/19 chunks: the assigned small model
(``LFM3-1.7B-MLX-4bit``) cannot answer graphiti's real extraction prompts.
Three failure classes were recorded — malformed JSON, valid JSON carrying the
WRONG key names (``{"entities": [...]}`` instead of graphiti's
``{"extracted_entities": [{"name": ..., "entity_type_id": ...}]}``), and a
SIGTERM artifact owned by another worker.

Root cause: ``GRAPHITI_STRUCTURED_OUTPUT_MODE=json_schema`` puts the schema only
into ``response_format``, which our ``/v1`` proxy strips
(``app/models/manager.py``), so the model never sees the key names graphiti will
validate. The seam in ``app/utils/graphiti_zep.py`` therefore:

* **R0** logs the raw body (bounded) plus ``finish_reason`` before parsing —
  the raw output was never persisted anywhere before;
* **R2** appends graphiti's own schema reminder to the prompt, so the schema
  reaches the model whatever the mode env var says;
* classifies an unparseable body by ``finish_reason`` — only a genuine
  ``length`` finish is a truncation (terminal ``_TruncatedJSONError``, M3);
  a generation defect propagates as ``json.JSONDecodeError`` so graphiti's own
  tenacity wrapper may retry it;
* **R3** renames unambiguous key aliases only, and never invents a value.

Everything here is offline: no model, no network, no Kuzu. The transport is a
fake, the repaired payloads are graphiti's own pydantic classes, and the prompts
are graphiti's own rendered prompts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from typing import List

import pytest
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate
from graphiti_core.prompts.dedupe_nodes import NodeResolutions
from graphiti_core.prompts.extract_edges import ExtractedEdges
from graphiti_core.prompts.extract_nodes import ExtractedEntities, SummarizedEntities
from graphiti_core.prompts.lib import prompt_library
from graphiti_core.prompts.models import Message
from pydantic import BaseModel, ValidationError

from app.config import Config
from app.utils import graphiti_zep
from app.utils.graphiti_zep import GraphitiZepClient, _TruncatedJSONError

# The prose graphiti itself injects in json_object mode
# (graphiti_core/llm_client/openai_generic_client.py:197-204). The seam reuses
# it verbatim so its reminder is indistinguishable from graphiti's own.
_SCHEMA_REMINDER = "\n\nRespond with a JSON object in the following format:\n\n"

# The sentence the seam appends AFTER graphiti's block: the item shape
# model_json_schema() hides behind $defs/$ref, restated for the 1.7B model.
_ITEM_REQUIREMENTS_SENTENCE = (
    'every item of "extracted_entities" must contain name (string), '
    'entity_type_id (integer). Omit any item that lacks one of its required '
    'fields.'
)

# The plain-English statement the seam sends instead of graphiti's raw dump for
# the ECHO-PRONE response models (NodeResolutions, EdgeDuplicate — see
# _ECHO_PRONE_RESPONSE_MODELS and the dedupe-split section below): the schema's
# own top-level key, its own JSON type and its own required set, in words.
# Measured cause: these models answer a raw dump with the dump itself — 3 of 5
# NodeResolutions on the 1.7B ({"$defs": {"NodeDuplicate": ...}), 5 of 5
# EdgeDuplicate on the 9B ({"properties": {"duplicate_facts": []}}) — each a
# wrong-keys skip (scratch/final_full.log, scratch/check24_9b_full_b.log).
_ENTITIES_PLAIN_STATEMENT = (
    'An object with the required key "extracted_entities", holding an array of '
    "objects."
)
_RESOLUTIONS_PLAIN_STATEMENT = (
    'An object with the required key "entity_resolutions", holding an array of '
    "objects."
)
_EDGE_DEDUPE_PLAIN_STATEMENT = (
    'An object with the required keys "duplicate_facts", holding an array of '
    'integers and "contradicted_facts", holding an array of integers.'
)

# The shape clause the seam appends for EVERY response model: ONE compact line
# of JSON, nothing else, no commentary anywhere inside the object — not merely
# inside a value, which is where the model put it (a "// Wait, ..." line between
# two array items, scratch/final_node.log).
_SHAPE_SENTENCE = (
    "Return that object as ONE compact line of JSON: no indentation, no line "
    "breaks, no markdown code fences, no prose before or after it, and no "
    "comments or reasoning anywhere inside it — not between its items and not "
    "inside a value."
)

# Only emitted when the schema actually carries a "fact" field (graphiti's Edge).
_TERSE_FACT_SENTENCE = (
    'Write each "fact" as a short phrase naming the relation, not a sentence '
    "copied from the text, and keep every concrete noun and number from the "
    "source."
)

# Only emitted when the schema actually carries valid_at/invalid_at (Edge).
_TIMESTAMP_SENTENCE = (
    'Keep "valid_at" and "invalid_at" null unless the text states or resolves '
    "a date or time; never invent one, and write any timestamp at second "
    "precision (YYYY-MM-DDTHH:MM:SSZ)."
)

_TERMINAL_PREFIX = "truncated/unparseable JSON response (not retried): "

# The ontology of the failing acceptance run (Entity 0, then the declared types).
_ENTITY_TYPES = [
    {
        "entity_type_id": 0,
        "entity_type_name": "Entity",
        "entity_type_description": "A specific, identifiable entity.",
    },
    {
        "entity_type_id": 1,
        "entity_type_name": "Person",
        "entity_type_description": "A named individual.",
    },
    {
        "entity_type_id": 2,
        "entity_type_name": "Organization",
        "entity_type_description": "A company, agency or institution.",
    },
    {
        "entity_type_id": 3,
        "entity_type_name": "Location",
        "entity_type_description": "A city, region or facility.",
    },
]

_EPISODE = (
    "Section 3: Dr. Ada Chen joined the Wuhan Institute of Weather Modelling in "
    "2018 as a senior researcher. The institute is located in Wuhan, Hubei."
)


def _nodes_prompt():
    """Graphiti's real node-extraction prompt for one text episode."""

    return prompt_library.extract_nodes.extract_text(
        {
            "episode_content": _EPISODE,
            "entity_types": _ENTITY_TYPES,
            "previous_episodes": [],
            "custom_extraction_instructions": "",
            "reference_time": "2026-11-07T00:00:00Z",
        }
    )


def _edges_prompt():
    """Graphiti's real edge-extraction prompt for one text episode."""

    return prompt_library.extract_edges.edge(
        {
            "previous_episodes": [],
            "episode_content": _EPISODE,
            "nodes": [{"name": "Dr. Ada Chen", "entity_type_id": 1}],
            "edge_types": [],
            "custom_extraction_instructions": "",
            "reference_time": "2026-11-07T00:00:00Z",
            "strict_edge_types": False,
        }
    )


def _resolutions_prompt():
    """Graphiti's real node-dedupe prompt (no ENTITY TYPES block)."""

    return prompt_library.dedupe_nodes.node(
        {
            "previous_episodes": [],
            "episode_content": _EPISODE,
            "extracted_node": {"id": 0, "name": "Dr. Ada Chen"},
            "entity_type_description": "A named individual.",
            "existing_nodes": [{"candidate_id": 1, "name": "Dr. Ada Chen"}],
        }
    )


def _transport(body, finish_reason="stop"):
    """Fake OpenAI transport: records the request kwargs, answers one body."""

    calls = []

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

    return calls, SimpleNamespace(create=_create)


def _client(body, *, finish_reason="stop", mode="json_schema", monkeypatch):
    """The production client object under test, with a fake transport.

    Config is patched to a known state; ``LLM_MODEL_NAME`` is a sentinel so a
    test can prove the R0 log describes the RESPONSE and never the request."""

    monkeypatch.setattr(Config, "GRAPHITI_LLM_MAX_TOKENS", 4096)
    monkeypatch.setattr(Config, "GRAPHITI_STRUCTURED_OUTPUT_MODE", mode)
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MODEL", "")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "REQUEST_SENTINEL_MODEL")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "http://127.0.0.1:1/v1")
    client = graphiti_zep._build_llm_client()
    calls, completions = _transport(body, finish_reason)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, calls


def _generate(client, messages, response_model=None, max_tokens=2048):
    return asyncio.run(
        client._generate_response(messages, response_model, max_tokens)
    )


class _WarningCollector(logging.Handler):
    """Collect the WARNING records the seam logger emits."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def _captured_schema_warnings():
    """Capture ``mirofish.graphiti_zep`` warnings for the duration of a block.

    The app logger sets ``propagate = False`` (app/utils/logger.py), so pytest's
    ``caplog`` never sees its records; a direct handler does."""

    log = logging.getLogger("mirofish.graphiti_zep")
    collector = _WarningCollector()
    log.addHandler(collector)
    try:
        yield collector.records
    finally:
        log.removeHandler(collector)


# ---------------------------------------------------------------------------
# M1 — the per-call max_tokens clamp must survive every change
# ---------------------------------------------------------------------------


def test_max_tokens_clamped_to_config_cap(monkeypatch):
    # graphiti's edge path asks for extract_edges_max_tokens = 16384
    # (edge_operations.py:166); the leaf clamp must still win.
    client, calls = _client('{"anything": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Return JSON")], max_tokens=16384)

    assert calls[0]["max_tokens"] == 4096


def test_max_tokens_below_cap_is_passed_through(monkeypatch):
    client, calls = _client('{"anything": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Return JSON")], max_tokens=1024)

    assert calls[0]["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# R2 — the schema reminder reaches the model
# ---------------------------------------------------------------------------


def test_schema_reminder_appended_for_a_response_model(monkeypatch):
    client, calls = _client('{"extracted_entities": []}', monkeypatch=monkeypatch)
    messages = [
        Message(role="system", content="You are an entity extraction specialist."),
        Message(role="user", content="Extract entities."),
    ]

    result = _generate(client, messages, ExtractedEntities, 2048)

    sent = calls[0]["messages"]
    assert result == {"extracted_entities": []}
    # an EXTRACTION prompt keeps graphiti's verbatim dump: it is the only
    # compact one-line exemplar the model gets and no measured model ever echoed
    # it (#24 dropped it and the 1.7B pretty-printed 10x slower, granite-4.2-3b
    # lost the wrapper entirely — MODEL_EVAL §7c)
    assert _SCHEMA_REMINDER + json.dumps(ExtractedEntities.model_json_schema()) in sent[
        -1
    ]["content"]
    assert "extracted_entities" in sent[-1]["content"]
    assert sent[0]["content"] == "You are an entity extraction specialist."
    # the shape clause is now the closing sentence (R2 follow-up)
    assert sent[-1]["content"].endswith(_SHAPE_SENTENCE)
    assert _ITEM_REQUIREMENTS_SENTENCE + "\n\n" + _SHAPE_SENTENCE in sent[-1]["content"]


def test_schema_reminder_absent_without_a_response_model(monkeypatch):
    client, calls = _client('{"anything": 1}', monkeypatch=monkeypatch)
    content = "Extract entities and return JSON."

    _generate(client, [Message(role="user", content=content)])

    assert calls[0]["messages"][-1]["content"] == content


def test_graphitis_json_object_reminder_is_not_duplicated(monkeypatch):
    # In json_object mode graphiti's own generate_response already appends the
    # reminder; the seam must recognise the string and leave it alone.
    client, calls = _client(
        '{"extracted_entities": []}', mode="json_object", monkeypatch=monkeypatch
    )
    messages = [
        Message(role="system", content="You are an entity extraction specialist."),
        Message(role="user", content="Extract entities."),
    ]

    result = asyncio.run(client.generate_response(messages, ExtractedEntities, 2048))

    assert result == {"extracted_entities": []}
    assert calls[0]["messages"][-1]["content"].count(_SCHEMA_REMINDER) == 1


def test_schema_reminder_appended_once_across_retries(monkeypatch):
    # The vendored tenacity wrapper re-enters _generate_response with the same
    # mutated message list; the reminder must not accumulate per attempt.
    client, _calls = _client('{"extracted_entities": []}', monkeypatch=monkeypatch)
    messages = [Message(role="user", content="Extract entities.")]

    _generate(client, messages, ExtractedEntities, 2048)
    _generate(client, messages, ExtractedEntities, 2048)

    assert messages[-1].content.count(_SCHEMA_REMINDER) == 1


def test_schema_reminder_is_mode_independent(monkeypatch):
    client, calls = _client(
        '{"extracted_entities": []}', mode="json_schema", monkeypatch=monkeypatch
    )

    _generate(client, [Message(role="user", content="Extract entities.")], ExtractedEntities, 2048)

    assert _SCHEMA_REMINDER in calls[0]["messages"][-1]["content"]


# ---------------------------------------------------------------------------
# R2 follow-up — the reminder must be readable by the small model
# ---------------------------------------------------------------------------
#
# Measured on the merged fix (scratch/after_fix_node.log, H2 --repeat 2): 11/12
# node calls passed, but chunk 0 still failed once with
# ``extracted_entities.5.entity_type_id Field required`` — the raw body's last
# element was ``{"name": "Mr. [director?]"}``, no type key at all (chunk 0's
# text ends on the dangling fragment "The bureau's director, Mr."). No alias
# rule can name a key the model never emitted, so the fix has to be at the
# prompt: ``model_json_schema()`` hides the per-item shape behind ``$defs``
# plus a ``$ref`` pointer that a 1.7B model does not resolve.


@pytest.mark.parametrize(
    "response_model, schema_block, item_sentence, extra_clauses",
    [
        (
            ExtractedEntities,
            json.dumps(ExtractedEntities.model_json_schema()),
            'every item of "extracted_entities" must contain name (string), '
            'entity_type_id (integer). Omit any item that lacks one of its '
            'required fields.',
            "",
        ),
        (
            ExtractedEdges,
            json.dumps(ExtractedEdges.model_json_schema()),
            'every item of "edges" must contain source_entity_name (string), '
            'target_entity_name (string), relation_type (string), fact (string). '
            'Omit any item that lacks one of its required fields.',
            "\n\n" + _TERSE_FACT_SENTENCE + "\n\n" + _TIMESTAMP_SENTENCE,
        ),
        (
            NodeResolutions,
            _RESOLUTIONS_PLAIN_STATEMENT,
            'every item of "entity_resolutions" must contain id (integer), '
            'name (string), duplicate_candidate_id (integer). Omit any item that '
            'lacks one of its required fields.',
            "",
        ),
    ],
)
def test_schema_reminder_restates_required_item_fields_without_refs(
    monkeypatch, response_model, schema_block, item_sentence, extra_clauses
):
    client, calls = _client('{"x": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Extract.")], response_model, 2048)

    sent = calls[0]["messages"][-1]["content"]
    # the whole appended tail: the schema block this prompt class gets (graphiti's
    # verbatim dump for extraction, the plain statement for the echo-prone dedupe
    # models), the item fields, the shape clauses. Asserted exactly, so the
    # rejected #24 broad swap — and any regression back to the raw dump on
    # NodeResolutions, which the model answers with the schema itself — fails
    # here.
    assert sent == (
        "Extract."
        + _SCHEMA_REMINDER
        + schema_block
        + "\n\n"
        + item_sentence
        + "\n\n"
        + _SHAPE_SENTENCE
        + extra_clauses
    )
    # the added sentence is plain language, not a second $ref chain
    assert "$ref" not in item_sentence
    assert "$defs" not in item_sentence
    # and a dedupe prompt never carries a raw dump
    if response_model is NodeResolutions:
        assert "$defs" not in sent
        assert "$ref" not in sent


def test_element_requirements_absent_without_array_items(monkeypatch):
    class _FlatResponse(BaseModel):
        answer: str

    client, calls = _client('{"answer": "x"}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Answer.")], _FlatResponse, 2048)

    # nothing to restate: graphiti's reminder plus the shape clause is the whole
    # tail; no item sentence and no schema-specific clause were added
    sent = calls[0]["messages"][-1]["content"]
    assert sent.endswith(
        _SCHEMA_REMINDER
        + json.dumps(_FlatResponse.model_json_schema())
        + "\n\n"
        + _SHAPE_SENTENCE
    )
    assert "must contain" not in sent
    assert _TERSE_FACT_SENTENCE not in sent
    assert _TIMESTAMP_SENTENCE not in sent

# ---------------------------------------------------------------------------
# The dedupe split — plain English for the models that answer with the SCHEMA
# ---------------------------------------------------------------------------
#
# Measured on both models, in --mode full (the six real chunks, fresh graph ids,
# Ollama stopped):
#
#   LFM3-1.7B-MLX-4bit  NodeResolutions  3 of 5 calls answered {"$defs":
#                       {"NodeDuplicate": …}} — a wrong-keys skip
#                       (scratch/final_full.log). With the statement, 0 of 4
#                       calls echoed it (scratch/check24_1p7b_full_a.log): real
#                       resolution data with the schema's own key names.
#   Ornith-1.5-9B-MLX-6bit  EdgeDuplicate  5 of 5 calls answered {"properties":
#                       {"duplicate_facts": []}} (scratch/check24_9b_full_b.log)
#                       while every EXTRACTION prompt it answered was correct,
#                       including the ExtractedEdges the 1.7B cannot fit in M1's
#                       clamp.
#
# Both are DEDUPE prompts: the wanted answer is a short list of ids, and the
# dump is a plausible short answer, so the model hands it back. No EXTRACTION
# prompt has ever echoed its schema on any measured model, and those prompts
# need the dump — it is their only compact exemplar, and losing it is what made
# the plain-statement-everywhere variant (#24, MODEL_EVAL §7c) fail. So the seam
# splits by response model: statement for NodeResolutions/EdgeDuplicate, graphiti's
# verbatim dump for everything else.


@pytest.mark.parametrize(
    "response_model, statement",
    [
        (NodeResolutions, _RESOLUTIONS_PLAIN_STATEMENT),
        (EdgeDuplicate, _EDGE_DEDUPE_PLAIN_STATEMENT),
    ],
)
def test_raw_schema_dump_is_dropped_for_an_echo_prone_model(
    monkeypatch, response_model, statement
):
    # The whole appended tail, asserted exactly: no $defs, no $ref, and the
    # plain statement followed by the item sentence and the shape clauses.
    client, calls = _client('{"x": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Extract.")], response_model, 2048)

    sent = calls[0]["messages"][-1]["content"]
    assert _SCHEMA_REMINDER + statement in sent
    assert "$defs" not in sent
    assert "$ref" not in sent
    assert sent.count(_SCHEMA_REMINDER) == 1
    # the required item fields are still restated for a model that has object
    # items (EdgeDuplicate's are plain integers: nothing to restate, exactly as
    # test_element_requirements_absent_without_array_items pins)
    if response_model is NodeResolutions:
        assert "Omit any item that lacks one of its required fields." in sent


@pytest.mark.parametrize(
    "response_model",
    [ExtractedEntities, ExtractedEdges, SummarizedEntities],
)
def test_extraction_prompts_keep_graphitis_verbatim_dump(response_model, monkeypatch):
    # The complement, pinned just as hard: the extraction prompts are the ones
    # the 1.7B answers correctly only with the dump in front of them (12/12 node
    # calls at a 7.9 s mean), so reintroducing the rejected #24 broad swap fails
    # here too.
    client, calls = _client('{"x": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Extract.")], response_model, 2048)

    sent = calls[0]["messages"][-1]["content"]
    assert _SCHEMA_REMINDER + json.dumps(response_model.model_json_schema()) in sent
    assert "An object with the" not in sent


def test_plain_statement_is_read_from_the_schema_not_hand_copied():
    # A response model graphiti does not define: the statement must still name
    # ITS key and ITS item fields, which only a schema walk can know.
    class _Thing(BaseModel):
        label: str
        weight: int

    class _ThingsResponse(BaseModel):
        things: List[_Thing]

    schema = _ThingsResponse.model_json_schema()
    statement = graphiti_zep._plain_schema_statement(_ThingsResponse, schema)

    assert (
        statement
        == 'An object with the required key "things", holding an array of objects.'
    )
    assert (
        'every item of "things" must contain label (string), weight (integer). '
        "Omit any item that lacks one of its required fields."
        in graphiti_zep._element_requirements(_ThingsResponse)
    )


def test_a_flat_schema_dump_is_kept_verbatim(monkeypatch):
    # A schema that is small and carries no $defs/$ref, and is not echo-prone:
    # graphiti's own block is reproduced byte for byte (the plain statement is
    # not added on top of it).
    class _FlatResponse(BaseModel):
        answer: str

    client, calls = _client('{"answer": "x"}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Answer.")], _FlatResponse, 2048)

    sent = calls[0]["messages"][-1]["content"]
    assert sent.count(_SCHEMA_REMINDER) == 1
    assert "An object with the" not in sent
    assert sent.endswith(
        _SCHEMA_REMINDER
        + json.dumps(_FlatResponse.model_json_schema())
        + "\n\n"
        + _SHAPE_SENTENCE
    )


def test_plain_statement_is_appended_once_across_retries(monkeypatch):
    client, _calls = _client('{"entity_resolutions": []}', monkeypatch=monkeypatch)
    messages = [Message(role="user", content="Resolve duplicates.")]

    _generate(client, messages, NodeResolutions, 2048)
    _generate(client, messages, NodeResolutions, 2048)

    content = messages[-1].content
    assert content.count(_RESOLUTIONS_PLAIN_STATEMENT) == 1
    assert content.count(_SHAPE_SENTENCE) == 1
    assert "$defs" not in content


def test_json_object_mode_keeps_graphitis_block_and_adds_only_the_tail(monkeypatch):
    # In json_object mode graphiti itself appends its raw dump; the seam must
    # recognise that block and append ONLY its own clauses, never a second
    # statement of the schema.
    client, calls = _client(
        '{"extracted_entities": []}', mode="json_object", monkeypatch=monkeypatch
    )
    messages = [
        Message(role="system", content="You are an entity extraction specialist."),
        Message(role="user", content="Extract entities."),
    ]

    asyncio.run(client.generate_response(messages, ExtractedEntities, 2048))

    sent = calls[0]["messages"][-1]["content"]
    assert sent.count(_SCHEMA_REMINDER) == 1
    assert _ENTITIES_PLAIN_STATEMENT not in sent
    assert sent.endswith(
        _ITEM_REQUIREMENTS_SENTENCE + "\n\n" + _SHAPE_SENTENCE
    )


# ---------------------------------------------------------------------------
# Prompt shape — the response must be compact and commentary-free (final fix)
# ---------------------------------------------------------------------------
#
# Measured with the schema reminder in place (scratch/after_fix_full.log, H1
# --repeat 1): node extraction was healthy, but ExtractedEdges hit the 4096-token
# leaf clamp — finish_reason=length on a ~14 000-char PRETTY-PRINTED body whose
# "fact" values were full sentences and whose valid_at carried an invented
# microsecond timestamp — so chunks 0/1/2 were skipped by _TruncatedJSONError.
# M1 forbids raising the cap, so the prompt now states the response SHAPE. Every
# extra clause is derived from the response model's own schema: a "fact" clause
# only for a model that has one, the timestamp clause only for valid_at/
# invalid_at, and nothing at all for a model that has neither.


def test_shape_instruction_asks_for_one_compact_line_of_json(monkeypatch):
    client, calls = _client('{"extracted_entities": []}', monkeypatch=monkeypatch)

    _generate(
        client, [Message(role="user", content="Extract.")], ExtractedEntities, 2048
    )

    sent = calls[0]["messages"][-1]["content"]
    # the whole appended tail: graphiti's block, the item fields, the shape clause
    block = _SCHEMA_REMINDER + json.dumps(ExtractedEntities.model_json_schema())
    assert sent == (
        "Extract."
        + block
        + "\n\n"
        + _ITEM_REQUIREMENTS_SENTENCE
        + "\n\n"
        + _SHAPE_SENTENCE
    )


@pytest.mark.parametrize(
    "response_model, expectation",
    [
        (ExtractedEdges, True),
        (ExtractedEntities, False),
        (NodeResolutions, False),
    ],
)
def test_terse_fact_clause_follows_the_schema(monkeypatch, response_model, expectation):
    client, calls = _client('{"x": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Extract.")], response_model, 2048)

    sent = calls[0]["messages"][-1]["content"]
    assert (_TERSE_FACT_SENTENCE in sent) is expectation


@pytest.mark.parametrize(
    "response_model, expectation",
    [
        (ExtractedEdges, True),
        (ExtractedEntities, False),
    ],
)
def test_timestamp_clause_follows_the_schema(monkeypatch, response_model, expectation):
    client, calls = _client('{"x": 1}', monkeypatch=monkeypatch)

    _generate(client, [Message(role="user", content="Extract.")], response_model, 2048)

    sent = calls[0]["messages"][-1]["content"]
    assert (_TIMESTAMP_SENTENCE in sent) is expectation


def test_shape_instruction_is_appended_once_across_retries(monkeypatch):
    # The vendored tenacity wrapper re-enters _generate_response with the same
    # mutated message list; the shape clause must not accumulate per attempt.
    client, _calls = _client('{"extracted_entities": []}', monkeypatch=monkeypatch)
    messages = [Message(role="user", content="Extract entities.")]

    _generate(client, messages, ExtractedEntities, 2048)
    _generate(client, messages, ExtractedEntities, 2048)

    content = messages[-1].content
    assert content.count(_SHAPE_SENTENCE) == 1
    assert content.count(_TERSE_FACT_SENTENCE) == 0


def test_shape_instruction_added_when_graphiti_already_appended_its_block(
    monkeypatch,
):
    # json_object mode: graphiti's own generate_response appends its verbatim
    # block first, so the seam adds only its own clauses, never a second copy
    # of the schema.
    client, calls = _client(
        '{"edges": []}', mode="json_object", monkeypatch=monkeypatch
    )
    messages = [
        Message(role="system", content="You are an expert fact extractor."),
        Message(role="user", content="Extract facts."),
    ]

    result = asyncio.run(
        client.generate_response(messages, ExtractedEdges, 2048)
    )

    assert result == {"edges": []}
    sent = calls[0]["messages"][-1]["content"]
    assert sent.count(_SCHEMA_REMINDER) == 1
    # the shape clauses are the LAST thing in the prompt: the format demand sits
    # next to generation, which is where the multi-line body came from
    assert sent.endswith(
        'every item of "edges" must contain source_entity_name (string), '
        'target_entity_name (string), relation_type (string), fact (string). '
        'Omit any item that lacks one of its required fields.'
        + "\n\n"
        + _SHAPE_SENTENCE
        + "\n\n"
        + _TERSE_FACT_SENTENCE
        + "\n\n"
        + _TIMESTAMP_SENTENCE
    )


def test_shape_instruction_absent_without_a_response_model(monkeypatch):
    client, calls = _client('{"anything": 1}', monkeypatch=monkeypatch)
    content = "Extract entities and return JSON."

    _generate(client, [Message(role="user", content=content)])

    assert calls[0]["messages"][-1]["content"] == content


def test_element_requirements_added_when_graphiti_already_appended_its_block(
    monkeypatch,
):
    # json_object mode: graphiti's own generate_response appends its verbatim
    # block first, so the seam must add ONLY the sentence, never a second copy
    # of the schema (the prompt grows either way).
    client, calls = _client(
        '{"extracted_entities": []}', mode="json_object", monkeypatch=monkeypatch
    )
    messages = [
        Message(role="system", content="You are an entity extraction specialist."),
        Message(role="user", content="Extract entities."),
    ]

    asyncio.run(client.generate_response(messages, ExtractedEntities, 2048))

    sent = calls[0]["messages"][-1]["content"]
    assert sent.count(_SCHEMA_REMINDER) == 1
    # only the seam's own clauses follow, never a second copy of the schema
    assert sent.endswith(
        'every item of "extracted_entities" must contain name (string), '
        'entity_type_id (integer). Omit any item that lacks one of its required '
        'fields.'
        + "\n\n"
        + _SHAPE_SENTENCE
    )


# ---------------------------------------------------------------------------
# Finish-reason-aware classification (M3 preserved)
# ---------------------------------------------------------------------------


def test_truncated_body_is_terminal_and_not_retried(monkeypatch):
    client, _calls = _client(
        '{"extracted_entities": [{"name": "Dr.', finish_reason="length", monkeypatch=monkeypatch
    )

    with pytest.raises(_TruncatedJSONError) as raised:
        _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert str(raised.value).startswith(_TERMINAL_PREFIX)
    # outside tenacity's retried set (client.py:is_server_or_retry_error)
    assert not isinstance(raised.value, json.JSONDecodeError)


def test_malformed_body_with_stop_finish_reason_propagates(monkeypatch):
    # The acceptance run's first failure class: an unescaped quote inside a
    # value at line 20 col 37 — a generation defect, not a truncation. It must
    # NOT be mislabelled as truncation, so graphiti can retry it.
    body = '{"extracted_entities": [{"name": "Dr. Ada Chen"s institute"}]}'
    client, _calls = _client(body, finish_reason="stop", monkeypatch=monkeypatch)

    with pytest.raises(json.JSONDecodeError) as raised:
        _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert not isinstance(raised.value, _TruncatedJSONError)


def test_malformed_body_without_finish_reason_stays_terminal(monkeypatch):
    # A transport that omits finish_reason leaves nothing to reason with, so
    # M3's conservative fail-fast still applies.
    client, _calls = _client(
        '{"extracted_entities": [{"name": "Dr.', finish_reason=None, monkeypatch=monkeypatch
    )

    with pytest.raises(_TruncatedJSONError) as raised:
        _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert str(raised.value).startswith(_TERMINAL_PREFIX)


# ---------------------------------------------------------------------------
# R3 — guarded alias normalisation
# ---------------------------------------------------------------------------


def test_node_aliases_are_normalised_before_validation(monkeypatch):
    payload = {
        "entities": [
            {"text": "Dr. Ada Chen", "entity_type": "Person", "episode_indices": [0]},
            {
                "entity_name": "Wuhan Institute of Weather Modelling",
                "entity_type_name": "Organization",
                "episode_indices": [0],
            },
        ]
    }
    client, _calls = _client(json.dumps(payload), monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == {
        "extracted_entities": [
            {"name": "Dr. Ada Chen", "entity_type_id": 1, "episode_indices": [0]},
            {
                "name": "Wuhan Institute of Weather Modelling",
                "entity_type_id": 2,
                "episode_indices": [0],
            },
        ]
    }
    ExtractedEntities(**result)


def test_edge_aliases_are_normalised_before_validation(monkeypatch):
    payload = {
        "extracted_edges": [
            {
                "source": "Dr. Ada Chen",
                "target": "Wuhan Institute of Weather Modelling",
                "relationship": "WORKS_AT",
                "fact": "Dr. Ada Chen joined as a senior researcher.",
            }
        ]
    }
    client, _calls = _client(json.dumps(payload), monkeypatch=monkeypatch)

    result = _generate(client, _edges_prompt(), ExtractedEdges, 2048)

    assert result == {
        "edges": [
            {
                "source_entity_name": "Dr. Ada Chen",
                "target_entity_name": "Wuhan Institute of Weather Modelling",
                "relation_type": "WORKS_AT",
                "fact": "Dr. Ada Chen joined as a senior researcher.",
            }
        ]
    }
    assert len(ExtractedEdges(**result).edges) == 1


def test_resolution_aliases_are_normalised(monkeypatch):
    payload = {
        "resolutions": [
            {"id": 0, "name": "Dr. Ada Chen", "duplicate_candidate_id": -1}
        ]
    }
    client, _calls = _client(json.dumps(payload), monkeypatch=monkeypatch)

    result = _generate(client, _resolutions_prompt(), NodeResolutions, 2048)

    assert result == {
        "entity_resolutions": [
            {"id": 0, "name": "Dr. Ada Chen", "duplicate_candidate_id": -1}
        ]
    }
    NodeResolutions(**result)


def test_unknown_entity_type_name_leaves_the_id_absent(monkeypatch):
    # "Satellite" is not in the prompt's ENTITY TYPES block: never invent an id,
    # leave it absent so pydantic fails visibly and the chunk is SKIPPED.
    payload = {"entities": [{"text": "Kestrel-3", "entity_type": "Satellite"}]}
    client, _calls = _client(json.dumps(payload), monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result["extracted_entities"][0]["name"] == "Kestrel-3"
    assert "entity_type_id" not in result["extracted_entities"][0]
    with pytest.raises(ValidationError):
        ExtractedEntities(**result)


def test_ambiguous_entity_type_name_leaves_the_id_absent(monkeypatch):
    prompt = prompt_library.extract_nodes.extract_text(
        {
            "episode_content": _EPISODE,
            "entity_types": [
                {"entity_type_id": 0, "entity_type_name": "Entity"},
                {"entity_type_id": 7, "entity_type_name": "Entity"},
                {"entity_type_id": 1, "entity_type_name": "Person"},
            ],
            "previous_episodes": [],
            "custom_extraction_instructions": "",
            "reference_time": "2026-11-07T00:00:00Z",
        }
    )
    payload = {"entities": [{"text": "Dr. Ada Chen", "entity_type": "Entity"}]}
    client, _calls = _client(json.dumps(payload), monkeypatch=monkeypatch)

    result = _generate(client, prompt, ExtractedEntities, 2048)

    assert "entity_type_id" not in result["extracted_entities"][0]
    with pytest.raises(ValidationError):
        ExtractedEntities(**result)


def test_payload_carrying_both_the_alias_and_the_real_key_is_unchanged(monkeypatch):
    payload = {
        "entities": [{"text": "Dr. Ada Chen", "entity_type": "Person"}],
        "extracted_entities": [{"name": "Wuhan", "entity_type_id": 3}],
    }
    body = json.dumps(payload)
    client, _calls = _client(body, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == payload
    assert json.dumps(result) == body


def test_element_with_two_aliases_for_one_field_is_unchanged(monkeypatch):
    payload = {
        "entities": [{"text": "Dr. Ada Chen", "entity_name": "Dr. A. Chen", "entity_type": "Person"}]
    }
    body = json.dumps(payload)
    client, _calls = _client(body, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == payload
    assert json.dumps(result) == body


def test_unrecognised_element_keys_are_left_alone(monkeypatch):
    # Only the listed aliases are mapped: an element the small model keys in its
    # own way keeps its keys, so pydantic still rejects it visibly.
    payload = {"extracted_entities": [{"label": "Dr. Ada Chen", "kind": "Person"}]}
    body = json.dumps(payload)
    client, _calls = _client(body, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == payload


def test_fragment_element_without_any_type_key_is_not_repaired(monkeypatch):
    # The residual #22 body, observed in full on chunk 0 (a text that ends on
    # the dangling fragment "The bureau's director, Mr."):
    #     {"extracted_entities": [ ...5 conforming..., {"name": "Mr. [director?]"}]}
    # There is no alias to rename and no id that may be invented, so the payload
    # must reach graphiti's pydantic untouched and the chunk must be skipped
    # (M2) — never silently defaulted to entity_type_id 0.
    payload = {
        "extracted_entities": [
            {"name": "Dr. Ada Chen", "entity_type_id": 1, "episode_indices": [0]},
            {"name": "Mr. [director?]"},
        ]
    }
    body = json.dumps(payload)
    client, _calls = _client(body, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == payload
    with pytest.raises(ValidationError) as raised:
        ExtractedEntities(**result)
    assert raised.value.errors()[0]["loc"] == ("extracted_entities", 1, "entity_type_id")


@pytest.mark.parametrize(
    "payload",
    [
        '["entities"]',
        '"entities"',
        "17",
    ],
)
def test_non_dict_payloads_are_returned_untouched(monkeypatch, payload):
    client, _calls = _client(payload, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == json.loads(payload)


def test_normalisation_skipped_without_a_response_model(monkeypatch):
    payload = {"entities": [{"text": "Dr. Ada Chen", "entity_type": "Person"}]}
    body = json.dumps(payload)
    client, _calls = _client(body, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), None, 2048)

    assert result == json.loads(body)


@pytest.mark.parametrize(
    "payload",
    [
        # top level: only the alias present
        {"entities": [{"name": "A", "entity_type_id": 1}]},
        # element level: only the alias present
        {
            "extracted_entities": [
                {"entity_name": "A", "entity_type_name": "Person"}
            ]
        },
    ],
)
def test_positive_normalisation_matrix(monkeypatch, payload):
    assert graphiti_zep._normalize_to_schema(
        payload, ExtractedEntities, _nodes_prompt()
    ) != payload


@pytest.mark.parametrize(
    "payload",
    [
        # already conforming
        {"extracted_entities": [{"name": "A", "entity_type_id": 1}]},
        # nothing to rename
        {"something_else": [{"name": "A", "entity_type_id": 1}]},
        # two top-level aliases for one required field
        {
            "entities": [{"name": "A", "entity_type_id": 1}],
            "nodes": [{"name": "B", "entity_type_id": 1}],
        },
        # alias plus the real key (handled as ambiguity, not a rename)
        {
            "entities": [{"name": "A", "entity_type_id": 1}],
            "extracted_entities": [{"name": "B", "entity_type_id": 1}],
        },
        # only top-level LIST elements are mapped, never a nested dict
        {
            "extracted_entities": [{"name": "A", "entity_type_id": 1}],
            "extra": {"text": "B"},
        },
        # an element key that is not a listed alias is left alone
        {"extracted_entities": [{"label": "A", "kind": "Person"}]},
    ],
)
def test_negative_normalisation_matrix(monkeypatch, payload):
    body = json.dumps(payload)

    result = graphiti_zep._normalize_to_schema(
        payload, ExtractedEntities, _nodes_prompt()
    )

    assert result == payload
    assert json.dumps(result) == body


def test_already_conforming_payload_survives_the_seam_unchanged(monkeypatch):
    payload = {
        "extracted_entities": [
            {
                "name": "Dr. Ada Chen",
                "entity_type_id": 1,
                "episode_indices": [0],
            }
        ]
    }
    body = json.dumps(payload)
    client, _calls = _client(body, monkeypatch=monkeypatch)

    result = _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert result == payload
    assert json.dumps(result) == body


# ---------------------------------------------------------------------------
# R0 — bounded instrumentation of the raw model output
# ---------------------------------------------------------------------------


def test_raw_body_is_logged_with_finish_reason(monkeypatch):
    client, _calls = _client(
        '{"extracted_entities": []}', finish_reason="stop", monkeypatch=monkeypatch
    )

    with _captured_schema_warnings() as warnings:
        _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "ExtractedEntities" in text
    assert "2048" in text
    assert "stop" in text
    assert '{"extracted_entities": []}' in text


def test_logged_body_is_bounded_and_the_request_is_never_logged(monkeypatch):
    body = '{"a": "' + "p" * 400 + "SENTINEL_BEYOND_THE_CAP" + '"}'
    client, calls = _client(body, monkeypatch=monkeypatch)

    with _captured_schema_warnings() as warnings:
        _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "p" * 40 in message
    assert "SENTINEL_BEYOND_THE_CAP" not in message
    # the request itself must never be logged
    assert "REQUEST_SENTINEL_MODEL" not in message
    assert "response_format" not in message
    assert _no_request_string_in_log(calls[0], message)


def _no_request_string_in_log(request, message):
    """No string the fake transport was handed may appear in the log.

    ``max_tokens`` is deliberately exempt: R0 requires it (it is the clamped
    value, not a request secret)."""

    for key, value in request.items():
        if key == "max_tokens":
            continue
        if isinstance(value, str) and value and value in message:
            return False
    return True


def test_raw_body_is_logged_before_a_failure_is_raised(monkeypatch):
    body = '{"extracted_entities": [{"name": "Dr.'
    client, _calls = _client(body, finish_reason="length", monkeypatch=monkeypatch)

    with _captured_schema_warnings() as warnings:
        with pytest.raises(_TruncatedJSONError):
            _generate(client, _nodes_prompt(), ExtractedEntities, 2048)

    text = " ".join(record.getMessage() for record in warnings)
    assert "Dr." in text
    assert "ExtractedEntities" in text


# ---------------------------------------------------------------------------
# M2 — the per-chunk fault isolation and the batch terminator are untouched
# ---------------------------------------------------------------------------


def _async_return(value):
    async def _coro(*_args, **_kwargs):
        return value

    return _coro


class _LiveLoop:
    """A live runtime loop: what ``_runtime._loop`` is while ingesting.

    ``_ingest_record`` reads the shared loop before its chunk loop to ask the
    #22 ingest-loop predicate whether the runtime can still run ingestion
    work, so a fake runtime that omits it reports "loop gone" and leaves every
    chunk pending. A stub that answers like a running loop keeps these M2 tests
    about fault isolation, which is what they assert.
    """

    def is_closed(self):
        return False

    def is_running(self):
        return True


def _ingest_client(outcomes):
    """A GraphitiZepClient wired to a fake runtime, no Kuzu, no event loop.

    ``outcomes`` is indexed by chunk: ``None`` succeeds, an exception instance
    fails exactly like graphiti's extraction path does.
    """

    record = {
        "graph_id": "graph-1",
        "batch_id": "batch-1",
        "items": [
            {
                "sequence_index": index,
                "status": "pending",
                "error": None,
                "chunk": "Section 0: Dr. Ada Chen joined the institute.",
            }
            for index in range(len(outcomes))
        ],
        "done": 0,
        "skipped": 0,
        "skipped_indices": [],
    }

    async def _add_episode(**kwargs):
        outcome = outcomes[int(kwargs["name"].rsplit("-", 1)[1])]
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(episode=SimpleNamespace(uuid="episode-uuid"))

    graphiti = SimpleNamespace(add_episode=_add_episode)
    client = object.__new__(GraphitiZepClient)
    client._types_for = lambda graph_id: ([], [], {})
    client._load_record = lambda graph_id, batch_id: record
    client._save_record = lambda saved: None
    client.runtime = SimpleNamespace(
        get_graph=_async_return(graphiti),
        episode_lock=lambda graph_id: asyncio.Lock(),
        _loop=_LiveLoop(),
    )
    return client, record


def test_ingest_record_still_skips_a_failing_chunk(monkeypatch):
    # chunk 0 fails the way the acceptance run's dominant failure class did
    # (graphiti's pydantic validation of a wrong-keyed payload), chunk 1 is the
    # terminal M3 error, chunk 2 succeeds.
    client, record = _ingest_client(
        [
            ValidationError.from_exception_data(
                "ExtractedEntities",
                [
                    {
                        "type": "missing",
                        "loc": ("extracted_entities",),
                        "input": {"entities": []},
                    }
                ],
            ),
            _TruncatedJSONError(f"{_TERMINAL_PREFIX}Expecting ',' delimiter"),
            None,
        ]
    )

    asyncio.run(GraphitiZepClient._ingest_record(client, record))

    assert [item["status"] for item in record["items"]] == [
        "skipped",
        "skipped",
        "succeeded",
    ]
    assert record["done"] == 1
    assert record["skipped"] == 2
    assert record["skipped_indices"] == [0, 1]
    assert record["error"] == "skipped 2/3 chunks: [0, 1]"
    # M2 terminator: any success is terminal-succeeded even with skips
    assert record["status"] == "succeeded"


def test_ingest_record_stays_failed_when_every_chunk_fails(monkeypatch):
    client, record = _ingest_client(
        [
            _TruncatedJSONError("terminal"),
            _TruncatedJSONError("terminal"),
            json.JSONDecodeError("Expecting ',' delimiter", "doc", 12),
        ]
    )

    asyncio.run(GraphitiZepClient._ingest_record(client, record))

    assert record["done"] == 0
    assert record["skipped"] == 3
    assert record["skipped_indices"] == [0, 1, 2]
    assert record["error"] == "skipped 3/3 chunks: [0, 1, 2]"
    assert record["status"] == "failed"
