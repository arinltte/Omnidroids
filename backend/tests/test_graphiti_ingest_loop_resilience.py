# SPDX-License-Identifier: AGPL-3.0-or-later
"""#22: a gone ingest loop must leave chunks pending, not skipped.

The §12.4 SIGTERM-mid-build run stopped the runtime while chunk 5 was in
flight: ``_ingest_record`` kept iterating, recorded chunks 6-18 as ``skipped``
with ``cannot schedule new futures after shutdown`` and terminated the batch
``failed`` — chunks that were never attempted became un-resumable. These tests
pin the "loop is gone" class apart from M2's genuine per-chunk fault isolation.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils import graphiti_zep

GRAPH_ID = "graph_22_loop"
BATCH_ID = "local_loop_resilience"

# Verbatim from the acceptance batch record (items 6-18).
KUZU_SHUTDOWN_ERROR = "cannot schedule new futures after shutdown"
# A chunk-level failure the small model produced in the same run (items 2-5).
SCHEMA_ERROR = (
    "1 validation error for ExtractedEntities\nextracted_entities\n"
    "  Field required [type=missing, input_value={'entities': []}, input_type=dict]"
)


def _episode():
    return SimpleNamespace(episode=SimpleNamespace(uuid="episode-uuid"))


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


class _FakeGraphiti:
    """graphiti_core.Graphiti stand-in: one scripted outcome per `add_episode`."""

    def __init__(self, outcomes=()):
        self._outcomes = list(outcomes)
        self.calls = []

    async def add_episode(self, **kwargs):
        self.calls.append(kwargs)
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return _episode()


class _RuntimeLoop:
    """The production shape `_ingest_record` runs in: one loop alive in its own
    thread, never the test's main loop."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=60)

    def stop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=60)
        self._loop.close()


@pytest.fixture()
def live_loop():
    loop = _RuntimeLoop()
    try:
        yield loop
    finally:
        loop.stop()


def _client_for(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path))
    return graphiti_zep.GraphitiZepClient()


def _record_for(client, chunks=3):
    record = {
        "batch_id": BATCH_ID,
        "operation_id": "operation_22",
        "graph_id": GRAPH_ID,
        "status": "processing",
        "error": None,
        "metadata": {"graph_id": GRAPH_ID, "chunk_count": chunks},
        "items": [
            {
                "sequence_index": index,
                "episode_uuid": f"placeholder-{index}",
                "sha256": "0" * 64,
                "chunk": f"Section {index}: Dr. Ada Chen joined the institute.",
                "status": "pending",
                "error": None,
            }
            for index in range(chunks)
        ],
        "done": 0,
        "skipped": 0,
        "skipped_indices": [],
        "created_at": graphiti_zep._utc_now().isoformat(),
        "updated_at": graphiti_zep._utc_now().isoformat(),
    }
    client._save_record(record)
    return record


def _wire_runtime(client, loop, graphiti):
    """Swap in the only collaborators `_ingest_record` drives: the shared runtime
    loop and a scripted Graphiti instance — no Kuzu, no LLM, no model."""

    client.runtime = SimpleNamespace(
        _loop=loop,
        get_graph=_async_return(graphiti),
        episode_lock=lambda _graph_id: asyncio.Lock(),
    )


def _on_disk(client):
    return client._load_record(GRAPH_ID, BATCH_ID)


def _items(on_disk_record):
    return {item["sequence_index"]: item for item in on_disk_record["items"]}


def _drain(client, timeout=60):
    return client._batch_futures[BATCH_ID].result(timeout=timeout)


# -- the loop-gone class -----------------------------------------------------


def test_ingest_loop_gone_leaves_unattempted_chunks_pending_and_batch_non_terminal(
    tmp_path, monkeypatch, live_loop
):
    client = _client_for(tmp_path, monkeypatch)
    record = _record_for(client, chunks=4)
    graphiti = _FakeGraphiti(
        outcomes=[_episode(), RuntimeError(KUZU_SHUTDOWN_ERROR), _episode()]
    )
    _wire_runtime(client, live_loop._loop, graphiti)

    live_loop.run(client._ingest_record(record))

    items = _items(_on_disk(client))
    # The chunk in flight never landed: it stays resumable, not skipped.
    assert items[1]["status"] == "pending"
    assert items[1]["error"] is None
    assert items[1]["episode_uuid"] == "placeholder-1"
    # Every chunk after it is untouched too.
    assert [items[2]["status"], items[3]["status"]] == ["pending", "pending"]
    # The loop stopped: chunk 2 and 3 were never attempted.
    assert len(graphiti.calls) == 2
    # The record is the resume plan and stays non-terminal so `_ensure_ingestion`
    # / `batch.get` self-heal it on restart.
    on_disk_record = _on_disk(client)
    assert on_disk_record["status"] not in {"succeeded", "failed"}
    assert on_disk_record["status"] == "processing"
    assert on_disk_record["done"] == 1
    assert on_disk_record["skipped"] == 0
    assert on_disk_record["skipped_indices"] == []
    assert on_disk_record["error"] is None
    assert all(item["status"] != "skipped" for item in on_disk_record["items"])


def test_closed_runtime_loop_attempts_nothing_and_leaves_every_chunk_pending(
    tmp_path, monkeypatch, live_loop
):
    client = _client_for(tmp_path, monkeypatch)
    record = _record_for(client, chunks=3)
    graphiti = _FakeGraphiti()
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    _wire_runtime(client, dead_loop, graphiti)

    live_loop.run(client._ingest_record(record))

    on_disk_record = _on_disk(client)
    assert graphiti.calls == []
    assert [item["status"] for item in on_disk_record["items"]] == ["pending"] * 3
    assert on_disk_record["done"] == 0
    assert on_disk_record["skipped"] == 0
    assert on_disk_record["status"] == "processing"


