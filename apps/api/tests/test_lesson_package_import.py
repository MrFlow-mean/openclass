from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router, documents as documents_router, workspace as workspace_router
from app.services import lesson_package_export, lesson_package_import, workspace_state
from app.services.board_asset_store import BoardAssetStore
from app.services.course_store import SqliteCourseStore


TEST_USER = UserView(
    id="ridoc_import_user",
    email="ridoc-import@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


class _EmptyEvidenceStore:
    def get_bundle(self, *, owner_user_id: str, bundle_id: str):
        return None


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    database_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(database_path, legacy_json_path=None)
    assets = BoardAssetStore(database_path, tmp_path / "board-assets")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(lesson_package_export, "source_evidence_store", _EmptyEvidenceStore())
    monkeypatch.setattr(lesson_package_export, "get_board_asset_store", lambda: assets)
    monkeypatch.setattr(lesson_package_import, "get_board_asset_store", lambda: assets)
    monkeypatch.setattr(documents_router, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(workspace_router, "UPLOAD_DIR", tmp_path / "uploads")
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _export_lesson(api_client: TestClient) -> tuple[dict, bytes]:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Shared lesson", "start_blank": True},
    )
    assert generated.status_code == 200
    original = generated.json()["lessons"][0]
    branched = api_client.post(
        f"/api/lessons/{original['id']}/branches",
        json={"name": "alternate"},
    )
    assert branched.status_code == 200
    exported = api_client.get(f"/api/lessons/{original['id']}/document/export-ridoc")
    assert exported.status_code == 200
    return original, exported.content


def test_import_ridoc_creates_standalone_lesson_and_can_continue(api_client: TestClient) -> None:
    original, content = _export_lesson(api_client)
    workspace_before = api_client.get("/api/workspace").json()
    standalone_before = workspace_before["packages"][0]

    imported = api_client.post(
        "/api/workspace/import-ridoc",
        files={"file": ("shared.ridoc", content, "application/vnd.openclass.ridoc+zip")},
    )

    assert imported.status_code == 200
    package = imported.json()
    lesson = next(item for item in package["lessons"] if item["id"] != original["id"])
    workspace_after = api_client.get("/api/workspace").json()
    assert len(workspace_after["packages"]) == len(workspace_before["packages"])
    assert package["id"] == standalone_before["id"]
    assert package["is_standalone"] is True
    assert len(package["lessons"]) == len(standalone_before["lessons"]) + 1
    assert package["active_lesson_id"] == lesson["id"]
    assert lesson["id"] != original["id"]
    assert set(lesson["history_graph"]["branches"]) == {"main", "alternate"}
    original_commit_ids = {commit["id"] for commit in original["history_graph"]["commits"]}
    imported_commit_ids = {commit["id"] for commit in lesson["history_graph"]["commits"]}
    assert original_commit_ids.isdisjoint(imported_commit_ids)
    assert all(commit["metadata"]["ridoc_imported"] is True for commit in lesson["history_graph"]["commits"])

    document = dict(lesson["board_document"])
    document["content_text"] = "Continued after import"
    document["content_html"] = "<p>Continued after import</p>"
    document["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Continued after import"}]}],
    }
    continued = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={"document": document, "label": "Continue", "message": "Continue", "metadata": {}},
    )
    assert continued.status_code == 200
    continued_lesson = next(item for item in continued.json()["lessons"] if item["id"] == lesson["id"])
    assert len(continued_lesson["history_graph"]["commits"]) == len(imported_commit_ids) + 1
    assert imported_commit_ids.issubset({commit["id"] for commit in continued_lesson["history_graph"]["commits"]})


def test_import_ridoc_rejects_corrupt_file_without_creating_course(api_client: TestClient) -> None:
    before = api_client.get("/api/workspace").json()
    response = api_client.post(
        "/api/workspace/import-ridoc",
        files={"file": ("broken.ridoc", b"not a zip", "application/vnd.openclass.ridoc+zip")},
    )

    assert response.status_code == 400
    after = api_client.get("/api/workspace").json()
    assert len(after["packages"]) == len(before["packages"])
