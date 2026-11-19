# SPDX-License-Identifier: AGPL-3.0-or-later
"""Step-1 prepare 502 on ``small``: an explicit token budget + a context guard (#51).

The measured defect (scratch/accept48_run.py ``step1_prepare_defect``): the
ontology call passed ``max_tokens=None``, ``MLXRunner.generate`` dropped that
falsy kwarg, mlx-vlm then applied its own ``DEFAULT_MAX_TOKENS = 2048`` and the
1.7B's ontology JSON truncated (``finish_reason=length`` → 502), while the
17,189-token prompt went in unchecked.

Two thresholds, on purpose. The hard one is the checkpoint's own window
(``max_position_embeddings``: 262,144 on the 1.7B and the 9B);
``LLM_CONTEXT_LENGTH`` (8192) is the operational cap that feeds
``llm_monitor``'s ``num_ctx`` report, not the capability — ACCEPTANCE_12 §2
records that 17,189-token prompt as "context, not the cause", with the same
endpoint succeeding while it was in place. Over the cap the guard warns loudly;
over the model window it raises ``PromptTooLongError``.

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
# The 1.7B checkpoint's own window; the 9B declares the same number.
MODEL_WINDOW = 262144


class _CountingTokenizer:
    """A stand-in for the model's own tokenizer: one token per word."""

    def encode(self, text, add_special_tokens=True):
        return [0] * len(str(text).split())


class _FakeResult(SimpleNamespace):
    text = ONTOLOGY_BODY
    finish_reason = "stop"
    prompt_tokens = 1
    generation_tokens = 1


def _install_fake_mlx(monkeypatch, calls, *, model_window=MODEL_WINDOW):
    """A fake ``mlx_vlm`` whose tokenizer counts words (1 token = 1 word).

    ``model_window`` becomes the loaded model's ``max_position_embeddings``;
    None leaves the config without one, which is the fallback path.
    """

    import sys
    import types

    def _load(path):
        config = SimpleNamespace()
        if model_window is not None:
            config.max_position_embeddings = model_window
        model = SimpleNamespace(config=config, parameters=dict)
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
        self._handler.setFormatter(
            logging.Formatter("%(levelname)s: %(message)s")
        )
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


def test_an_uncapped_request_never_inherits_mlx_vlms_2048_default(mlx):
    """A caller that omits the cap gets an explicit, config-backed budget.

    This used to leave the kwarg out, which lets ``mlx_vlm.generate`` apply its
    own ``DEFAULT_MAX_TOKENS = 2048`` -- measured in #51's acceptance run, where
    ``LLMClient.chat_json``'s JSON retry omits the cap "so the provider can use
    its model-specific output limit" and locally there is no such limit, so the
    retry truncated the 1.7B's ontology JSON a second time and the endpoint
    answered 502 again. The runner now resolves one budget and logs it, so an
    omitted cap is a logged decision rather than a silent inheritance.
    """
    runner, calls = mlx

    with _LogCapture() as output:
        asyncio.run(runner.generate(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=None,
        ))

    assert calls[-1]["kwargs"]["max_tokens"] == Config.MLX_MAX_TOKENS
    assert (
        f"prompt_tokens=2 max_tokens={Config.MLX_MAX_TOKENS}" in output.getvalue()
    )
    # the budget is not the inherited 2048 -- that is the whole point
    assert Config.MLX_MAX_TOKENS != 2048


def test_the_runner_default_budget_is_not_mlx_vlms(monkeypatch):
    """Pin the constant against the library it must never inherit from."""
    import mlx_vlm

    library_default = getattr(mlx_vlm, "DEFAULT_MAX_TOKENS", None)
    if library_default is None:  # the fake module in the tests above
        library_default = 2048
    assert Config.MLX_MAX_TOKENS != library_default


