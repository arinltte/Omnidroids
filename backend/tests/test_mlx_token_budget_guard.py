# SPDX-License-Identifier: AGPL-3.0-or-later
"""Step-1 prepare 502 on ``small``: an explicit token budget + a context guard (#51).

The measured defect (scratch/accept48_run.py ``step1_prepare_defect``): the
ontology call passed ``max_tokens=None``, ``MLXRunner.generate`` dropped that
falsy kwarg, mlx-vlm then applied its own ``DEFAULT_MAX_TOKENS = 2048`` and the
1.7B's ontology JSON truncated (``finish_reason=length`` → 502), while the
17,189-token prompt went in unchecked against ``LLM_CONTEXT_LENGTH=8192``.

Everything here runs against a fake ``mlx_vlm`` (the
tests/test_model_runtime_threading.py pattern): no real weights are loaded.
"""

import asyncio
import io
import logging
from types import SimpleNamespace

import pytest

from app.config import Config
from app.models.runtime import MLXRunner, PromptTooLongError
from app.services.ontology_generator import OntologyGenerator
from app.utils.llm_client import LLMClient


ONTOLOGY_BODY = '{"entity_types": [], "edge_types": [], "analysis_summary": "ok"}'
RUNTIME_LOGGER = "omnidroids.models.runtime"


class _CountingTokenizer:
    """A stand-in for the model's own tokenizer: one token per word."""

    def encode(self, text, add_special_tokens=True):
        return [0] * len(str(text).split())


class _FakeResult(SimpleNamespace):
    text = ONTOLOGY_BODY
    finish_reason = "stop"
    prompt_tokens = 1
    generation_tokens = 1


def _install_fake_mlx(monkeypatch, calls):
    """A fake ``mlx_vlm`` whose tokenizer counts words (1 token = 1 word)."""

    import sys
    import types

    def _load(path):
        model = SimpleNamespace(config=SimpleNamespace(), parameters=dict)
        processor = SimpleNamespace(tokenizer=_CountingTokenizer())
        return model, processor

    def _generate(model, processor, prompt, **kwargs):
        calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        return _FakeResult()

    def _apply_chat_template(processor, config, messages, add_generation_prompt=True):
        return "\n".join(str(m.get("content", "")) for m in messages)

    vlm = types.ModuleType("mlx_vlm")
    vlm.load = _load
    vlm.generate = _generate
    vlm.apply_chat_template = _apply_chat_template
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
def mlx(monkeypatch):
    calls: list[dict] = []
    _install_fake_mlx(monkeypatch, calls)
    runner = MLXRunner("fake-mlx", "/tmp/fake-mlx")
    asyncio.run(runner.load())
    return runner, calls


class _LogCapture:
    """The runtime logger does not propagate, so read its own handler."""

    def __init__(self):
        self.buffer = io.StringIO()
        self._handler = logging.StreamHandler(self.buffer)
        self._logger = logging.getLogger(RUNTIME_LOGGER)

    def __enter__(self):
        self._logger.addHandler(self._handler)
        return self.buffer

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        return False


class _RunnerBackedCompletions:
    """The /v1 hop, in-process: an OpenAI-shaped client routed to MLXRunner."""

    def __init__(self, runner):
        self.runner = runner

    def create(self, **kwargs):
        result = asyncio.run(self.runner.generate(
            messages=kwargs.get("messages") or [],
            max_tokens=kwargs.get("max_tokens"),
            temperature=kwargs.get("temperature"),
        ))
        return SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason=result.finish_reason,
                message=SimpleNamespace(content=result.content),
            )]
        )


def _llm_client_for(runner) -> LLMClient:
    client = object.__new__(LLMClient)
    client.api_key = "local"
    client.base_url = "http://testserver/v1"
    client.model = "LFM3-1.7B-MLX-4bit"
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_RunnerBackedCompletions(runner))
    )
    return client


