# SPDX-License-Identifier: AGPL-3.0-or-later
"""The /v1 proxy must carry OpenAI tool-calling in and out.

Every MiroFish/OASIS client speaks OpenAI Chat Completions to this proxy, and
camel's agent executes ``message.tool_calls`` and nothing else. The proxy used
to read only model/messages/max_tokens/temperature/response_format/stream, so a
tool-calling client's ``tools`` never reached the model and the reply never
carried ``tool_calls``: an agent call was prose the client could not turn into
an action. Measured on the WHU acceptance run -- the simulation's own
``tool_call`` text named ``RESPONSE METHOD`` (a heading out of the system
prompt), because no tool schema was ever sent.

Both directions are pinned here with a stub runner, so no model is loaded: the
request carries ``tools``/``tool_choice`` through to the runner, and the reply
maps a runner result's calls into the OpenAI message shape. The output budget
rule (a tool-calling caller that names no budget gets the agent budget, never
the JSON-extraction one) is pinned on the same seam.
"""

import json

from app import create_app
from app.models.manager import get_model_manager
from app.models.runtime import GenResult, normalize_tool_choice, normalize_tools


TOOLS = [{
    "type": "function",
    "function": {
        "name": "create_post",
        "description": "Create a new post.",
        "parameters": {"type": "object",
                       "properties": {"content": {"type": "string"}},
                       "required": ["content"]},
    },
}]

CALLS = [{
    "type": "function",
    "id": "call_1",
    "function": {"name": "create_post", "arguments": "{\"content\": \"hi\"}"},
}]


def _client(monkeypatch, result):
    seen = {}
    manager = get_model_manager()

    def _generate_sync(**kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr(manager, "slot_for_model", lambda model: "small")
    monkeypatch.setattr(manager, "generate_sync", _generate_sync)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client(), seen


def test_tool_calling_request_reaches_the_runner_and_returns_tool_calls(monkeypatch):
    client, seen = _client(monkeypatch, GenResult(
        content="", finish_reason="tool_calls", prompt_tokens=10,
        completion_tokens=8, model="m", tool_calls=CALLS))

    response = client.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "act"}],
        "tools": TOOLS, "tool_choice": "auto",
    })

    assert response.status_code == 200
    assert seen["tools"] == TOOLS
    assert seen["tool_choice"] == "auto"
    choice = json.loads(response.get_data(as_text=True))["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"] == CALLS


def test_a_plain_request_is_untouched(monkeypatch):
    client, seen = _client(monkeypatch, GenResult(
        content="hello", finish_reason="stop", model="m"))

    response = client.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
    })

    assert response.status_code == 200
    assert seen.get("tools") is None
    assert seen.get("tool_choice") is None
    choice = json.loads(response.get_data(as_text=True))["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"] == {"role": "assistant", "content": "hello"}


def test_streaming_emits_the_tool_calls_chunk(monkeypatch):
    client, _ = _client(monkeypatch, GenResult(
        content="", finish_reason="tool_calls", model="m", tool_calls=CALLS))

    response = client.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "act"}],
        "tools": TOOLS, "stream": True,
    })

    body = b"".join(response.iter_encoded())
    assert b'"tool_calls"' in body
    assert json.loads(body.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])[
        "choices"][0]["delta"] == {"role": "assistant", "content": ""}


def test_a_bad_tool_choice_is_refused_without_a_500(monkeypatch):
    manager = get_model_manager()

    def _generate_sync(**kwargs):
        # What the runner does before any model work: normalize_tool_choice
        # refuses a vocabulary it cannot honour (see the helpers' test below).
        normalize_tool_choice(kwargs.get("tool_choice"))
        return GenResult(content="x", model="m")  # pragma: no cover

    monkeypatch.setattr(manager, "slot_for_model", lambda model: "small")
    monkeypatch.setattr(manager, "generate_sync", _generate_sync)
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "act"}],
        "tools": TOOLS, "tool_choice": "whenever",
    })

    # The refusal is the proxy's own 502 carrying the reason, never an
    # unhandled 500 the operator cannot read.
    assert response.status_code == 502
    assert "tool_choice" in json.loads(
        response.get_data(as_text=True))["error"]["message"]


def test_normalize_helpers():
    assert normalize_tools(None) == []
    assert normalize_tools(TOOLS) == TOOLS
    assert normalize_tools("not a list") == []
    assert normalize_tool_choice("auto") == "auto"
    assert normalize_tool_choice(None) is None
    assert normalize_tool_choice({"type": "function", "function": {
        "name": "create_post"}}) == {"type": "function",
                                    "function": {"name": "create_post"}}
    for bad in ("whenever", {"type": "other"}, {"type": "function"}, 7):
        try:
            normalize_tool_choice(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")