def test_the_json_retry_chain_cannot_land_on_the_library_default(mlx):
    """#51's acceptance finding, pinned end to end.

    The measured chain: ontology call at 4096 -> unusable JSON ->
    ``chat_json`` drops the cap for the retry -> the runner used to forward
    nothing -> mlx-vlm's 2048 -> truncation -> 502. This drives
    ``MLXRunner.generate`` through the same ``max_tokens=None`` the retry sends
    and asserts the kwarg that reaches the library is the resolved budget.
    """
    runner, calls = mlx

    # exactly what the retry sends: no cap at all
    asyncio.run(runner.generate(
        messages=[{"role": "user", "content": "a prompt"}],
        max_tokens=None,
    ))

    assert "max_tokens" in calls[-1]["kwargs"], (
        "an omitted cap must not leave the kwarg absent"
    )
    assert calls[-1]["kwargs"]["max_tokens"] == Config.MLX_MAX_TOKENS


def test_runtime_budget_is_logged_on_every_generate(mlx):
    runner, calls = mlx

    with _LogCapture() as output:
        asyncio.run(runner.generate(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=1234,
        ))

    assert "prompt_tokens=2 max_tokens=1234" in output.getvalue()


# --- (b) above the MODEL window: hard failure with a precise message ----

def test_guard_hard_fails_above_the_models_own_context_window(monkeypatch):
    calls: list[dict] = []
    _install_fake_mlx(monkeypatch, calls, model_window=32)
    runner = MLXRunner("fake-mlx", "/tmp/fake-mlx")
    asyncio.run(runner.load())

    messages = [
        {"role": "system", "content": " ".join(["sys"] * 10)},
        {"role": "user", "content": " ".join(["doc"] * 40)},
    ]
    with pytest.raises(PromptTooLongError) as excinfo:
        asyncio.run(runner.generate(messages=messages, max_tokens=4096))

    message = str(excinfo.value)
    assert "prompt of 50 tokens" in message            # the assembled prompt
    assert "32-token context window" in message        # the model's own window
    assert "over by 18" in message                     # how far over
    assert "user message #2, 40 tokens" in message     # what pushed it over
    assert "raise LLM_CONTEXT_LENGTH" in message       # both remedies named
    assert "shorten the documents or the simulation requirement" in message
    assert "never silently truncated" in message
    assert calls == [], "an impossible prompt must never reach generation"


# --- (a) over the operational cap, inside the model window: warn only -------

def test_prompt_above_the_operational_cap_only_warns(monkeypatch):
    """The owner's real shape: 17,189 tokens, cap 8192, window 262144.

    ACCEPTANCE_12 §2 records that prompt as "context, not the cause" and the
    same endpoint succeeding with it in place, so it must not be blocked.
    """

    calls: list[dict] = []
    _install_fake_mlx(monkeypatch, calls)
    runner = MLXRunner("fake-mlx", "/tmp/fake-mlx")
    asyncio.run(runner.load())
    monkeypatch.setattr(Config, "LLM_CONTEXT_LENGTH", 8192)

    messages = [
        {"role": "system", "content": " ".join(["sys"] * 1391)},
        {"role": "user", "content": " ".join(["doc"] * 15798)},
    ]
    with _LogCapture() as output:
        result = asyncio.run(runner.generate(messages=messages, max_tokens=4096))

    assert result.content == ONTOLOGY_BODY, "the path that works must keep working"
    assert calls, "generation was submitted"
    logged = output.getvalue()
    assert "prompt_tokens=17189" in logged
    assert "LLM_CONTEXT_LENGTH=8192" in logged
    assert "262144" in logged
    assert "WARNING" in logged


# --- (c) an unreadable model config falls back to the operational cap --------