# --- (a) the token budget is explicit, not the provider's silent default ------

def test_explicit_token_budget_survives_the_hop_into_mlx(mlx):
    """max_tokens must reach mlx_vlm.generate instead of being dropped (#51)."""
    runner, calls = mlx

    asyncio.run(runner.generate(
        messages=[{"role": "user", "content": "short prompt"}],
        max_tokens=Config.ONTOLOGY_LLM_MAX_TOKENS,
    ))

    assert calls[-1]["kwargs"]["max_tokens"] == Config.ONTOLOGY_LLM_MAX_TOKENS


def test_uncapped_request_keeps_the_provider_budget_but_logs_it(mlx):
    """A caller that wants no clamp keeps it, and the effective budget is logged."""
    runner, calls = mlx

    with _LogCapture() as output:
        asyncio.run(runner.generate(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=None,
        ))

    assert "max_tokens" not in calls[-1]["kwargs"]
    assert "prompt_tokens=2 max_tokens=provider default" in output.getvalue()


def test_runtime_budget_is_logged_on_every_generate(mlx):
    runner, calls = mlx

    with _LogCapture() as output:
        asyncio.run(runner.generate(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=1234,
        ))

    assert "prompt_tokens=2 max_tokens=1234" in output.getvalue()


# --- (b) the guard fires on an oversized prompt -------------------------------

def test_guard_rejects_a_prompt_larger_than_the_context_window(mlx, monkeypatch):
    runner, calls = mlx
    monkeypatch.setattr(Config, "LLM_CONTEXT_LENGTH", 16)

    messages = [
        {"role": "system", "content": " ".join(["sys"] * 10)},
        {"role": "user", "content": " ".join(["doc"] * 40)},
    ]
    with pytest.raises(PromptTooLongError) as excinfo:
        asyncio.run(runner.generate(messages=messages, max_tokens=4096))

    message = str(excinfo.value)
    assert "50 tokens" in message                     # the assembled prompt
    assert "16-token context window" in message       # the resolved budget
    assert "over by 34" in message                   # how far over
    assert "user message #2, 40 tokens" in message    # what pushed it over
    assert calls == [], "an oversized prompt must never reach generation"


# --- (c) the guard does not fire on a normal-sized prompt ---------------------

def test_guard_allows_a_prompt_inside_the_context_window(mlx):
    runner, calls = mlx

    result = asyncio.run(runner.generate(
        messages=[{"role": "user", "content": "short prompt"}],
        max_tokens=4096,
    ))

    assert result.content == ONTOLOGY_BODY
    assert result.finish_reason == "stop"
    assert calls, "a prompt inside the context window must still be generated"


# --- (d) the ontology path end to end against the fake mlx_vlm ---------------

def test_ontology_generation_reaches_mlx_with_the_configured_budget(mlx):
    """Step-1 prepare: the budget must be deliberate and arrive at mlx_vlm."""
    runner, calls = mlx
    generator = OntologyGenerator(llm_client=_llm_client_for(runner))

    ontology = generator.generate(
        document_texts=["A short source document."],
        simulation_requirement="Simulate the public discussion.",
    )

    assert ontology["analysis_summary"] == "ok"
    assert calls[-1]["kwargs"]["max_tokens"] == Config.ONTOLOGY_LLM_MAX_TOKENS


def test_ontology_generation_fails_precisely_on_an_oversized_prompt(mlx, monkeypatch):
    runner, calls = mlx
    monkeypatch.setattr(Config, "LLM_CONTEXT_LENGTH", 16)
    generator = OntologyGenerator(llm_client=_llm_client_for(runner))

    with pytest.raises(PromptTooLongError) as excinfo:
        generator.generate(
            document_texts=[" ".join(["doc"] * 40)],
            simulation_requirement="Simulate the public discussion.",
        )

    assert "over by" in str(excinfo.value)
    assert calls == [], "an oversized prompt must never reach generation"
