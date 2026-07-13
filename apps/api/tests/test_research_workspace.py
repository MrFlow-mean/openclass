from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.research_models import ResearchArtifactCreate
from app.routers import auth as auth_router
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore
from app.services.research_ai import ResearchAIError
from app.services.research_store import research_store
from app.services.research_workspace import research_workspace_service


TEST_USER = UserView(
    id="research_user",
    email="research@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


class _FakeResearchAI:
    def generate_text(self, *, instruction: str, context: str, conversation: str = "", text_model=None) -> str:
        return f"generated::{instruction}::{context}::{conversation}".strip(":")

    def synthesize_podcast(self, **_kwargs):
        raise AssertionError("audio synthesis should be disabled in this test")

    def podcast_audio_available(self) -> bool:
        return False


class _FailOnceResearchAI(_FakeResearchAI):
    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, *, instruction: str, context: str, conversation: str = "", text_model=None) -> str:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary generation failure")
        return super().generate_text(
            instruction=instruction,
            context=context,
            conversation=conversation,
            text_model=text_model,
        )


class _PodcastSynthesisFailureAI(_FakeResearchAI):
    def generate_text(self, **_kwargs) -> str:
        return "Host: cited opening\nReviewer: cited response"

    def synthesize_podcast(self, **_kwargs):
        raise RuntimeError("temporary speech failure")


class _FailingResearchAI(_FakeResearchAI):
    def generate_text(self, *, instruction: str, context: str, conversation: str = "", text_model=None) -> str:
        raise ResearchAIError("generation unavailable")


@pytest.fixture
def research_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(research_workspace_service, "ai", _FakeResearchAI())
    workspace_state.ensure_data_dirs()
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _package_id(client: TestClient) -> str:
    created = client.post("/api/packages", json={"title": "Research package", "summary": ""})
    assert created.status_code == 200
    return created.json()["active_package_id"]


