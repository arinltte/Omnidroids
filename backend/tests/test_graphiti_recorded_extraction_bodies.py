# SPDX-License-Identifier: AGPL-3.0-or-later
"""#22 regression fixture: the real extraction bodies that failed the acceptance run.

The §12.4 acceptance run skipped 19/19 chunks. The baseline harness
(``scratch/graphiti_extraction_check.py``, section B ``--mode node --repeat 2``)
then reproduced the same two failure classes over 12 node-extraction calls and
recorded, per call, the ``keys=`` the body parsed to, its ``finish_reason``, its
``error-class`` and the first 120 chars of the RAW MODEL OUTPUT of
``LiquidAI/LFM3-1.7B-MLX-4bit``. Those bodies existed only in a git-ignored log
— ``.agent-notes/graphiti_extraction_baseline.log``, batch
``local_a84bf4218fe24790``, captured 2026-11-07 — so the #22 fix rested on
manual harness runs. This file commits them as a fixture: the measured
behaviour is now locked in by the suite.

**These bodies are RAW MODEL OUTPUT captured on 2026-11-07, not synthetic.**
Each ``body`` field below is the verbatim first 120 chars of one recorded
response (the harness bound is ``raw_body_head(width=120)``); ``intact`` is the
verbatim head of that same body which stops at an element boundary, and
``closing`` is the bracket work that closes it. The provenance test below is
what keeps that claim checkable instead of a comment: it fails if an ``intact``
head ever stops being a prefix of the ``body`` it was trimmed from. Nothing at
the head is rewritten and no value is invented — the only bytes added are the
brackets the model's own array opened.

The fixture drives each body through the seam's real path
(``_CappedLLMClient._generate_response`` → parse → ``_normalize_to_schema`` →
the alias tables), and asserts the three properties the #22 fix owes:

* **answered** — a body whose recorded shape is ``entities``/``text``/
  ``entity_type`` normalises into graphiti's schema and
  ``ExtractedEntities(**result)`` validates;
* **fail-fast** — a genuinely malformed body (``finish_reason=length``) still
  raises the terminal ``_TruncatedJSONError`` with its exact message prefix, so
  the M3 invariant stays visible;
* **unchanged** — a body whose keys the alias net cannot resolve comes back
  untouched, so graphiti rejects it and M2 skips the chunk *visibly* instead of
  silently graphing a wrong node.

One honest gap is recorded rather than papered over: the model's other wrong
inner key, ``"entity"``, is not in ``_ELEMENT_ALIASES``, so the recorded bodies
that carry it are still skipped after the fix. See
``test_the_recorded_bodies_the_alias_net_cannot_resolve_stay_unchanged``.

Everything here is offline: no model, no network, no Kuzu. The transport is a
fake; the payloads are graphiti's own pydantic classes and the prompts are
graphiti's own rendered prompts.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from graphiti_core.prompts.extract_nodes import ExtractedEntities
from graphiti_core.prompts.lib import prompt_library
from pydantic import ValidationError

from app.config import Config
from app.utils import graphiti_zep
from app.utils.graphiti_zep import _TruncatedJSONError

# The fail-fast message M3 must keep producing for a genuine truncation.
_TERMINAL_PREFIX = "truncated/unparseable JSON response (not retried): "

# The ontology of the failing acceptance run (id 0 is always graphiti's
# catch-all "Entity", then the declared types). Recorded verbatim in the
# failure notes and confirmed by the harness's own
# "entity_type_ids=[0, 1, 2, 3]" line.
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

# The chunk text the recorded bodies were extracted from (one of the six the
# harness copied verbatim from batch local_a84bf4218fe24790; the recorded
# entity names — Dr. Ada Chen, Wuhan Institute of Weather Modelling — all come
# from it, so the prompt can resolve the recorded entity types to their ids).
_EPISODE = (
    "Section 3: Dr. Ada Chen joined the Wuhan Institute of Weather Modelling in "
    "2018 as a senior researcher. The institute is located in Wuhan, Hubei."
)


@dataclass(frozen=True)
class RecordedBody:
    """One recorded LLM response, exactly as the harness persisted it.

    ``error_class``/``finish_reason``/``recorded_error`` are what the harness
    logged about the call; ``body`` is the raw response text it persisted;
    ``intact`` is the verbatim head of ``body`` that ends on a whole element and
    ``closing`` the brackets that close it, so the recorded head can be driven
    through the normalisation path as parsable JSON; ``recovers`` names the
    entities the merged alias net must get out of that head.
    """

    chunk: int
    repetition: int
    error_class: str
    finish_reason: str
    recorded_error: str
    body: str
    intact: str
    closing: str
    recovers: tuple[tuple[str, int], ...]

    @property
    def id(self) -> str:
        return f"chunk{self.chunk}-rep{self.repetition}"


def _body(
    chunk: int,
    repetition: int,
    error_class: str,
    finish_reason: str,
    recorded_error: str,
    body: str,
    intact: str,
    closing: str,
    recovers: tuple[tuple[str, int], ...] = (),
) -> RecordedBody:
    """One recorded row (``_body`` keeps the table readable)."""

    return RecordedBody(
        chunk=chunk,
        repetition=repetition,
        error_class=error_class,
        finish_reason=finish_reason,
        recorded_error=recorded_error,
        body=body,
        intact=intact,
        closing=closing,
        recovers=recovers,
    )


_MALFORMED_ERROR = (
    "truncated/unparseable JSON response (not retried): Expecting ',' delimiter"
)
_WRONG_KEYS_ERROR = (
    "1 validation error for ExtractedEntities extracted_entities Field required"
)

# The twelve recorded node-extraction calls, in the harness's run order. The
# harness ran --repeat 2, so several bodies appear twice; they are kept because
# that is what was measured (the mode is stable, the model does not vary it).
RECORDED_BODIES = (
    # --- repeat 1 ---------------------------------------------------------
    # The two malformed-json rows: the body ran on past the 4096-token clamp and
    # broke mid-value — an unescaped quote at char 412 / char 515. Both carry the
    # answerable text/entity_type shape in their recorded head.
    _body(
        0, 1, "malformed-json", "length",
        _MALFORMED_ERROR + ": line 20 column 37 (char 412)",
        '{ "entities": [ { "text": "Dr. Ada Chen", "entity_type": "Person", '
        '"entity_type_id": 1 }, { "text": "Wuhan Institute of ',
        '{ "entities": [ { "text": "Dr. Ada Chen", "entity_type": "Person", '
        '"entity_type_id": 1 }',
        " ] }",
        (("Dr. Ada Chen", 1),),
    ),
    _body(
        1, 1, "malformed-json", "length",
        _MALFORMED_ERROR + ": line 25 column 32 (char 515)",
        '{ "entities": [ { "text": "Mr. Liu Wei", "entity_type": "Person", '
        '"entity_type_id": 1 }, { "text": "Wuhan facility", "en',
        '{ "entities": [ { "text": "Mr. Liu Wei", "entity_type": "Person", '
        '"entity_type_id": 1 }',
        " ] }",
        (("Mr. Liu Wei", 1),),
    ),
    # --- the wrong-keys rows ----------------------------------------------
    # Two distinct wrong inner shapes, both recorded: "entity" (with the real
    # entity_type_id already present) and "text" (with entity_type resolved from
    # the name). Only the second one the merged alias net can answer.
    _body(
        2, 1, "wrong-keys", "stop",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Chen"}, {"entity_type_id": 1, "entity_t',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Chen"}',
        "]}",
    ),
    _body(
        3, 1, "wrong-keys", "length",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Ada Chen"}, {"entity_type_id": 3, "enti',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Ada Chen"}',
        "]}",
    ),
    _body(
        4, 1, "wrong-keys", "stop",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Mr. Liu Wei"}, {"entity_type_id": 3, "entit',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Mr. Liu Wei"}',
        "]}",
    ),
    _body(
        5, 1, "wrong-keys", "stop",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Ada Chen"}, {"entity_type_id": 3, "enti',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Ada Chen"}',
        "]}",
    ),
    # --- repeat 2 ---------------------------------------------------------
    _body(
        0, 2, "malformed-json", "length",
        _MALFORMED_ERROR + ": line 20 column 37 (char 412)",
        '{ "entities": [ { "text": "Dr. Ada Chen", "entity_type": "Person", '
        '"entity_type_id": 1 }, { "text": "Wuhan Institute of ',
        '{ "entities": [ { "text": "Dr. Ada Chen", "entity_type": "Person", '
        '"entity_type_id": 1 }',
        " ] }",
        (("Dr. Ada Chen", 1),),
    ),
    _body(
        1, 2, "wrong-keys", "length",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'te",
        '{"entities": [{"text": "Mr. Liu Wei", "entity_type": "Person", '
        '"entity_type_id": 1}, {"text": "Wuhan facility", "entity_',
        '{"entities": [{"text": "Mr. Liu Wei", "entity_type": "Person", '
        '"entity_type_id": 1}',
        "]}",
        (("Mr. Liu Wei", 1),),
    ),
    _body(
        2, 2, "wrong-keys", "stop",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Chen"}, {"entity_type_id": 1, "entity_t',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Chen"}',
        "]}",
    ),
    _body(
        3, 2, "wrong-keys", "length",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'te",
        '{"entities": [{"text": "Wuhan Institute of Weather Modelling", '
        '"entity_type": "Organization", "entity_type_id": 2}, {"te',
        '{"entities": [{"text": "Wuhan Institute of Weather Modelling", '
        '"entity_type": "Organization", "entity_type_id": 2}',
        "]}",
        (("Wuhan Institute of Weather Modelling", 2),),
    ),
    _body(
        4, 2, "wrong-keys", "stop",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Mr. Liu Wei"}, {"entity_type_id": 3, "entit',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Mr. Liu Wei"}',
        "]}",
    ),
    _body(
        5, 2, "wrong-keys", "stop",
        _WRONG_KEYS_ERROR + " [type=missing, input_value={'entities': [{'en",
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Ada Chen"}, {"entity_type_id": 3, "enti',
        '{"entities": [{"entity_type_id": 1, "entity_type_name": "Person", '
        '"entity": "Dr. Ada Chen"}',
        "]}",
    ),
)


def _nodes_prompt() -> list:
    """Graphiti's real node-extraction prompt for the recorded chunk text."""

    return prompt_library.extract_nodes.extract_text(
        {
            "episode_content": _EPISODE,
            "entity_types": _ENTITY_TYPES,
            "previous_episodes": [],
            "custom_extraction_instructions": "",
            "reference_time": "2026-11-07T00:00:00Z",
        }
    )