def test_unreadable_model_config_falls_back_to_the_operational_cap(monkeypatch):
    calls: list[dict] = []
    _install_fake_mlx(monkeypatch, calls, model_window=None)
    runner = MLXRunner("fake-mlx", "/tmp/fake-mlx")
    asyncio.run(runner.load())
    monkeypatch.setattr(Config, "LLM_CONTEXT_LENGTH", 16)

    messages = [
        {"role": "system", "content": " ".join(["sys"] * 10)},
        {"role": "user", "content": " ".join(["doc"] * 40)},
    ]
    with _LogCapture() as output:
        with pytest.raises(PromptTooLongError) as excinfo:
            asyncio.run(runner.generate(messages=messages, max_tokens=4096))

    assert "16-token context window" in str(excinfo.value)
    assert "declares no context window" in output.getvalue()
    assert calls == []


# --- (d) a normal prompt neither warns nor raises ---------------------------

def test_normal_prompt_neither_warns_nor_fails(mlx):
    runner, calls = mlx

    with _LogCapture() as output:
        result = asyncio.run(runner.generate(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=4096,
        ))

    assert result.content == ONTOLOGY_BODY
    assert result.finish_reason == "stop"
    assert calls, "a prompt inside the context window must still be generated"
    assert "WARNING" not in output.getvalue()


# --- the ontology path end to end against the fake mlx_vlm ---------------

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


def test_ontology_generation_fails_precisely_on_an_impossible_prompt(monkeypatch):
    """A step-1 prompt that cannot fit the model window at all fails early."""
    calls: list[dict] = []
    _install_fake_mlx(monkeypatch, calls, model_window=64)
    runner = MLXRunner("fake-mlx", "/tmp/fake-mlx")
    asyncio.run(runner.load())
    generator = OntologyGenerator(llm_client=_llm_client_for(runner))

    with pytest.raises(PromptTooLongError) as excinfo:
        generator.generate(
            document_texts=[" ".join(["doc"] * 40)],
            simulation_requirement="Simulate the public discussion.",
        )

    assert "64-token context window" in str(excinfo.value)
    assert calls == [], "an impossible prompt must never reach generation"


# --- (e) the JSON retry's message must be true about the budget it sends ----

class _TruncatingThenValidCompletions:
    """The /v1 hop where the FIRST JSON attempt truncates at the caller's cap.

    ``_RunnerBackedCompletions`` always answers ``stop``; the measured #51 chain
    needs a first attempt at ``finish_reason=length`` with an unusable partial
    body, then a valid one — exactly what the fake returns.
    """

    def __init__(self, runner):
        self.runner = runner
        self.seen = 0

    def create(self, **kwargs):
        self.seen += 1
        if self.seen == 1:
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content='{"entity_types": ['),
                )],
            )
        return _RunnerBackedCompletions(self.runner).create(**kwargs)


def _llm_client_for_truncation(runner) -> LLMClient:
    client = object.__new__(LLMClient)
    client.api_key = "local"
    client.base_url = "http://testserver/v1"
    client.model = "LFM3-1.7B-MLX-4bit"
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_TruncatingThenValidCompletions(runner))
    )
    return client


def test_a_truncation_retry_names_the_budget_it_actually_sends(mlx, caplog, monkeypatch):
    """The retry must not claim a cap it did not drop, or hide a smaller one.

    Measured in #51's probe (``fix51_backend_1794154252.log``): attempt 1 at
    4096 truncated, attempt 2 was logged as "retrying content generation without
    an output token cap" while the runner had just resolved its own
    ``MLX_MAX_TOKENS`` and sent that. The log is what an operator reads during a
    502, so it must state the number the retry really uses — and, when the local
    default is below the budget that just truncated, name the knob to raise.
    Behaviour is unchanged: the retry still sends no caller cap.
    """
    runner, calls = mlx
    monkeypatch.setattr(Config, "MLX_MAX_TOKENS", 2048)   # the library's number
    client = _llm_client_for_truncation(runner)

    with caplog.at_level(logging.WARNING, logger="app.utils.llm_client"):
        parsed = client.chat_json(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=8192,
            max_attempts=2,
        )

    assert parsed["analysis_summary"] == "ok", "the valid second body parses"
    logged = caplog.text
    assert "without an output token cap" not in logged, (
        "the old claim is false: the local runner resolves MLX_MAX_TOKENS and "
        "sends it, so 'without a cap' reads as 'uncapped' when it is not"
    )
    # the number the truncated attempt asked for, and the one the retry uses
    assert "8192" in logged
    assert f"MLX_MAX_TOKENS={Config.MLX_MAX_TOKENS}" in logged
    # and the knob to raise, because 2048 is below the 8192 that just truncated
    assert "MLX_MAX_TOKENS" in logged and "raise" in logged.lower()
    # behaviour unchanged: the retry sends no caller-supplied cap
    assert calls[-1]["kwargs"]["max_tokens"] == Config.MLX_MAX_TOKENS


