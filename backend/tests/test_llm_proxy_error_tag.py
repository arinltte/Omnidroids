# SPDX-License-Identifier: AGPL-3.0-or-later
"""The /v1 proxy must TAG the context-guard rejection, not just report it.

``LLMClient`` re-wraps a tagged ``prompt_too_long`` rejection as
``LLMResponseError`` so the guard's precise reason reaches the operator
(#51, ``app/api/graph.py``'s ``LLMResponseError`` branch). An untagged 502 takes
that API's ``isinstance(provider_status, int)`` branch instead and collapses to
"LLM provider request failed (HTTP 502)" -- the bare-502 outcome the ticket
forbids. Both shapes are pinned here, and so is the no-regression case: every
other upstream failure keeps the historical ``upstream_unavailable`` tag.
"""

import json

from app import create_app
from app.models.manager import get_model_manager
from app.models.runtime import PromptTooLongError


def _post(client):
    return client.post(
        "/v1/chat/completions",
        json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
    )


def _client_failing_with(monkeypatch, error):
    manager = get_model_manager()
    monkeypatch.setattr(manager, "slot_for_model", lambda model: "main")
    monkeypatch.setattr(
        manager, "generate_sync", lambda **kwargs: (_ for _ in ()).throw(error)
    )

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_proxy_tags_the_context_guard_rejection(monkeypatch):
    client = _client_failing_with(monkeypatch, PromptTooLongError("prompt of 42 tokens"))

    response = _post(client)

    assert response.status_code == 502
    payload = json.loads(response.get_data(as_text=True))
    assert payload["error"]["type"] == "prompt_too_long"
    assert "prompt of 42 tokens" in payload["error"]["message"]
    assert payload["error"]["message"].startswith("local model error: ")


def test_proxy_keeps_the_historical_tag_for_other_failures(monkeypatch):
    client = _client_failing_with(monkeypatch, RuntimeError("upstream exploded"))

    response = _post(client)

    assert response.status_code == 502
    payload = json.loads(response.get_data(as_text=True))
    assert payload["error"]["type"] == "upstream_unavailable"
    assert "upstream exploded" in payload["error"]["message"]