def _transport(body: str, finish_reason: str):
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


def _client(body: str, *, finish_reason: str, monkeypatch):
    """The production client object under test, with a fake transport.

    Config is patched to the state the harness recorded
    (``GRAPHITI_LLM_MAX_TOKENS=4096``, ``json_schema``); ``LLM_MODEL_NAME`` is a
    sentinel so a body is never mistaken for the request."""

    monkeypatch.setattr(Config, "GRAPHITI_LLM_MAX_TOKENS", 4096)
    monkeypatch.setattr(Config, "GRAPHITI_STRUCTURED_OUTPUT_MODE", "json_schema")
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MODEL", "")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "REQUEST_SENTINEL_MODEL")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "http://127.0.0.1:1/v1")
    client = graphiti_zep._build_llm_client()
    calls, completions = _transport(body, finish_reason)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, calls


def _generate(client, messages, response_model):
    return asyncio.run(client._generate_response(messages, response_model, 2048))


def _closed(record: RecordedBody) -> str:
    """The recorded head, closed so it can be parsed and normalised."""

    return record.intact + record.closing


# ---------------------------------------------------------------------------
# Provenance — the heads this fixture drives really are the recorded bodies
# ---------------------------------------------------------------------------


def test_the_recorded_heads_are_verbatim_prefixes():
    """Each ``intact`` head must be a prefix of the ``body`` it was trimmed from.

    The harness persisted only the first 120 chars of every response and never
    wrote a body to disk, so ``intact`` is a hand-trimmed prefix that stops at an
    element boundary and ``closing`` is the bracket work that closes it. This
    assertion is the checkable half of that statement: it fails if a head ever
    stops being byte-for-byte the beginning of the recorded body, which is the
    one way a "raw model output" fixture could quietly become synthetic."""

    for record in RECORDED_BODIES:
        assert record.intact == record.body[: len(record.intact)]
        # the trim stopped at an element boundary, not inside a value
        assert record.intact.endswith("}")
        assert len(record.body) == 120  # the harness bound, nothing more exists


