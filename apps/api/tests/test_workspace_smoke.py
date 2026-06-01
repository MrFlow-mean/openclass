from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import LibraryChapter, ResourceLibraryItem, UserView
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import resources as resources_router
from app.services import resource_service
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore


TEST_USER = UserView(
    id="user_smoke",
    email="smoke@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    upload_dir = tmp_path / "uploads"
    export_dir = tmp_path / "exports"

    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(documents_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(resources_router, "UPLOAD_DIR", upload_dir)
    workspace_state.ensure_data_dirs()

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _document_with_text(document: dict, text: str) -> dict:
    next_document = deepcopy(document)
    next_document["content_text"] = text
    next_document["content_html"] = f"<p>{text}</p>"
    next_document["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return next_document


def test_workspace_document_history_and_resource_flow(api_client: TestClient) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Smoke package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Smoke lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    package = generated.json()
    lesson = package["lessons"][0]

    first_document = _document_with_text(lesson["board_document"], "First smoke version")
    first_save = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": first_document,
            "label": "First smoke save",
            "message": "Saved first smoke version",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert first_save.status_code == 200
    first_commit_id = first_save.json()["lessons"][0]["history_graph"]["commits"][-1]["id"]

    second_document = _document_with_text(first_document, "Second smoke version")
    second_save = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": second_document,
            "label": "Second smoke save",
            "message": "Saved second smoke version",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert second_save.status_code == 200
    assert second_save.json()["lessons"][0]["board_document"]["content_text"] == "Second smoke version"

    upload = api_client.post(
        "/api/resources/upload",
        data={"lesson_id": lesson["id"]},
        files={"file": ("notes.txt", b"# Notes\nReusable resource text.", "text/plain")},
    )
    assert upload.status_code == 200
    assert upload.json()["resources"][0]["name"] == "notes.txt"

    search = api_client.get("/api/documents/search", params={"q": "Second smoke", "limit": 5})
    assert search.status_code == 200
    assert search.json()["results"]

    restored = api_client.post(
        f"/api/lessons/{lesson['id']}/restore",
        json={"commit_id": first_commit_id, "label": "Restore first smoke version"},
    )
    assert restored.status_code == 200
    assert restored.json()["lessons"][0]["board_document"]["content_text"] == "First smoke version"


def test_heavy_uploads_defer_inline_text_extraction(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    created_workspace = api_client.post(
        "/api/packages",
        json={"title": "Upload package", "summary": ""},
    )
    assert created_workspace.status_code == 200
    target_package_id = created_workspace.json()["active_package_id"]

    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Upload lesson", "target_package_id": target_package_id, "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]

    defer_flags: list[bool] = []

    def fake_build_resource_item(source_path, original_name, *, external_parse=None, defer_text_extraction=False):
        defer_flags.append(defer_text_extraction)
        return ResourceLibraryItem(
            name=original_name,
            mime_type="image/png" if original_name.endswith(".png") else "application/pdf",
            resource_type="image" if original_name.endswith(".png") else "document",
            size_bytes=source_path.stat().st_size,
            outline=[
                LibraryChapter(
                    title=original_name,
                    summary="Metadata-only test resource.",
                    locator_hint=original_name,
                    order_index=0,
                )
            ],
            extracted_text_available=False,
            source_path=str(source_path),
        )

    monkeypatch.setattr(resource_service, "build_resource_item", fake_build_resource_item)

    large_pdf = api_client.post(
        "/api/resources/upload",
        data={"lesson_id": lesson["id"]},
        files={
            "file": (
                "large.pdf",
                b"x" * (resource_service.INLINE_UPLOAD_EXTRACTION_MAX_BYTES + 1),
                "application/pdf",
            )
        },
    )
    assert large_pdf.status_code == 200

    small_image = api_client.post(
        "/api/resources/upload",
        data={"lesson_id": lesson["id"]},
        files={"file": ("screenshot.png", b"png", "image/png")},
    )
    assert small_image.status_code == 200

    assert defer_flags == [True, True]
