"""Local, API-key-free Zep-compatible graph backend.

Replaces Zep Cloud with **Graphiti** running fully on-device:

* embedded graph store: **Kuzu** (no DB server, no connection, no key)
* LLM: local model (mlx-vlm / llama.cpp) via our own ``/v1`` proxy
* embeddings: local sentence-transformers model, in this process (B5, #14)
* reranker: local no-op

The public surface intentionally mirrors the parts of the ``zep_cloud`` SDK that
MiroFish's service layer already consumes (``client.graph.*``, ``client.batch.*``,
``client.graph.episode.get``), so switching backends is a config flag:

    GRAPH_BACKEND = zep | graphiti

Everything asynchronous (Graphiti is an asyncio library) runs on one dedicated
event-loop thread; sync Flask code blocks on it with **no timeout**, and episode
ingestion is checkpointed to disk so a restarted backend resumes instead of
failing.
"""

from __future__ import annotations

import os

# Kuzu prints a DeprecationWarning on every driver construction (upstream Kuzu is
# unmaintained); Graphiti's PostHog telemetry must stay off before import.
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module="graphiti_core")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import asyncio
import atexit
import hashlib
import json
import signal
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from ..config import Config
from graphiti_core.embedder.client import EmbedderClient

from .logger import get_logger

logger = get_logger("mirofish.graphiti_zep")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Paths & ontology persistence
# ---------------------------------------------------------------------------


def graph_dir(graph_id: str) -> Path:
    """Root directory of one local graph (Kuzu DB + ontology + batch records)."""

    safe = graph_id or ""
    if not all(char.isalnum() or char in "_-" for char in safe):
        raise ValueError(f"unsafe graph_id: {graph_id!r}")
    return Path(Config.GRAPHITI_DATA_DIR) / safe


def _ontology_path(graph_id: str) -> Path:
    return graph_dir(graph_id) / "ontology.json"


def _batch_path(graph_id: str, batch_id: str) -> Path:
    return graph_dir(graph_id) / "batches" / f"{batch_id}.json"


def _iter_graph_ids() -> List[str]:
    base = Path(Config.GRAPHITI_DATA_DIR)
    if not base.exists():
        return []
    return [child.name for child in sorted(base.iterdir()) if child.is_dir()]


def _load_ontology_spec(graph_id: str) -> Dict[str, Any]:
    path = _ontology_path(graph_id)
    if not path.exists():
        return {"entities": [], "edges": []}
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception as exc:  # a corrupted spec must not kill ingestion
        logger.warning("graphiti ontology unreadable for %s: %s", graph_id, exc)
        return {"entities": [], "edges": []}


def _build_graphiti_types(spec: Dict[str, Any]):
    """Turn a persisted ontology spec into Graphiti's add_episode types.

    Entity/edge models are intentionally *bare* (no custom fields): Graphiti then
    skips per-entity attribute-extraction LLM calls, which the 1.7B local model
    cannot complete reliably (it returns wrapper objects like ``{"value": null}``
    and crashes pydantic validation). Descriptions ride along via ``__doc__``,
    which Graphiti injects into the extraction prompt.
    """

    entity_types: Dict[str, type] = {}
    edge_types: Dict[str, type] = {}
    edge_type_map: Dict[tuple, List[str]] = {}

    for entity in spec.get("entities", []):
        name = entity.get("name")
        if not name:
            continue
        entity_types[name] = type(
            name,
            (BaseModel,),
            {"__doc__": (entity.get("description") or f"A {name} entity.").strip()},
        )

    for edge in spec.get("edges", []):
        name = edge.get("name")
        if not name:
            continue
        class_name = "".join(word.capitalize() for word in name.split("_")) or name
        edge_types[name] = type(
            class_name,
            (BaseModel,),
            {"__doc__": (edge.get("description") or f"A {name} relationship.").strip()},
        )
        pairs = edge.get("source_targets") or [{"source": "Entity", "target": "Entity"}]
        for pair in pairs:
            key = (pair.get("source") or "Entity", pair.get("target") or "Entity")
            edge_type_map.setdefault(key, [])
            if name not in edge_type_map[key]:
                edge_type_map[key].append(name)

    return entity_types or None, edge_types or None, edge_type_map or None


# ---------------------------------------------------------------------------
# Zep-compatible response shims
# ---------------------------------------------------------------------------


class _NodeShim:
    """zep_cloud-style node view (uuid_, name, labels, summary, attributes...)."""

    def __init__(self, node: Any):
        self._node = node

    @property
    def uuid_(self) -> str:
        return getattr(self._node, "uuid", "") or ""

    @property
    def name(self) -> str:
        return getattr(self._node, "name", "") or ""

    @property
    def labels(self) -> List[str]:
        return list(getattr(self._node, "labels", None) or [])

    @property
    def summary(self) -> str:
        return getattr(self._node, "summary", "") or ""

    @property
    def attributes(self) -> Dict[str, Any]:
        return getattr(self._node, "attributes", None) or {}

    @property
    def created_at(self):
        return getattr(self._node, "created_at", None)


class _EdgeShim:
    """zep_cloud-style edge view."""

    def __init__(self, edge: Any):
        self._edge = edge

    @property
    def uuid_(self) -> str:
        return getattr(self._edge, "uuid", "") or ""

    @property
    def name(self) -> str:
        return getattr(self._edge, "name", "") or ""

    @property
    def fact(self) -> str:
        return getattr(self._edge, "fact", "") or ""

    @property
    def fact_type(self) -> str:
        return getattr(self._edge, "fact_type", None) or self.name

    @property
    def source_node_uuid(self) -> str:
        return getattr(self._edge, "source_node_uuid", "") or ""

    @property
    def target_node_uuid(self) -> str:
        return getattr(self._edge, "target_node_uuid", "") or ""

    @property
    def attributes(self) -> Dict[str, Any]:
        return getattr(self._edge, "attributes", None) or {}

    @property
    def episodes(self) -> List[str]:
        value = getattr(self._edge, "episodes", None) or []
        if not isinstance(value, list):
            value = [value]
        return [str(item) for item in value]

    episode_ids = episodes

    @property
    def created_at(self):
        return getattr(self._edge, "created_at", None)

    @property
    def valid_at(self):
        return getattr(self._edge, "valid_at", None)

    @property
    def invalid_at(self):
        return getattr(self._edge, "invalid_at", None)

    @property
    def expired_at(self):
        return getattr(self._edge, "expired_at", None)