# ---------------------------------------------------------------------------
# M3 — the genuinely malformed bodies still fail fast
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [r for r in RECORDED_BODIES if r.error_class == "malformed-json"],
    ids=lambda r: r.id,
)
def test_recorded_malformed_bodies_fail_fast(monkeypatch, record):
    """A ``finish_reason=length`` body is a truncation, terminal and not retried.

    The harness recorded an unescaped quote inside a value for these calls
    (``Expecting ',' delimiter`` at char 412 / char 515). The persisted prefix
    ends mid-value, so the JSONDecodeError this fixture sees is "Unterminated
    string" rather than the harness's — the harness bound is what differs, not
    the classification: M3 decides from ``finish_reason``, and the seam must fail
    fast instead of burning graphiti's retry chain on a body it will always
    produce again."""

    client, _calls = _client(
        record.body, finish_reason=record.finish_reason, monkeypatch=monkeypatch
    )

    with pytest.raises(_TruncatedJSONError) as raised:
        _generate(client, _nodes_prompt(), ExtractedEntities)

    assert str(raised.value).startswith(_TERMINAL_PREFIX)
    # outside graphiti's tenacity retried set, so M2 skips the chunk once
    assert not isinstance(raised.value, json.JSONDecodeError)


# ---------------------------------------------------------------------------
# The fix — the recorded alias shapes are answered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [r for r in RECORDED_BODIES if r.recovers],
    ids=lambda r: r.id,
)
def test_recorded_wrong_key_bodies_are_answered(monkeypatch, record):
    """``entities``/``text``/``entity_type`` bodies now normalise and validate.

    Every recorded body carries the wrong top-level key (the harness's
    ``keys=['entities']`` observation), so the top-level rename has to fire, and
    the elements keyed ``text``/``entity_type``/``entity_type_id`` must come out
    as graphiti's ``name``/``entity_type_id`` with the id the body itself
    carried — never an invented one. ``ExtractedEntities(**result)`` must then
    validate, which is the gate graphiti applies before anything is graphed.

    For the two ``malformed-json`` rows the chunk still failed overall — the body
    ran on to an unescaped quote — but their recorded head carries this shape,
    and the fix owes it an answer."""

    body = _closed(record)
    # the head is the wrong-key body the harness saw, not a corrected one
    assert list(json.loads(body)) == ["entities"]

    client, _calls = _client(
        body, finish_reason=record.finish_reason, monkeypatch=monkeypatch
    )

    result = _generate(client, _nodes_prompt(), ExtractedEntities)

    # the wrong top-level key is gone and graphiti's real field carries the data
    assert list(result) == ["extracted_entities"]
    assert [
        (entity["name"], entity["entity_type_id"])
        for entity in result["extracted_entities"]
    ] == list(record.recovers)
    entities = ExtractedEntities(**result)
    assert [entity.name for entity in entities.extracted_entities] == [
        name for name, _ in record.recovers
    ]