def test_note_crud_and_search_are_package_scoped(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    created = research_client.post(
        f"/api/packages/{package_id}/research/notes",
        json={"title": "Indexing note", "content": "stable chunks preserve source identity", "tags": ["index"]},
    )
    assert created.status_code == 200
    note_id = created.json()["id"]

    listed = research_client.get(f"/api/packages/{package_id}/research/notes")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [note_id]

    searched = research_client.post(
        f"/api/packages/{package_id}/research/search",
        json={"query": "stable chunks", "mode": "hybrid", "include_notes": True},
    )
    assert searched.status_code == 200
    assert any(item["kind"] == "note" and item["note"]["id"] == note_id for item in searched.json()["results"])

    updated = research_client.patch(
        f"/api/packages/{package_id}/research/notes/{note_id}",
        json={"content": "updated source evidence"},
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "updated source evidence"

    removed = research_client.delete(f"/api/packages/{package_id}/research/notes/{note_id}")
    assert removed.status_code == 200
    assert research_client.get(f"/api/packages/{package_id}/research/notes").json() == []


def test_named_research_thread_persists_messages(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    thread = research_client.post(
        f"/api/packages/{package_id}/research/threads",
        json={"title": "", "context_mode": "off"},
    )
    assert thread.status_code == 200
    thread_id = thread.json()["id"]

    response = research_client.post(
        f"/api/packages/{package_id}/research/threads/{thread_id}/messages",
        json={"message": "Compare the evidence"},
    )
    assert response.status_code == 200
    assert response.json()["message"]["content"].startswith("generated::Compare the evidence")
    assert response.json()["thread"]["title"] == "Compare the evidence"

    messages = research_client.get(
        f"/api/packages/{package_id}/research/threads/{thread_id}/messages"
    )
    assert messages.status_code == 200
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]


def test_generic_artifact_generation_persists_provenance(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    artifact = research_client.post(
        f"/api/packages/{package_id}/research/artifacts",
        json={
            "kind": "custom",
            "title": "Evidence map",
            "instructions": "organize claims and citations",
            "synthesize_audio": False,
        },
    )
    assert artifact.status_code == 202
    payload = artifact.json()
    assert payload["status"] == "queued"
    assert payload["title"] == "Evidence map"
    assert payload["metadata"]["instructions"] == "organize claims and citations"
    assert payload["metadata"]["artifact_request"]["kind"] == "custom"

    completed = research_client.get(
        f"/api/packages/{package_id}/research/artifacts/{payload['id']}"
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "ready"
    assert completed.json()["metadata"]["attempt_count"] == 1

    listed = research_client.get(f"/api/packages/{package_id}/research/artifacts")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [payload["id"]]


def test_capabilities_report_native_workspace_features(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    capabilities = research_client.get(f"/api/packages/{package_id}/research/capabilities")
    assert capabilities.status_code == 200
    payload = capabilities.json()
    assert payload["native_ingestion"] is True
    assert payload["persisted_chat"] is True
    assert payload["podcast_audio"] is False


def test_transformation_and_podcast_profiles_are_user_defined(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    transformation = research_client.post(
        f"/api/packages/{package_id}/research/transformations",
        json={
            "name": "Claims map",
            "instructions": "Group each supported claim with its citations.",
            "output_kind": "custom",
        },
    )
    assert transformation.status_code == 200
    transformation_id = transformation.json()["id"]
    run = research_client.post(
        f"/api/packages/{package_id}/research/transformations/{transformation_id}/run",
        json={"title": "Mapped claims"},
    )
    assert run.status_code == 202
    assert run.json()["title"] == "Mapped claims"

    speakers = research_client.post(
        f"/api/packages/{package_id}/research/speaker-profiles",
        json={"name": "Two voices", "speakers": [{"name": "Host"}, {"name": "Reviewer"}]},
    )
    assert speakers.status_code == 200
    assert len(speakers.json()["speakers"]) == 2

    episode = research_client.post(
        f"/api/packages/{package_id}/research/episode-profiles",
        json={"name": "Detailed discussion", "segment_count": 8, "instructions": "Follow the evidence order."},
    )
    assert episode.status_code == 200
    assert episode.json()["segment_count"] == 8


def test_multi_query_ask_returns_model_planned_queries(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    research_client.post(
        f"/api/packages/{package_id}/research/notes",
        json={"title": "Private note", "content": "note-content-must-not-be-used"},
    )
    response = research_client.post(
        f"/api/packages/{package_id}/research/ask",
        json={"question": "How are citations preserved?", "max_queries": 3, "include_notes": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["question"] == "How are citations preserved?"
    assert payload["search_queries"]
    assert payload["answer"].startswith("generated::How are citations preserved?")
    assert "note-content-must-not-be-used" not in payload["answer"]


def test_failed_artifact_can_retry_with_the_same_persisted_request(
    research_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_id = _package_id(research_client)
    ai = _FailOnceResearchAI()
    monkeypatch.setattr(research_workspace_service, "ai", ai)

    queued = research_client.post(
        f"/api/packages/{package_id}/research/artifacts",
        json={
            "kind": "custom",
            "title": "Retryable output",
            "instructions": "preserve this request",
            "synthesize_audio": False,
        },
    )
    assert queued.status_code == 202
    artifact_id = queued.json()["id"]
    failed = research_client.get(f"/api/packages/{package_id}/research/artifacts/{artifact_id}")
    assert failed.json()["status"] == "failed"
    assert failed.json()["error"] == "temporary generation failure"

    retried = research_client.post(
        f"/api/packages/{package_id}/research/artifacts/{artifact_id}/retry"
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    completed = research_client.get(f"/api/packages/{package_id}/research/artifacts/{artifact_id}")
    assert completed.json()["status"] == "ready"
    assert completed.json()["metadata"]["attempt_count"] == 2
    assert "preserve this request" in completed.json()["content"]

    invalid_retry = research_client.post(
        f"/api/packages/{package_id}/research/artifacts/{artifact_id}/retry"
    )
    assert invalid_retry.status_code == 409


def test_podcast_keeps_generated_script_when_audio_synthesis_fails(
    research_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_id = _package_id(research_client)
    monkeypatch.setattr(research_workspace_service, "ai", _PodcastSynthesisFailureAI())

    queued = research_client.post(
        f"/api/packages/{package_id}/research/artifacts",
        json={
            "kind": "podcast",
            "title": "Evidence discussion",
            "speakers": [{"name": "Host"}, {"name": "Reviewer"}],
            "synthesize_audio": True,
        },
    )
    assert queued.status_code == 202
    artifact_id = queued.json()["id"]
    failed = research_client.get(f"/api/packages/{package_id}/research/artifacts/{artifact_id}")
    assert failed.json()["status"] == "failed"
    assert failed.json()["transcript"].startswith("Host: cited opening")
    assert failed.json()["content"] == failed.json()["transcript"]


def test_status_query_resumes_a_persisted_queued_artifact(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    artifact = research_workspace_service.queue_artifact(
        owner_user_id=TEST_USER.id,
        package_id=package_id,
        request=ResearchArtifactCreate(
            kind="summary",
            title="Persisted task",
            synthesize_audio=False,
        ),
    )

    queued = research_client.get(f"/api/packages/{package_id}/research/artifacts/{artifact.id}")
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    completed = research_client.get(f"/api/packages/{package_id}/research/artifacts/{artifact.id}")
    assert completed.json()["status"] == "ready"


def test_run_on_import_transformation_persists_ready_artifact(research_client: TestClient) -> None:
    package_id = _package_id(research_client)
    transformation = research_client.post(
        f"/api/packages/{package_id}/research/transformations",
        json={
            "name": "Evidence organization",
            "instructions": "Organize the imported evidence according to its structure.",
            "output_kind": "custom",
            "run_on_import": True,
        },
    )
    assert transformation.status_code == 200
    manual_only = research_client.post(
        f"/api/packages/{package_id}/research/transformations",
        json={
            "name": "Manual only",
            "instructions": "Run only when explicitly requested.",
            "output_kind": "custom",
            "run_on_import": False,
        },
    )
    assert manual_only.status_code == 200

    imported = research_client.post(
        f"/api/packages/{package_id}/sources",
        data={"text": "# Section\n\nA source with stable identity."},
    )

    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["metadata"]["import_transformation_status"] == "ready"
    artifacts = research_client.get(f"/api/packages/{package_id}/research/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["status"] == "ready"
    assert artifacts[0]["source_ingestion_ids"] == [source["id"]]
    assert artifacts[0]["metadata"]["transformation_id"] == transformation.json()["id"]
    assert artifacts[0]["metadata"]["trigger"] == "source_import"
    assert source["metadata"]["import_transformation_artifact_ids"] == [artifacts[0]["id"]]


def test_run_on_import_failure_keeps_index_ready_and_persists_failed_artifact(
    research_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_id = _package_id(research_client)
    transformation = research_client.post(
        f"/api/packages/{package_id}/research/transformations",
        json={
            "name": "Imported source transformation",
            "instructions": "Transform only the imported source.",
            "output_kind": "custom",
            "run_on_import": True,
        },
    )
    assert transformation.status_code == 200
    monkeypatch.setattr(research_workspace_service, "ai", _FailingResearchAI())

    imported = research_client.post(
        f"/api/packages/{package_id}/sources",
        data={"text": "# Source\n\nThe imported body remains indexed when automation fails."},
    )

    assert imported.status_code == 200
    source = imported.json()
    assert source["status"] == "ready"
    assert source["metadata"]["import_transformation_status"] == "failed"
    assert "generation unavailable" in source["error"]
    artifacts = research_client.get(f"/api/packages/{package_id}/research/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["status"] == "failed"
    assert artifacts[0]["source_ingestion_ids"] == [source["id"]]
    assert artifacts[0]["metadata"]["transformation_id"] == transformation.json()["id"]
    assert artifacts[0]["metadata"]["trigger"] == "source_import"
    assert "generation unavailable" in artifacts[0]["error"]
    jobs = research_client.get(f"/api/packages/{package_id}/sources/jobs").json()
    assert jobs[0]["status"] == "ready"
    assert "generation unavailable" in jobs[0]["error"]
    assert "transforming" in jobs[0]["phase_history"]