class _EpisodeShim:
    """zep_cloud-style episode view. Local ingestion is synchronous, so any
    episode handed out here is already fully processed."""

    def __init__(self, episode: Any):
        self._episode = episode

    @property
    def uuid_(self) -> str:
        return getattr(self._episode, "uuid", "") or ""

    @property
    def processed(self) -> bool:
        return True

    @property
    def content(self) -> str:
        return getattr(self._episode, "content", "") or ""


class _SearchResponseShim:
    def __init__(self):
        self.edges: List[_EdgeShim] = []
        self.nodes: List[_NodeShim] = []


class _PagedResponseShim:
    """Raw-response shim used by Zep paging helpers (.data + cursor headers)."""

    def __init__(self, data: List[Any]):
        self.data = data
        self.headers: Dict[str, str] = {}


class _ProgressShim:
    def __init__(self, percent_complete: float, succeeded_items: int, total_items: int):
        self.percent_complete = percent_complete
        self.succeeded_items = succeeded_items
        self.total_items = total_items


class _BatchShim:
    """zep_cloud Batch view: status + progress."""

    def __init__(self, record: Dict[str, Any]):
        total = len(record.get("items", []))
        done = int(record.get("done", 0) or 0)
        self.batch_id = record.get("batch_id")
        self.status = record.get("status")
        self.progress = _ProgressShim(
            percent_complete=(100.0 * done / total) if total else 0.0,
            succeeded_items=done,
            total_items=total,
        )
        self.metadata = record.get("metadata") or {}
        self.created_at = record.get("created_at")
        self.updated_at = record.get("updated_at")
        self.error = record.get("error")
        self.skipped = int(record.get("skipped", 0) or 0)
        self.skipped_indices = list(record.get("skipped_indices", []) or [])


class _BatchItemShim:
    def __init__(self, item: Dict[str, Any]):
        self.sequence_index = item.get("sequence_index")
        self.status = item.get("status")
        self.episode_uuid = item.get("episode_uuid")
        self.source_uuid = item.get("episode_uuid")
        self.error = item.get("error")


class _GraphShim:
    def __init__(self, graph_id: str, name: str = "MiroFish Graph", description: str = ""):
        self.id = graph_id
        self.graph_id = graph_id
        self.name = name
        self.description = description
        self.metadata: Dict[str, Any] = {}


class _BatchPageShim:
    def __init__(self, items: List[_BatchItemShim]):
        self.items = items
        self.next_cursor = None


class _BatchListShim:
    def __init__(self, batches: List[Any]):
        self.batches = batches
        self.next_cursor = None


class GraphNotFoundError(Exception):
    """Local equivalent of zep_cloud.NotFoundError for graph/batch reads."""


class _TruncatedJSONError(Exception):
    """Deterministic json_schema truncation — retrying is futile (M3).

    Under constrained ``json_schema`` decoding a truncated (``finish_reason ==
    "length"``) body is invalid JSON, and the same prompt deterministically
    produces the same broken body. The vendored tenacity wrapper retries
    ``JSONDecodeError`` 4x (measured ~980 s wasted across 3 chains in the
    2026-11-05 run); this terminal error deliberately falls OUTSIDE the vendored
    retried set so it fails fast and M2's fault-tolerant ``_ingest_record``
    skips the chunk once instead of burning the retry chain.
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Local Graphiti clients (local-runtime LLM, local embedder, no-op reranker)
# ---------------------------------------------------------------------------


def _noop_reranker_class():
    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class _NoOpReranker(CrossEncoderClient):
        """Local reranker: preserve provider ordering, uniform scores."""

        async def rank(self, query: str, passages: List[str]) -> List[tuple]:
            return [(passage, 1.0) for passage in passages]

    return _NoOpReranker


_NoOpReranker = _noop_reranker_class()


# ---------------------------------------------------------------------------
# Extraction-schema plumbing for the local small model (issue #22)
# ---------------------------------------------------------------------------
#
# The 2026-11-07 acceptance run skipped 19/19 chunks. The dominant failure was
# valid JSON carrying the WRONG key names (`{"entities": [...]}` instead of
# graphiti's `{"extracted_entities": [{"name": ..., "entity_type_id": ...}]}`):
# GRAPHITI_STRUCTURED_OUTPUT_MODE defaults to `json_schema`, which puts the
# schema into `response_format` ONLY — and our /v1 proxy strips response_format
# for local backends (app/models/manager.py), so the model never sees the key
# names graphiti validates against.


def _schema_reminder(response_model) -> str:
    """The exact reminder graphiti appends to ``messages[-1]`` in json_object
    mode (``openai_generic_client.py:197-204``); reused verbatim so a
    seam-level reminder is indistinguishable from graphiti's own."""

    return (
        "\n\nRespond with a JSON object in the following format:\n\n"
        + json.dumps(response_model.model_json_schema())
    )


def _append_schema_reminder(messages, response_model) -> None:
    """Put graphiti's response schema into the prompt for the model (#22, R2).

    ``response_model`` is set for every graphiti prompt (nodes, edges, node
    dedupe, edge dedupe, summaries), whatever ``GRAPHITI_STRUCTURED_OUTPUT_MODE``
    says, so the small model is told the key names it must emit. Idempotent:
    graphiti itself appends the same string in json_object mode and the vendored
    tenacity wrapper re-enters this call with the same mutated message list."""

    if response_model is None or not messages:
        return
    reminder = _schema_reminder(response_model)
    last = messages[-1]
    content = last.content or ""
    if content.endswith(reminder):
        return
    last.content = content + reminder


# Positive-only alias tables. A rename happens ONLY when graphiti's real field
# is absent, exactly one candidate alias is present, and (for entity_type_id)
# the value resolves unambiguously against the prompt's own <ENTITY TYPES>
# block. Anything else returns the payload unchanged, so the existing per-chunk
# skip (M2) still fires: a visible skip beats a silently wrong graph. The target
# names are graphiti's, read from graphiti_core/prompts — extract_nodes.py
# (ExtractedEntities, SummarizedEntities), extract_edges.py (ExtractedEdges,
# Edge), dedupe_nodes.py (NodeResolutions), dedupe_edges.py (EdgeDuplicate).
_TOP_LEVEL_ALIASES = {
    "extracted_entities": ("entities", "nodes"),
    "edges": ("extracted_edges", "facts", "triples"),
    "entity_resolutions": ("resolutions", "duplicates"),
    "summaries": ("entity_summaries", "summarized_entities"),
}

