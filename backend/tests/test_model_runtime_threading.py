# SPDX-License-Identifier: AGPL-3.0-or-later
"""MLX runtime: every Metal call for a model runs on ONE dedicated thread.

Measured failure this guards (scratch/check24_9b_full_a.log): with
``GRAPHITI_MAX_COROUTINES=2`` the 9B full-ingestion run answered 17 calls with
``Error code: 502 - {'message': 'local model error: There is no Stream(gpu, N)
in current thread.'}`` — 4 of 6 chunks failed. MLX's GPU streams are thread
local and ``mlx_vlm.generate`` mutates shared model state (the KV cache), and
``MLXRunner.generate`` used to dispatch each call through ``asyncio.to_thread``
(i.e. an arbitrary pool thread per call), while ``load`` ran on a temporary
one-worker pool that was discarded. Sequential node-mode runs of the same model
were clean (12/12), which is what pins the defect to concurrency, not to the
model or the prompt.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest


class _FakeResult(SimpleNamespace):
    text = "{}"
    finish_reason = "stop"
    prompt_tokens = 1
    generation_tokens = 1


def _install_fake_mlx(monkeypatch, events):
    """A fake ``mlx_vlm``/``mlx`` good enough for MLXRunner.load/generate."""

    import sys

    def _load(path):
        events.append(("load", threading.current_thread(), time.monotonic()))
        return SimpleNamespace(config=SimpleNamespace(), parameters=dict), SimpleNamespace()

    def _generate(model, processor, prompt, **kwargs):
        events.append(("gen", threading.current_thread(), time.monotonic()))
        time.sleep(0.05)  # long enough for a second call to overlap if it could
        events.append(("gen-done", threading.current_thread(), time.monotonic()))
        return _FakeResult()

    import types

    vlm = types.ModuleType("mlx_vlm")
    vlm.load = _load
    vlm.generate = _generate
    vlm.apply_chat_template = (
        lambda processor, config, messages, add_generation_prompt=True: "prompt"
    )
    core = types.ModuleType("mlx.core")
    core.eval = lambda *a, **k: None
    core.clear_cache = lambda: None
    mlx = types.ModuleType("mlx")
    mlx.__path__ = []          # a package, so `import mlx.core` resolves
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx_vlm", vlm)
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)


@pytest.fixture()
def runner(monkeypatch):
    from app.models.runtime import MLXRunner

    events: list[tuple] = []
    _install_fake_mlx(monkeypatch, events)
    return MLXRunner("fake-mlx", "/tmp/fake-mlx"), events


def _thread_of(events, kind: str) -> int:
    return next(t for k, t, _ in events if k == kind)


def _generation_spans(events) -> list[tuple[float, float]]:
    """(start, end) per completed generation, in start order."""

    open_starts: list[float] = []
    spans: list[tuple[float, float]] = []
    for kind, _thread, at in events:
        if kind == "gen":
            open_starts.append(at)
        elif kind == "gen-done" and open_starts:
            spans.append((open_starts.pop(0), at))
    return sorted(spans)


def _dedicated_worker_threads(mlx):
    """The runner's live worker threads, observed through their public name.

    ``MLXRunner`` builds its one-worker executor with
    ``thread_name_prefix=f"mlx-{self.name}"``, so the dedicated thread shows up
    in ``threading.enumerate()`` — the same fact the executor's private
    ``_threads`` carried, without reaching into a private attribute of a
    stdlib object.
    """

    prefix = f"mlx-{mlx.name}"
    return [t for t in threading.enumerate() if t.name.startswith(prefix)]


def test_generate_runs_on_the_thread_that_loaded_the_model(runner):
    mlx, events = runner

    asyncio.run(mlx.load())
    asyncio.run(mlx.generate(messages=[{"role": "user", "content": "hi"}], max_tokens=8))

    gen_threads = [t for k, t, _ in events if k == "gen"]
    assert gen_threads == [_thread_of(events, "load")], (
        "MLX streams are thread local: generate must reuse the loader thread"
    )


def test_concurrent_generates_are_serialized_on_one_thread(runner):
    mlx, events = runner

    asyncio.run(mlx.load())

    async def _both():
        return await asyncio.gather(
            mlx.generate(messages=[{"role": "user", "content": "a"}], max_tokens=8),
            mlx.generate(messages=[{"role": "user", "content": "b"}], max_tokens=8),
        )

    asyncio.run(_both())

    spans = _generation_spans(events)
    assert len(spans) == 2
    assert {t for k, t, _ in events if k == "gen"} == {_thread_of(events, "load")}
    # the two windows must not overlap: one model object, one GPU, one worker
    assert spans[0][1] <= spans[1][0], "two generations overlapped on one model"


def test_unload_releases_the_dedicated_thread(runner):
    mlx, events = runner

    asyncio.run(mlx.load())
    assert _dedicated_worker_threads(mlx), (
        "load must run its work on one dedicated worker thread"
    )

    asyncio.run(mlx.unload())

    assert not _dedicated_worker_threads(mlx), (
        "the dedicated worker thread must be joined on unload; it is still "
        "alive under its mlx-<model> name"
    )