def test_an_uncapped_json_retry_is_not_asked_for_a_knob(mlx, caplog):
    """A caller that never sent a cap has nothing to compare against."""

    runner, calls = mlx
    client = _llm_client_for_truncation(runner)

    with caplog.at_level(logging.WARNING, logger="app.utils.llm_client"):
        client.chat_json(
            messages=[{"role": "user", "content": "short prompt"}],
            max_tokens=None,
            max_attempts=2,
        )

    logged = caplog.text
    assert "without an output token cap" not in logged
    assert "MLX_MAX_TOKENS" not in logged, (
        "no caller budget was truncated, so there is no knob comparison to make"
    )
    assert calls[-1]["kwargs"]["max_tokens"] == Config.MLX_MAX_TOKENS


# --- review finding 3: the synthesised finish_reason must follow the budget
# this runner actually submitted, not the caller's optionality ---------------

def test_a_synthesised_finish_reason_names_the_submitted_budget(monkeypatch):
    """An uncapped request is NOT an unbounded one any more (#51).

    ``MLXRunner.generate`` resolves ``Config.MLX_MAX_TOKENS`` when the caller
    sends no cap, so ``finish_reason`` synthesis must key off the budget that
    was actually submitted (``effective_max_tokens``). Keying off the caller's
    optionality reported ``stop`` for a request truncated at the resolved 4096 —
    the silent inheritance this ticket exists to expose.
    """
    calls: list[dict] = []

    def _generate(model, processor, prompt, **kwargs):
        calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        # a library answer with no finish_reason at all: the synthesis path
        return SimpleNamespace(text="{}", finish_reason=None,
                               prompt_tokens=2, generation_tokens=1)

    import sys
    import types
    vlm = types.ModuleType("mlx_vlm")
    vlm.load = lambda path: (SimpleNamespace(
        config=SimpleNamespace(max_position_embeddings=262144),
        parameters=dict), SimpleNamespace(tokenizer=_CountingTokenizer()))
    vlm.generate = _generate
    vlm.apply_chat_template = (
        lambda processor, config, messages, add_generation_prompt=True:
        " ".join(str(m.get("content", "")) for m in messages))
    monkeypatch.setitem(sys.modules, "mlx_vlm", vlm)
    core = types.ModuleType("mlx.core")
    core.eval = lambda *a, **k: None
    core.clear_cache = lambda *a, **k: None
    mlx = types.ModuleType("mlx")
    mlx.__path__ = []
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)

    runner = MLXRunner("fake-mlx", "/tmp/fake-mlx")
    asyncio.run(runner.load())

    result = asyncio.run(runner.generate(
        messages=[{"role": "user", "content": "short prompt"}],
        max_tokens=None,
    ))

    submitted = calls[-1]["kwargs"]["max_tokens"]
    assert submitted == Config.MLX_MAX_TOKENS, "the budget travels"
    assert result.finish_reason == "length", (
        f"a request submitted with a {submitted}-token budget that returns no "
        f"finish_reason must not be reported as an unbounded 'stop'"
    )