_ELEMENT_ALIASES = {
    "name": ("text", "entity_name"),
    "entity_type_id": ("entity_type", "entity_type_name"),
    "source_entity_name": ("source",),
    "target_entity_name": ("target",),
    "relation_type": ("relation", "relationship"),
}


def _parse_entity_type_entries(text: str) -> List[Dict[str, Any]]:
    """The ``<ENTITY TYPES>`` block of a graphiti prompt as dicts.

    node_operations renders this block with ``repr()`` (single quotes, not
    JSON), so literal_eval is the parser that works. An absent, empty or
    unreadable block yields ``[]`` — which makes an entity-type name
    unresolvable rather than guessable."""

    import ast

    start = text.find("<ENTITY TYPES>")
    if start < 0:
        return []
    end = text.find("</ENTITY TYPES>", start)
    block = text[start + len("<ENTITY TYPES>") : end if end >= 0 else None].strip()
    if not block:
        return []
    try:
        entries = ast.literal_eval(block)
    except (SyntaxError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _entity_type_ids(messages) -> Dict[str, List[int]]:
    """``{entity_type_name: [entity_type_id, ...]}`` from every message's block.

    The id is only ever read from the prompt itself, never derived: a name that
    maps to more than one id is ambiguous and stays unresolved."""

    index: Dict[str, List[int]] = {}
    for message in messages or []:
        for entry in _parse_entity_type_entries(getattr(message, "content", "") or ""):
            name = entry.get("entity_type_name")
            type_id = entry.get("entity_type_id")
            if not isinstance(name, str) or isinstance(type_id, bool):
                continue
            if not isinstance(type_id, int):
                continue
            ids = index.setdefault(name, [])
            if type_id not in ids:
                ids.append(type_id)
    return index


def _entity_type_id(type_name: Any, messages):
    """Resolve an entity type NAME to graphiti's id, or ``None``."""

    if not isinstance(type_name, str):
        return None
    candidates = _entity_type_ids(messages).get(type_name, [])
    return candidates[0] if len(candidates) == 1 else None


def _plan_top_level_renames(payload: Dict[str, Any], response_model):
    """``{alias: graphiti field}`` for the top level, or ``None`` if ambiguous.

    Ambiguous means the real key and an alias both appear, two aliases for the
    same field appear, or one alias would serve two fields — a conflicting
    payload that is not ours to interpret."""

    planned: Dict[str, str] = {}
    for field, field_info in response_model.model_fields.items():
        aliases = _TOP_LEVEL_ALIASES.get(field)
        if not aliases or not field_info.is_required():
            continue
        present = [alias for alias in aliases if alias in payload]
        if not present:
            continue
        if field in payload or len(present) > 1 or present[0] in planned:
            return None
        planned[present[0]] = field
    return planned


def _normalize_element(item: Dict[str, Any], messages):
    """Rename one list element's field aliases; ``None`` when ambiguous."""

    renames: Dict[str, str] = {}
    for field, aliases in _ELEMENT_ALIASES.items():
        if field in item:
            continue
        present = [alias for alias in aliases if alias in item]
        if not present:
            continue
        if len(present) > 1:
            return None
        renames[present[0]] = field
    if not renames:
        return item
    normalized = dict(item)
    for alias, field in renames.items():
        if field != "entity_type_id":
            normalized[field] = normalized.pop(alias)
            continue
        type_id = _entity_type_id(normalized[alias], messages)
        if type_id is None:
            # Never invent an id: leave the field absent (and the alias where
            # it was) so pydantic fails visibly and the chunk is skipped.
            continue
        del normalized[alias]
        normalized[field] = type_id
    return normalized


def _normalize_elements(items, messages):
    """Element-level alias mapping for one top-level list; ``None`` if ambiguous.

    Returns the list it was given when no element changed, so a conforming
    payload stays the identical object (see ``_normalize_to_schema``)."""

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        element = _normalize_element(item, messages)
        if element is None:
            return None
        normalized.append(element)
    if all(new is old for new, old in zip(normalized, items)):
        return items
    return normalized


def _normalize_to_schema(payload, response_model, messages):
    """Rename unambiguous key aliases so graphiti can validate (#22, R3).

    The only place that can do this is our own leaf call: parsing happens
    inside graphiti's ``_generate_response`` and validation in
    ``node_operations`` (``ExtractedEntities(**llm_response)``), both outside
    our reach. Returns the payload UNCHANGED — the same object — unless a
    rename is provably safe, so today's skip path still fires for everything
    else."""

    if response_model is None or not isinstance(payload, dict):
        return payload

    planned = _plan_top_level_renames(payload, response_model)
    if planned is None:
        return payload

    normalized: Dict[str, Any] = {}
    changed = bool(planned)
    for key, value in payload.items():
        field = planned.get(key, key)
        if not isinstance(value, list):
            normalized[field] = value
            continue
        elements = _normalize_elements(value, messages)
        if elements is None:
            return payload
        changed = changed or elements is not value
        normalized[field] = elements
    return normalized if changed else payload


def _build_llm_client():
    from openai import AsyncOpenAI

    from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, LLMConfig, ModelSize
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    class _CappedLLMClient(OpenAIGenericClient):
        """OpenAIGenericClient hardened for the local small model (#22).

        Graphiti's edge-extraction path passes an explicit
        ``extract_edges_max_tokens = 16384``
        (``graphiti_core/utils/maintenance/edge_operations.py``), which otherwise
        overrides the constructor cap. Guarding the leaf call closes that hole.

        Three more behaviours live here, all forced by what the acceptance run
        actually recorded (19/19 chunks skipped):

        * the schema reminder is appended to ``messages[-1]``, because our /v1
          proxy strips ``response_format`` so ``json_schema`` mode never shows
          the model the key names graphiti validates;
        * the clamped ``max_tokens``, the response model, ``finish_reason`` and
          the first 400 chars of the raw body are logged at WARNING — the raw
          output used to be persisted nowhere; the request is never logged;
        * an unparseable body is classified by ``finish_reason``: only a genuine
          truncation becomes the terminal ``_TruncatedJSONError`` so the
          vendored tenacity wrapper does not retry it, while a generation
          defect propagates as ``json.JSONDecodeError`` and may be retried.

        The leaf call therefore copies graphiti's own request/parse sequence
        (``openai_generic_client.py:151-171``) instead of delegating to
        ``super()``, which returns an already-parsed dict and hides
        ``finish_reason``. ``scratch/prove_local_graphiti.py`` (``LocalProxyLLM``)
        is the in-repo precedent for that override.
        """

        async def _generate_response(
            self,
            messages,
            response_model=None,
            max_tokens: int = DEFAULT_MAX_TOKENS,
            model_size=ModelSize.medium,
            *,
            model=None,
        ):
            import openai

            from graphiti_core.llm_client.errors import (
                EmptyResponseError,
                RateLimitError,
            )
            from graphiti_core.llm_client.openai_generic_client import DEFAULT_MODEL

            cap = Config.GRAPHITI_LLM_MAX_TOKENS
            if not max_tokens or max_tokens > cap:
                max_tokens = cap
            _append_schema_reminder(messages, response_model)
            finish_reason = None
            try:
                openai_messages = []
                for m in messages:
                    m.content = self._clean_input(m.content)
                    if m.role == "user":
                        openai_messages.append({"role": "user", "content": m.content})
                    elif m.role == "system":
                        openai_messages.append(
                            {"role": "system", "content": m.content}
                        )
                response = await self.client.chat.completions.create(
                    model=model or self.model or DEFAULT_MODEL,
                    messages=openai_messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    response_format=self._build_response_format(response_model),
                )
                choice = response.choices[0]
                result = choice.message.content or ""
                finish_reason = getattr(choice, "finish_reason", None)
                # Bounded instrumentation: the raw model output was persisted
                # nowhere before, which is why #22 had to be diagnosed from a
                # pydantic error repr. %.400s caps the body; the request is
                # deliberately not logged.
                logger.warning(
                    "graphiti LLM response: model=%s max_tokens=%s "
                    "finish_reason=%s body=%.400s",
                    getattr(response_model, "__name__", response_model),
                    max_tokens,
                    finish_reason,
                    result,
                )
                # An empty body (refusal, length finish_reason, or a flaky
                # endpoint) would make json.loads raise a cryptic
                # JSONDecodeError; surface a clear error instead.
                if not result:
                    raise EmptyResponseError("LLM returned an empty response")
                return _normalize_to_schema(
                    json.loads(self._strip_code_fences(result)),
                    response_model,
                    messages,
                )
            except openai.RateLimitError as e:
                raise RateLimitError from e
            except json.JSONDecodeError as exc:
                # finish_reason == "length" is the only genuine truncation, and
                # a truncated json_schema body is deterministic — fail fast and
                # let M2's _ingest_record skip the chunk once. An absent
                # finish_reason leaves nothing to reason with, so be
                # conservative. Anything else is a generation defect
                # (unescaped quote, wrong keys, …), not a truncation: mislabelling
                # it wastes the measurement, so let it propagate and be retried.
                if finish_reason is None or finish_reason == "length":
                    raise _TruncatedJSONError(
                        f"truncated/unparseable JSON response (not retried): {exc}"
                    ) from exc
                raise
            except Exception as e:
                logger.error(f"Error in generating LLM response: {e}")
                raise

    base_url = (Config.LLM_BASE_URL or "http://localhost:5001/v1").rstrip("/")
    model = Config.GRAPHITI_LLM_MODEL or Config.LLM_MODEL_NAME
    config = LLMConfig(
        api_key=Config.LLM_API_KEY or "local",
        model=model,
        small_model=model,
        base_url=base_url,
        temperature=0.0,
        max_tokens=Config.GRAPHITI_LLM_MAX_TOKENS,
    )
    # Our /v1 proxy routes json_schema/json_object asks into its prompt-level
    # strict-JSON mode (+ repair parser) for local backends, but it strips
    # response_format entirely (app/models/manager.py) — so in the default
    # json_schema mode the schema exists ONLY in response_format and never
    # reaches the model, which is how #22's 19/19 skipped run happened.
    # _CappedLLMClient therefore appends graphiti's own schema reminder to the
    # prompt itself, which makes GRAPHITI_STRUCTURED_OUTPUT_MODE irrelevant to
    # what the model sees.
    #
    # max_tokens must be passed to the CLIENT constructor, not only via LLMConfig:
    # LLMClient.__init__ sets self.max_tokens = config.max_tokens, but
    # OpenAIGenericClient.__init__ then overwrites it with its own 16384 default.
    # Relying on LLMConfig alone silently makes GRAPHITI_LLM_MAX_TOKENS dead config
    # (leaving the 1.7B model free to ramble to 16k tokens, ~5 min per attempt, and
    # then fail JSON parsing after Graphiti's retries — measured ~975s wasted).
    #
    # The constructor cap alone is still insufficient: graphiti's edge-extraction path
    # calls the LLM with an explicit extract_edges_max_tokens = 16384
    # (graphiti_core/utils/maintenance/edge_operations.py:166), which overrides
    # self.max_tokens and produced one 16384-token call that took ~348 s and still
    # returned truncated JSON. _CappedLLMClient therefore clamps every per-call
    # max_tokens at the leaf, so that override is neutralised.
    return _CappedLLMClient(
        config=config,
        client=AsyncOpenAI(api_key=config.api_key, base_url=base_url),
        structured_output_mode=Config.GRAPHITI_STRUCTURED_OUTPUT_MODE or "json_schema",
        max_tokens=Config.GRAPHITI_LLM_MAX_TOKENS,
    )


class _LocalSentenceTransformerEmbedder(EmbedderClient):
    """Embeddings from a local sentence-transformers model, in this process.

    B5 (#14): embeddings used to be fetched from an external server; they now
    run a local model (cached under the confined HF cache) so the graph
    backend has no external dependency at all. The CPU/GPU encode call runs in
    a worker thread to keep the event loop responsive.
    """

    def __init__(self, model_name: str, embedding_dim: int,
                 query_prompt: str = ""):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self._dim = embedding_dim
        # asymmetric retrieval models ship a query/encoding prompt; apply it to
        # every encode so create() (queries AND single facts) and create_batch()
        # (fact batches) stay in one vector space (bake-off: 6/6 either way).
        self._prompt = query_prompt or ""
        logger.info("local embedder ready: %s (dim=%d)",
                    model_name, self._model.get_sentence_embedding_dimension())

    async def create(self, input_data) -> List[float]:
        first = input_data[0] if isinstance(input_data, (list, tuple)) else input_data
        text = self._prompt + str(first)
        vec = await asyncio.to_thread(self._model.encode, text)
        return [float(x) for x in vec[: self._dim]]

    async def create_batch(self, input_data_list: List[str]) -> List[List[float]]:
        texts = [self._prompt + str(t) for t in input_data_list]
        vecs = await asyncio.to_thread(self._model.encode, texts)
        return [[float(x) for x in v[: self._dim]] for v in vecs]


def _build_embedder():
    return _LocalSentenceTransformerEmbedder(
        Config.GRAPHITI_EMBEDDING_MODEL, Config.GRAPHITI_EMBEDDING_DIM,
        getattr(Config, "GRAPHITI_EMBEDDING_QUERY_PROMPT", "") or "")


async def _ensure_fts_indexes(driver) -> None:
    """Graphiti.build_indices_and_constraints() is a documented no-op for Kuzu, so
    search ops would fail on a missing FTS index; create them here instead.

    Existing index names are read first (CALL show_indexes) so re-opening a
    persistent database does not log spurious "already exists" errors.
    """

    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.graph_queries import get_fulltext_indices

    existing: set[str] = set()
    try:
        records, _, _ = await driver.execute_query("CALL show_indexes() RETURN *")
        for record in records:
            for key in ("index_name", 1):
                if isinstance(record, dict):
                    value = record.get(key)
                else:
                    value = record[key] if isinstance(key, int) else None
                if isinstance(value, str):
                    existing.add(value)
    except Exception:  # a fresh DB has no indexes -> show_indexes is still safe
        pass

    import re

    for query in get_fulltext_indices(GraphProvider.KUZU):
        match = re.search(r"CREATE_FTS_INDEX\('[^']+',\s*'([^']+)'", query)
        index_name = match.group(1) if match else None
        if index_name and index_name in existing:
            continue
        await driver.execute_query(query)


def _new_kuzu_driver(path: Path):
    from graphiti_core.driver.kuzu_driver import KuzuDriver

    class _KuzuLocalDriver(KuzuDriver):
        # graphiti-core 0.31.2 reads driver._database (Neo4j multi-DB) in
        # _resolve_request_scope; KuzuDriver never sets it and Kuzu has no
        # multi-DB concept (group_id is carried per-node).
        _database = ""

    return _KuzuLocalDriver(db=str(path))


def _new_graphiti(driver):
    from graphiti_core import Graphiti

    return Graphiti(
        graph_driver=driver,
        llm_client=_build_llm_client(),
        embedder=_build_embedder(),
        cross_encoder=_NoOpReranker(),
        max_coroutines=Config.GRAPHITI_MAX_COROUTINES or None,
    )


# ---------------------------------------------------------------------------
# Runtime: one event loop + one cached Graphiti instance per graph
# ---------------------------------------------------------------------------


class _Runtime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._graphs: Dict[str, Any] = {}
        self._episode_locks: Dict[str, asyncio.Lock] = {}

    # -- loop ----------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()

            def _target() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(target=_target, daemon=True, name="graphiti-loop")
            thread.start()
            self._loop, self._thread = loop, thread
            logger.info("graphiti runtime loop started (thread=%s)", thread.name)
            return loop

    def run(self, coro, *, timeout: Optional[float] = None):
        """Run a coroutine on the runtime loop and block for its result.

        ``timeout=None`` (default) waits indefinitely: local ingestion is
        deliberately uncapped — no cloud SLA, no 600s batch deadline.
        """

        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout)

    # -- graph instances -----------------------------------------------------

    async def get_graph(self, graph_id: str):
        """Cached Graphiti instance per graph id (must run inside the loop)."""

        instance = self._graphs.get(graph_id)
        if instance is None:
            path = graph_dir(graph_id) / "graph.kuzu"
            path.parent.mkdir(parents=True, exist_ok=True)
            driver = _new_kuzu_driver(path)
            await _ensure_fts_indexes(driver)
            instance = _new_graphiti(driver)
            self._graphs[graph_id] = instance
            self._episode_locks[graph_id] = asyncio.Lock()
            logger.info("graphiti instance ready for graph %s", graph_id)
        return instance

    def episode_lock(self, graph_id: str) -> asyncio.Lock:
        lock = self._episode_locks.get(graph_id)
        if lock is None:
            lock = self._episode_locks[graph_id] = asyncio.Lock()
        return lock

    async def forget_graph(self, graph_id: str) -> None:
        """Drop the cached instance and release the Kuzu handles so the same
        database path can be re-opened later (ontology reset, delete)."""

        instance = self._graphs.pop(graph_id, None)
        self._episode_locks.pop(graph_id, None)
        if instance is not None:
            await _close_driver_handles(instance.driver)

    def shutdown(self, timeout: float = 10.0) -> None:
        """Close every driver and stop the loop (process exit).

        Kuzu keeps its writes in a shadow/WAL file until the database is closed;
        without this checkpoint, a later process that re-opens the same file can
        hit a native SIGBUS on its first write, so a clean exit is required."""

        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def _close_all() -> None:
            for graph_id in list(self._graphs):
                instance = self._graphs.pop(graph_id)
                self._episode_locks.pop(graph_id, None)
                await _close_driver_handles(instance.driver)

        try:
            asyncio.run_coroutine_threadsafe(_close_all(), loop).result(timeout)
        except Exception as exc:
            logger.debug("graphiti runtime shutdown incomplete: %s", exc)


