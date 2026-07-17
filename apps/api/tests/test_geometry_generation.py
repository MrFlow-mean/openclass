from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.routers import geometry as geometry_router
from app.services import workspace_state
from app.services.ai_execution_adapter import StructuredExecutionResult
from app.services.course_store import SqliteCourseStore
from app.services.geometry_scene import GeometryScene, generate_geometry_scene


TEST_USER = UserView(
    id="user_geometry",
    email="geometry@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


def sample_scene() -> GeometryScene:
    return GeometryScene.model_validate(
        {
            "version": "1.0",
            "title": "Parallel segments",
            "summary": "A representative configuration.",
            "dimension": "2d",
            "show_axes": True,
            "show_grid": True,
            "viewport": {"x_min": -4, "x_max": 4, "y_min": -3, "y_max": 3},
            "points": [
                {"id": "A", "label": "A", "x": -2, "y": 1},
                {"id": "B", "label": "B", "x": 2, "y": 1},
                {"id": "C", "label": "C", "x": -2, "y": -1},
                {"id": "D", "label": "D", "x": 2, "y": -1},
            ],
            "primitives": [
                {"id": "AB", "kind": "segment", "point_ids": ["A", "B"]},
                {"id": "CD", "kind": "segment", "point_ids": ["C", "D"]},
            ],
            "steps": ["AB and CD share the same direction."],
        }
    )


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def create_board(api_client: TestClient, text: str) -> dict[str, object]:
    workspace = api_client.post("/api/packages", json={"title": "Geometry package", "summary": ""}).json()
    package_id = workspace["active_package_id"]
    package = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Geometry page", "target_package_id": package_id, "start_blank": True},
    ).json()
    lesson = package["lessons"][0]
    document = {
        **lesson["board_document"],
        "content_text": text,
        "content_html": f"<p>{text}</p>",
        "content_json": {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        },
    }
    saved = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": document,
            "label": "Geometry source",
            "message": "Saved source excerpt",
            "metadata": {"kind": "manual_document_save"},
        },
    ).json()
    return saved["lessons"][0]


def test_geometry_endpoint_uses_verified_board_selection(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excerpt = "AB is parallel to CD, and the diagonals meet at O."
    lesson = create_board(api_client, excerpt)
    captured: dict[str, object] = {}
    attachment = {
        "source_ingestion_id": "source_geometry_image",
        "name": "question.png",
        "mime_type": "image/png",
        "size_bytes": 68,
        "kind": "image",
        "status": "queued",
    }

    def fake_verify_chat_attachments(**kwargs):
        captured["attachment_verification"] = kwargs
        return kwargs["attachments"]

    def fake_prepare_chat_attachments(**kwargs):
        captured["attachment_preparation"] = kwargs
        return SimpleNamespace(
            prompt_context="Verified geometry attachment",
            image_inputs=["data:image/png;base64,AAAA"],
        )

    def fake_generate_geometry_scene(**kwargs):
        captured.update(kwargs)
        return sample_scene().model_copy(update={"source_excerpt": kwargs["source_excerpt"]})

    monkeypatch.setattr(geometry_router, "generate_geometry_scene", fake_generate_geometry_scene)
    monkeypatch.setattr(geometry_router, "verify_chat_attachments", fake_verify_chat_attachments)
    monkeypatch.setattr(geometry_router, "prepare_chat_attachments", fake_prepare_chat_attachments)
    response = api_client.post(
        f"/api/lessons/{lesson['id']}/geometry/generate",
        json={
            "selection": {
                "kind": "board",
                "lesson_id": lesson["id"],
                "document_id": lesson["board_document"]["id"],
                "excerpt": excerpt,
                "location_kind": "target_range",
            },
            "instructions": "Emphasize the parallel segments.",
            "attachments": [attachment],
            "text_model": {"provider": "openai_codex", "model": "gpt-5.5"},
        },
    )

    assert response.status_code == 200
    assert response.json()["source_excerpt"] == excerpt
    assert captured["source_excerpt"] == excerpt
    assert captured["instructions"] == "Emphasize the parallel segments."
    assert captured["attachment_context"] == "Verified geometry attachment"
    assert captured["image_inputs"] == ["data:image/png;base64,AAAA"]
    verification = captured["attachment_verification"]
    assert verification["owner_user_id"] == TEST_USER.id
    assert verification["package_id"]
    assert verification["attachments"][0].source_ingestion_id == attachment["source_ingestion_id"]


def test_geometry_endpoint_rejects_stale_or_non_board_references(api_client: TestClient) -> None:
    lesson = create_board(api_client, "A current board statement")
    base_payload = {
        "selection": {
            "kind": "board",
            "lesson_id": lesson["id"],
            "document_id": lesson["board_document"]["id"],
            "excerpt": "A stale statement",
            "location_kind": "target_range",
        }
    }
    stale = api_client.post(f"/api/lessons/{lesson['id']}/geometry/generate", json=base_payload)
    assert stale.status_code == 409

    non_board = api_client.post(
        f"/api/lessons/{lesson['id']}/geometry/generate",
        json={"selection": {**base_payload["selection"], "kind": "chat", "excerpt": "A current board statement"}},
    )
    assert non_board.status_code == 422


def test_geometry_endpoint_rejects_unverified_attachment(api_client: TestClient) -> None:
    excerpt = "A current board statement"
    lesson = create_board(api_client, excerpt)
    response = api_client.post(
        f"/api/lessons/{lesson['id']}/geometry/generate",
        json={
            "selection": {
                "kind": "board",
                "lesson_id": lesson["id"],
                "document_id": lesson["board_document"]["id"],
                "excerpt": excerpt,
                "location_kind": "target_range",
            },
            "attachments": [
                {
                    "source_ingestion_id": "source_from_another_course",
                    "name": "untrusted.png",
                    "mime_type": "image/png",
                    "size_bytes": 68,
                    "kind": "image",
                    "status": "queued",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "不属于当前课程" in response.json()["detail"]


def test_geometry_scene_adapter_returns_only_validated_scene_graph() -> None:
    scene = sample_scene()

    class FakeAdapter:
        def parse_structured(self, *, system_prompt: str, user_prompt: str, schema, image_inputs=None):
            assert "Never return HTML" in system_prompt
            assert "backend-verified attachment" in system_prompt
            assert "board_excerpt" in user_prompt
            assert "verified_attachment_context" in user_prompt
            assert schema is GeometryScene
            assert image_inputs == ["data:image/png;base64,AAAA"]
            return StructuredExecutionResult(output_parsed=scene)

    generated = generate_geometry_scene(
        adapter=FakeAdapter(),
        source_excerpt="AB is parallel to CD",
        instructions="Use a compact view",
        attachment_context="Verified attachment text",
        image_inputs=["data:image/png;base64,AAAA"],
    )

    assert generated.source_excerpt == "AB is parallel to CD"
    assert generated.primitives[0].point_ids == ["A", "B"]