# ---------------------------------------------------------------------------
# The honest gap — bodies the alias net cannot resolve stay unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record", [r for r in RECORDED_BODIES if not r.recovers], ids=lambda r: r.id
)
def test_recorded_bodies_the_alias_net_cannot_resolve_stay_unchanged(
    monkeypatch, record
):
    """``"entity"`` is not an alias, so these recorded bodies are still skipped.

    The model's other wrong inner key — ``{"entity_type_id": 1,
    "entity_type_name": "Person", "entity": "Dr. Chen"}`` — is not covered by
    ``_ELEMENT_ALIASES`` (``name`` maps ``text``/``entity_name`` only), so no
    ``name`` can be recovered and nothing may be invented to stand in for one.
    The seam returns the element exactly as the model wrote it — ``entity``
    stays, ``entity_type_name`` stays beside the ``entity_type_id`` it cannot
    update — so graphiti's pydantic rejects it and M2 skips the chunk VISIBLY.
    That is the intended outcome (a visible skip beats a silently wrong graph),
    and it is recorded here rather than papered over: the alias net has a hole
    for one of the two recorded inner shapes, which the #22 prompt fix made moot
    by no longer asking the model to emit ``entity`` at all.

    Note also that ``entity_type_name`` is deliberately NOT applied when the real
    ``entity_type_id`` is already present: a rename only ever happens when
    graphiti's own field is absent."""

    body = _closed(record)
    payload = json.loads(body)

    client, _calls = _client(
        body, finish_reason=record.finish_reason, monkeypatch=monkeypatch
    )

    result = _generate(client, _nodes_prompt(), ExtractedEntities)

    # only the top-level key was renamed; every element is byte-identical
    assert list(result) == ["extracted_entities"]
    assert json.dumps(result["extracted_entities"]) == json.dumps(payload["entities"])
    assert "entity" in result["extracted_entities"][0]
    assert "name" not in result["extracted_entities"][0]
    # graphiti rejects it, so M2 skips the chunk instead of graphing a wrong node
    with pytest.raises(ValidationError) as raised:
        ExtractedEntities(**result)
    assert raised.value.errors()[0]["loc"] == ("extracted_entities", 0, "name")


# ---------------------------------------------------------------------------
# Why the fixture closes the recorded heads
# ---------------------------------------------------------------------------


def test_a_persisted_prefix_that_ends_mid_value_is_not_a_truncation(monkeypatch):
    """The seam classifies by ``finish_reason``, so a cut body is not a truncation.

    This is the measurement reason the fixture needs ``intact`` + ``closing``:
    the harness bound meant every persisted body ends mid-value, and the
    ``finish_reason=stop`` ones would otherwise only ever exercise the parse
    path. Here the persisted text of one recorded call is fed through as
    persisted — it fails to parse, and because the response said ``stop`` the
    seam refuses to call that a truncation: the JSONDecodeError propagates for
    graphiti's tenacity wrapper to retry, exactly as it must for a generation
    defect."""

    record = next(r for r in RECORDED_BODIES if r.finish_reason == "stop")
    client, _calls = _client(
        record.body, finish_reason=record.finish_reason, monkeypatch=monkeypatch
    )

    with pytest.raises(json.JSONDecodeError) as raised:
        _generate(client, _nodes_prompt(), ExtractedEntities)

    assert not isinstance(raised.value, _TruncatedJSONError)