async def _close_driver_handles(driver) -> None:
    for target in (getattr(driver, "client", None), getattr(driver, "db", None)):
        try:
            if target is not None and hasattr(target, "close"):
                if asyncio.iscoroutinefunction(target.close):
                    await target.close()
                else:
                    target.close()
        except Exception as exc:  # best-effort release
            logger.debug("graphiti driver close failed: %s", exc)


_RUNTIME = _Runtime()


def _runtime_shutdown() -> None:
    try:
        _RUNTIME.shutdown()
    except Exception as exc:  # never block interpreter exit
        logger.debug("graphiti atexit shutdown failed: %s", exc)


def register_runtime_shutdown() -> None:
    """Install atexit + SIGTERM/SIGINT hooks so the runtime always closes cleanly.

    Called from the module import and again from create_app() (main thread),
    because CPython only allows signal.signal on the main thread.
    """

    atexit.register(_runtime_shutdown)
    _install_signal_handlers()


def _install_signal_handlers() -> None:
    """Make SIGTERM/SIGINT shut the runtime down cleanly.

    CPython's default SIGTERM kills the process without running atexit, which
    would leave a Kuzu database un-checkpointed; the next process to open it
    can hit a native SIGBUS on its first write. Chaining to the previous
    handler keeps Flask's own Ctrl-C behavior intact.
    """

    def _handler(signum, _frame, _previous=None):
        _runtime_shutdown()
        if callable(_previous):
            _previous(signum, _frame)
        else:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            previous = signal.getsignal(sig)

            def _bound_handler(signum, frame, _previous=previous, _sig=sig):
                _handler(signum, frame, _previous)

            signal.signal(sig, _bound_handler)
        except (ValueError, OSError):  # not the main thread / platform guard
            pass