def test_interrupted_batch_is_re_entered_and_completes_the_pending_chunks(
    tmp_path, monkeypatch, live_loop
):
    """The resume plan left on disk must be one the resume logic understands."""

    client = _client_for(tmp_path, monkeypatch)
    record = _record_for(client, chunks=3)
    first_pass = _FakeGraphiti(
        outcomes=[_episode(), RuntimeError(KUZU_SHUTDOWN_ERROR)]
    )
    _wire_runtime(client, live_loop._loop, first_pass)

    live_loop.run(client._ingest_record(record))
    interrupted = _on_disk(client)
    assert interrupted["status"] == "processing"

    # Restart: a fresh loop, a live Graphiti instance, same batch record. This is
    # exactly what `batch.get` / `_ensure_ingestion` does after a restart.
    second_pass = _FakeGraphiti(outcomes=[_episode(), _episode()])
    client.runtime = SimpleNamespace(
        _loop=live_loop._loop,
        _ensure_loop=lambda: live_loop._loop,
        get_graph=_async_return(second_pass),
        episode_lock=lambda _graph_id: asyncio.Lock(),
    )
    client._ensure_ingestion(interrupted)
    _drain(client)

    resumed = _on_disk(client)
    assert [item["status"] for item in resumed["items"]] == ["succeeded"] * 3
    assert resumed["done"] == 3
    assert resumed["skipped"] == 0
    assert resumed["status"] == "succeeded"
    # Only the two unattempted chunks were retried.
    assert len(second_pass.calls) == 2


# -- M2 fault isolation must not move ---------------------------------------


def test_genuine_chunk_failure_still_skips_and_continues_with_the_rest(
    tmp_path, monkeypatch, live_loop
):
    client = _client_for(tmp_path, monkeypatch)
    record = _record_for(client, chunks=3)
    graphiti = _FakeGraphiti(
        outcomes=[RuntimeError(SCHEMA_ERROR), _episode(), RuntimeError(SCHEMA_ERROR)]
    )
    _wire_runtime(client, live_loop._loop, graphiti)

    live_loop.run(client._ingest_record(record))

    items = _items(_on_disk(client))
    assert [items[0]["status"], items[1]["status"], items[2]["status"]] == [
        "skipped",
        "succeeded",
        "skipped",
    ]
    assert items[0]["error"].startswith("1 validation error for ExtractedEntities")
    on_disk_record = _on_disk(client)
    assert on_disk_record["done"] == 1
    assert on_disk_record["skipped"] == 2
    assert on_disk_record["skipped_indices"] == [0, 2]
    # Partially failed still terminates succeeded (M2 batch terminator).
    assert on_disk_record["status"] == "succeeded"
    assert on_disk_record["error"] == "skipped 2/3 chunks: [0, 2]"
    assert len(graphiti.calls) == 3


def test_all_chunks_failed_still_terminates_the_batch_failed(
    tmp_path, monkeypatch, live_loop
):
    client = _client_for(tmp_path, monkeypatch)
    record = _record_for(client, chunks=3)
    graphiti = _FakeGraphiti(outcomes=[RuntimeError(SCHEMA_ERROR)] * 3)
    _wire_runtime(client, live_loop._loop, graphiti)

    live_loop.run(client._ingest_record(record))

    on_disk_record = _on_disk(client)
    assert on_disk_record["done"] == 0
    assert on_disk_record["skipped"] == 3
    assert on_disk_record["status"] == "failed"
    assert on_disk_record["error"] == "skipped 3/3 chunks: [0, 1, 2]"


def test_unrelated_runtime_error_is_a_chunk_skip_not_a_resume(
    tmp_path, monkeypatch, live_loop
):
    """False-positive guard for the predicate: a RuntimeError is not evidence."""

    client = _client_for(tmp_path, monkeypatch)
    record = _record_for(client, chunks=3)
    graphiti = _FakeGraphiti(
        outcomes=[
            RuntimeError("model returned no JSON body"),
            _episode(),
            ValueError("graphiti validation failed"),
        ]
    )
    _wire_runtime(client, live_loop._loop, graphiti)

    live_loop.run(client._ingest_record(record))

    items = _items(_on_disk(client))
    assert items[0]["status"] == "skipped"
    assert items[0]["error"] == "model returned no JSON body"
    assert items[1]["status"] == "succeeded"
    assert items[2]["status"] == "skipped"
    on_disk_record = _on_disk(client)
    assert on_disk_record["skipped_indices"] == [0, 2]
    assert on_disk_record["status"] == "succeeded"
    assert len(graphiti.calls) == 3


# -- the predicate itself ----------------------------------------------------


def test_runtime_loop_gone_predicate_is_pure_and_precise(live_loop):
    closed = asyncio.new_event_loop()
    closed.close()

    gone_cases = [
        (None, None),
        (closed, None),
        (live_loop._loop, RuntimeError(KUZU_SHUTDOWN_ERROR)),
        (live_loop._loop, RuntimeError("Error executing Kuzu query: " + KUZU_SHUTDOWN_ERROR)),
        (closed, RuntimeError("Event loop is closed")),
    ]
    for loop, exc in gone_cases:
        assert graphiti_zep._runtime_loop_gone(loop, exc) is True, (loop, exc)

    alive_cases = [
        (live_loop._loop, None),
        (live_loop._loop, RuntimeError("model returned no JSON body")),
        (live_loop._loop, ValueError("graphiti validation failed")),
        (live_loop._loop, RuntimeError(SCHEMA_ERROR)),
    ]
    for loop, exc in alive_cases:
        assert graphiti_zep._runtime_loop_gone(loop, exc) is False, (loop, exc)
