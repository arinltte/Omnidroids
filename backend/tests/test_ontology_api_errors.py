import io

from app import create_app
from app.api import graph as graph_api
from app.models.project import ProjectManager, ProjectStatus
from app.utils.llm_client import LLMResponseError


def _post_ontology(client):
    return client.post(
        "/api/graph/ontology/generate",
        data={
            "simulation_requirement": "Simulate the discussion.",
            "files": (io.BytesIO(b"A short source document."), "source.md"),
        },
        content_type="multipart/form-data",
    )


def test_ontology_api_returns_safe_truncation_error_and_failed_project(
    tmp_path,
    monkeypatch,
):
    class FailingGenerator:
        def generate(self, **kwargs):
            raise LLMResponseError(
                "LLM JSON output was truncated at the token limit",
                finish_reason="length",
            )

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)

    app = create_app()
    app.config.update(TESTING=True)
    response = _post_ontology(app.test_client())

    assert response.status_code == 502
    assert response.json["success"] is False
    assert "token limit" in response.json["error"]
    assert "traceback" not in response.json

    project_id = response.json["data"]["project_id"]
    project = ProjectManager.get_project(project_id)
    assert project.status == ProjectStatus.FAILED
    assert project.error == response.json["error"]


def test_ontology_api_does_not_expose_provider_error_body(tmp_path, monkeypatch):
    class ProviderError(RuntimeError):
        status_code = 401
        request_id = "request-safe-id"
        body = {"error": {"message": "SECRET-PROVIDER-BODY"}}

    class FailingGenerator:
        def generate(self, **kwargs):
            raise ProviderError("SECRET-PROVIDER-BODY")

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)

    app = create_app()
    app.config.update(TESTING=True)
    response = _post_ontology(app.test_client())

    assert response.status_code == 502
    assert "HTTP 401" in response.json["error"]
    assert "request-safe-id" in response.json["error"]
    assert "SECRET-PROVIDER-BODY" not in response.get_data(as_text=True)
    assert "traceback" not in response.json


GUARD_MESSAGE = (
    "local model error: prompt of 17189 tokens exceeds the 262144-token "
    "context window of model LFM3-1.7B-MLX-4bit; largest contributor: user "
    "message #2, 15798 tokens. Remedies: shorten the documents or the "
    "simulation requirement, or (operational cap only) raise "
    "LLM_CONTEXT_LENGTH. An oversized prompt is never silently truncated."
)


def test_ontology_api_surfaces_the_context_guard_message(tmp_path, monkeypatch):
    """#51: the guard's reason must reach the operator, not die as a bare 502.

    ``LLMClient`` re-wraps a ``prompt_too_long`` provider rejection as
    ``LLMResponseError`` carrying the guard text verbatim; this endpoint's
    ``LLMResponseError`` branch already answers 502 with ``str(error)`` and marks
    the project FAILED. Without the re-wrap the same failure takes the
    ``isinstance(provider_status, int)`` branch and the operator sees only
    "LLM provider request failed (HTTP 502)".
    """

    class GuardedGenerator:
        def generate(self, **kwargs):
            raise LLMResponseError(GUARD_MESSAGE)

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr(graph_api, "OntologyGenerator", GuardedGenerator)

    app = create_app()
    app.config.update(TESTING=True)
    response = _post_ontology(app.test_client())

    assert response.status_code == 502
    assert response.json["success"] is False
    assert response.json["error"] == GUARD_MESSAGE
    assert "HTTP 502" not in response.json["error"]
    assert "traceback" not in response.get_data(as_text=True)

    project = ProjectManager.get_project(response.json["data"]["project_id"])
    assert project.status == ProjectStatus.FAILED
    assert project.error == GUARD_MESSAGE