# Module import installs what it can (atexit always; signals need the main
# thread — create_app() re-calls this from the main thread at startup).
register_runtime_shutdown()


# ---------------------------------------------------------------------------
# The client: zep_cloud-compatible namespace tree over local Graphiti
# ---------------------------------------------------------------------------


class GraphitiZepClient:
    def __init__(self) -> None:
        self.runtime = _RUNTIME
        self._batch_futures: Dict[str, Any] = {}
        self._batch_futures_guard = threading.Lock()
        self.graph = _GraphNamespace(self)
        self.batch = _BatchNamespace(self)

    # -- batch record persistence -------------------------------------------

    def _save_record(self, record: Dict[str, Any]) -> None:
        record["updated_at"] = _utc_now().isoformat()
        path = _batch_path(record["graph_id"], record["batch_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), "utf-8")
        tmp.replace(path)

    def _load_record(self, graph_id: str, batch_id: str) -> Dict[str, Any]:
        path = _batch_path(graph_id, batch_id)
        if not path.exists():
            raise GraphNotFoundError(f"batch {batch_id} is unknown locally")
        return json.loads(path.read_text("utf-8"))

    # -- ingestion -----------------------------------------------------------

    def _types_for(self, graph_id: str):
        return _build_graphiti_types(_load_ontology_spec(graph_id))

    async def _ingest_record(self, record: Dict[str, Any]) -> None:
        from graphiti_core.nodes import EpisodeType

        # Take a private copy: only this task (loop thread) mutates the record
        # afterwards, so a concurrent kick can never race the status writes.
        record = self._load_record(record["graph_id"], record["batch_id"])
        record["status"] = "processing"
        self._save_record(record)

        graph_id = record["graph_id"]
        graphiti = await self.runtime.get_graph(graph_id)
        entity_types, edge_types, edge_type_map = self._types_for(graph_id)
        lock = self.runtime.episode_lock(graph_id)

        for item in sorted(record["items"], key=lambda entry: entry["sequence_index"]):
            if item["status"] == "succeeded":
                continue
            async with lock:
                try:
                    # NOTE: uuid=None — add_episode(uuid=...) means *update an
                    # existing episode* (get_by_uuid). New episodes get their
                    # id from Graphiti; we write it back to the batch record so
                    # the Zep-style item validation still sees a real uuid.
                    results = await graphiti.add_episode(
                        name=f"episode-{item['sequence_index']}",
                        episode_body=item["chunk"],
                        source_description="MiroFish source document chunk",
                        reference_time=_utc_now(),
                        source=EpisodeType.text,
                        group_id=graph_id,
                        entity_types=entity_types,
                        edge_types=edge_types,
                        edge_type_map=edge_type_map,
                    )
                    item["episode_uuid"] = getattr(
                        getattr(results, "episode", None), "uuid", None
                    ) or item.get("episode_uuid")
                    item["status"] = "succeeded"
                    item["error"] = None
                except Exception as exc:
                    # Fault isolation (M2): one bad chunk must not abort the whole
                    # batch — checkpoint it as skipped and continue with the rest.
                    item["status"] = "skipped"
                    item["error"] = str(exc)[:500]
                    record["skipped"] = int(record.get("skipped", 0) or 0) + 1
                    record.setdefault("skipped_indices", []).append(
                        item["sequence_index"]
                    )
                    self._save_record(record)
                    logger.warning(
                        "graphiti chunk %s skipped: %s", item["sequence_index"], exc
                    )
                    continue
            record["done"] = int(record.get("done", 0)) + 1
            self._save_record(record)

        record["skipped"] = int(record.get("skipped", 0) or 0)
        if record["skipped"]:
            record["error"] = (
                f"skipped {record['skipped']}/{len(record['items'])} chunks: "
                f"{record.get('skipped_indices', [])}"
            )
        else:
            record["error"] = None
        # Fault-tolerant terminator (M2): a batch with ANY succeeded chunk is
        # terminal-succeeded even when some chunks were skipped; only a batch
        # where every chunk failed stays failed.
        record["status"] = "succeeded" if int(record.get("done", 0) or 0) else "failed"
        self._save_record(record)

    def _ensure_ingestion(self, record: Dict[str, Any]) -> None:
        """Kick off (or resume) ingestion for a non-terminal batch.

        Ingestion runs as a loop task; every ``batch.get`` self-heals a batch
        whose process died mid-run — checkpointed items are skipped on disk.
        The task owns all status writes; this call only schedules work.
        """

        if record["status"] in {"succeeded", "failed"}:
            return
        batch_id = record["batch_id"]
        with self._batch_futures_guard:
            future = self._batch_futures.get(batch_id)
            if future is not None and not future.done():
                return
            loop = self.runtime._ensure_loop()
            future = asyncio.run_coroutine_threadsafe(
                self._ingest_record(record), loop
            )
            self._batch_futures[batch_id] = future

            def _log_result(finished, batch_id=batch_id):
                try:
                    finished.result()
                except Exception as exc:
                    logger.error(
                        "graphiti ingestion task %s crashed: %s", batch_id, exc, exc_info=True
                    )

            future.add_done_callback(_log_result)
        logger.info(
            "graphiti ingestion %s started/resumed (%s items, %s done)",
            batch_id,
            len(record["items"]),
            record.get("done", 0),
        )

    async def _add_single_episode(
        self,
        graph_id: str,
        body: str,
        *,
        source_description: str,
        created_at: Optional[str],
    ):
        from graphiti_core.nodes import EpisodeType

        graphiti = await self.runtime.get_graph(graph_id)
        entity_types, edge_types, edge_type_map = self._types_for(graph_id)
        reference_time = _utc_now()
        if created_at:
            try:
                parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                reference_time = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        async with self.runtime.episode_lock(graph_id):
            return await graphiti.add_episode(
                name=f"episode-{uuid.uuid4().hex[:8]}",
                episode_body=body,
                source_description=source_description or "MiroFish episode",
                reference_time=reference_time,
                source=EpisodeType.text,
                group_id=graph_id,
                entity_types=entity_types,
                edge_types=edge_types,
                edge_type_map=edge_type_map,
            )

    # -- reads ---------------------------------------------------------------

    def _resolve_graph_ids(self, graph_id: Optional[str]) -> List[str]:
        return [graph_id] if graph_id else _iter_graph_ids()

    async def _read_nodes(self, graph_id: str) -> List[Any]:
        from graphiti_core.nodes import EntityNode

        graphiti = await self.runtime.get_graph(graph_id)
        return await EntityNode.get_by_group_ids(graphiti.driver, [graph_id])

    async def _read_edges(self, graph_id: str) -> List[Any]:
        from graphiti_core.edges import EntityEdge

        graphiti = await self.runtime.get_graph(graph_id)
        return await EntityEdge.get_by_group_ids(graphiti.driver, [graph_id])

    async def _read_node(self, graph_id: Optional[str], node_uuid: str):
        from graphiti_core.errors import NodeNotFoundError
        from graphiti_core.nodes import EntityNode

        last_error: Optional[Exception] = None
        for candidate in self._resolve_graph_ids(graph_id):
            try:
                graphiti = await self.runtime.get_graph(candidate)
                return await EntityNode.get_by_uuid(graphiti.driver, node_uuid)
            except NodeNotFoundError as exc:
                last_error = exc
        raise GraphNotFoundError(str(last_error or f"node {node_uuid} not found"))

    async def _read_node_edges(self, graph_id: Optional[str], node_uuid: str):
        from graphiti_core.edges import EntityEdge

        for candidate in self._resolve_graph_ids(graph_id):
            graphiti = await self.runtime.get_graph(candidate)
            edges = await EntityEdge.get_by_node_uuid(graphiti.driver, node_uuid)
            if edges:
                return edges
        return []

    async def _read_episode(self, graph_id: Optional[str], episode_uuid: str):
        from graphiti_core.errors import NodeNotFoundError
        from graphiti_core.nodes import EpisodicNode

        last_error: Optional[Exception] = None
        for candidate in self._resolve_graph_ids(graph_id):
            try:
                graphiti = await self.runtime.get_graph(candidate)
                return await EpisodicNode.get_by_uuid(graphiti.driver, episode_uuid)
            except NodeNotFoundError as exc:
                last_error = exc
        raise GraphNotFoundError(str(last_error or f"episode {episode_uuid} not found"))

    async def _search(
        self, graph_id: str, query: str, limit: int, scope: Optional[str]
    ) -> _SearchResponseShim:
        graphiti = await self.runtime.get_graph(graph_id)
        group_ids = [graph_id]
        results = _SearchResponseShim()

        if scope in (None, "edges", "both"):
            edges = await graphiti.search(query, group_ids=group_ids, num_results=limit)
            results.edges = [_EdgeShim(edge) for edge in edges]

        if scope in (None, "nodes", "both"):
            from graphiti_core.search.search import search as low_level_search
            from graphiti_core.search.search_config import (
                NodeReranker,
                NodeSearchConfig,
                NodeSearchMethod,
                SearchConfig,
            )
            from graphiti_core.search.search_filters import SearchFilters

            node_config = NodeSearchConfig(
                search_methods=[NodeSearchMethod.bm25, NodeSearchMethod.cosine_similarity],
                reranker=NodeReranker.rrf,
                limit=limit,
            )
            search_results = await low_level_search(
                graphiti.clients,
                query,
                group_ids,
                SearchConfig(node_config=node_config, limit=limit),
                SearchFilters(),
            )
            results.nodes = [_NodeShim(node) for node in search_results.nodes]

        return results


class _GraphNamespace:
    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client
        self.node = _NodeNamespace(client)
        self.edge = _EdgeNamespace(client)
        self.episode = _EpisodeNamespace(client)

    def create(
        self,
        *,
        graph_id: str,
        name: str = "MiroFish Social Simulation Graph",
        description: str = "",
        **_: Any,
    ) -> _GraphShim:
        directory = graph_dir(graph_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "graph.meta.json").write_text(
            json.dumps({"graph_id": graph_id, "name": name}, ensure_ascii=False), "utf-8"
        )
        return _GraphShim(graph_id, name, description)

    def get(self, graph_id: str, **_: Any) -> _GraphShim:
        if not graph_dir(graph_id).exists():
            raise GraphNotFoundError(f"graph {graph_id} does not exist locally")
        return _GraphShim(graph_id)

    def delete(self, *, graph_id: str, **_: Any) -> None:
        async def _forget() -> None:
            await self._client.runtime.forget_graph(graph_id)

        try:
            self._client.runtime.run(_forget())
        except Exception as exc:  # a forget failure must not block the delete
            logger.warning("graphiti forget during delete failed: %s", exc)
        directory = graph_dir(graph_id)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    def set_ontology(
        self,
        *,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> None:
        """Persist an ontology spec from Zep's dynamic EntityModel/EdgeModel
        classes (name, docstring description, source_targets)."""

        spec: Dict[str, Any] = {"entities": [], "edges": []}

        for name, model in (entities or {}).items():
            spec["entities"].append(
                {
                    "name": name,
                    "description": (model.__doc__ or "").strip(),
                    "attributes": [
                        {"name": field_name, "description": field.description or ""}
                        for field_name, field in model.model_fields.items()
                    ],
                }
            )

        for name, payload in (edges or {}).items():
            model, source_targets = payload
            spec["edges"].append(
                {
                    "name": name,
                    "description": (model.__doc__ or "").strip(),
                    "source_targets": [
                        {
                            "source": getattr(st, "source", "Entity") or "Entity",
                            "target": getattr(st, "target", "Entity") or "Entity",
                        }
                        for st in (source_targets or [])
                    ],
                }
            )

        for graph_id in graph_ids:
            path = _ontology_path(graph_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")

            async def _reload() -> None:
                await self._client.runtime.forget_graph(graph_id)

            try:
                self._client.runtime.run(_reload())
            except Exception as exc:
                logger.debug("graphiti forget after set_ontology failed: %s", exc)

    def add(
        self,
        *,
        graph_id: str,
        type: str = "text",
        data: str = "",
        created_at: Optional[str] = None,
        source_description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> _EpisodeShim:
        """Ingest one episode synchronously (simulation memory updates)."""

        results = self._client.runtime.run(
            self._client._add_single_episode(
                graph_id,
                data,
                source_description=source_description,
                created_at=created_at,
            )
        )
        episode = getattr(results, "episode", None)
        if episode is None:  # defensive: never hand back a UUID-less episode
            raise RuntimeError("graphiti add_episode returned no episode")
        return _EpisodeShim(episode)

    def search(
        self,
        *,
        query: str,
        graph_id: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: Optional[str] = None,
        **_: Any,
    ) -> _SearchResponseShim:
        return self._client.runtime.run(
            self._client._search(graph_id, query, limit=limit, scope=scope)
        )


class _NodeNamespace:
    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client
        self.with_raw_response = _NodeRawNamespace(client)

    def get(self, *, uuid_: str, **_: Any) -> _NodeShim:
        node = self._client.runtime.run(self._client._read_node(None, uuid_))
        return _NodeShim(node)

    def get_edges(self, *, node_uuid: str, **_: Any) -> List[_EdgeShim]:
        edges = self._client.runtime.run(self._client._read_node_edges(None, node_uuid))
        return [_EdgeShim(edge) for edge in edges or []]


class _NodeRawNamespace:
    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client

    def get_by_graph_id(
        self, graph_id: str, *, limit: int = 100, cursor: Optional[str] = None, **_: Any
    ) -> _PagedResponseShim:
        nodes = self._client.runtime.run(self._client._read_nodes(graph_id))
        return _PagedResponseShim([_NodeShim(node) for node in nodes or []])


class _EdgeRawNamespace:
    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client

    def get_by_graph_id(
        self, graph_id: str, *, limit: int = 100, cursor: Optional[str] = None, **_: Any
    ) -> _PagedResponseShim:
        edges = self._client.runtime.run(self._client._read_edges(graph_id))
        return _PagedResponseShim([_EdgeShim(edge) for edge in edges or []])


class _EdgeNamespace:
    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client
        self.with_raw_response = _EdgeRawNamespace(client)


class _EpisodeNamespace:
    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client

    def get(self, *, uuid_: str, **_: Any) -> _EpisodeShim:
        episode = self._client.runtime.run(self._client._read_episode(None, uuid_))
        return _EpisodeShim(episode)


class _BatchNamespace:
    """Zep Batch API surface: create / add / process / get / list_items / list."""

    def __init__(self, client: GraphitiZepClient) -> None:
        self._client = client

    def create(self, *, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> _BatchShim:
        metadata = metadata or {}
        record = {
            "batch_id": f"local_{uuid.uuid4().hex[:16]}",
            "operation_id": metadata.get("mirofish_operation_id"),
            "graph_id": metadata.get("graph_id", ""),
            "status": "draft",
            "error": None,
            "metadata": metadata,
            "items": [],
            "done": 0,
            "skipped": 0,
            "skipped_indices": [],
            "created_at": _utc_now().isoformat(),
            "updated_at": _utc_now().isoformat(),
        }
        self._client._save_record(record)
        return _BatchShim(record)

    def add(self, *, batch_id: str, items: List[Any], **_: Any) -> List[_BatchItemShim]:
        graph_id = getattr(items[0], "graph_id", None) if items else None
        if not graph_id:
            raise ValueError("batch items carry no graph_id")
        record = self._client._load_record(graph_id, batch_id)
        if record["status"] not in {"draft", "processing"}:
            raise RuntimeError(f"batch {batch_id} is {record['status']}; cannot add items")

        existing = len(record["items"])
        for offset, item in enumerate(items):
            chunk = getattr(item, "data", "") or ""
            record["items"].append(
                {
                    "sequence_index": existing + offset,
                    "episode_uuid": uuid.uuid4().hex,
                    "sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    "chunk": chunk,
                    "status": "pending",
                    "error": None,
                }
            )
        record["metadata"]["chunk_count"] = len(record["items"])
        self._client._save_record(record)
        return [_BatchItemShim(item) for item in record["items"][existing:]]

    def process(self, *, batch_id: str, **_: Any) -> _BatchShim:
        graph_id = self._graph_id_of(batch_id)
        record = self._client._load_record(graph_id, batch_id)
        if record["status"] in {"draft", "processing"}:
            self._client._ensure_ingestion(record)
        return _BatchShim(record)

    def get(self, *, batch_id: str, **_: Any) -> _BatchShim:
        graph_id = self._graph_id_of(batch_id)
        record = self._client._load_record(graph_id, batch_id)
        if record["status"] in {"draft", "processing"}:
            self._client._ensure_ingestion(record)
        return _BatchShim(record)

    def list_items(
        self, *, batch_id: str, limit: int = 100, cursor: Optional[str] = None, **_: Any
    ) -> _BatchPageShim:
        graph_id = self._graph_id_of(batch_id)
        record = self._client._load_record(graph_id, batch_id)
        items = sorted(record["items"], key=lambda entry: entry["sequence_index"])
        return _BatchPageShim([_BatchItemShim(item) for item in items])

    def list(self, *, limit: int = 100, cursor: Optional[str] = None, **_: Any) -> _BatchListShim:
        # Reconciliation path in GraphBuilderService never matches locally: batch
        # ids are generated by this backend itself and tracked under the graph dir.
        return _BatchListShim([])

    def _graph_id_of(self, batch_id: str) -> str:
        for graph_id in _iter_graph_ids():
            if _batch_path(graph_id, batch_id).exists():
                return graph_id
        raise GraphNotFoundError(f"batch {batch_id} is unknown locally")


_CLIENT = GraphitiZepClient()


def get_graphiti_zep_client() -> GraphitiZepClient:
    """Return the process-shared local graph client (mirrors get_zep_client)."""

    return _CLIENT
