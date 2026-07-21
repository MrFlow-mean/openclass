from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router, documents as documents_router
from app.services import lesson_package_export, workspace_state
from app.services.course_store import SqliteCourseStore
from app.services.lesson_package_format import read_ridoc


TEST_USER = UserView(
    id="ridoc_export_user",
    email="ridoc-export@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


class _EmptyEvidenceStore:
    def get_bundle(self, *, owner_user_id: str, bundle_id: str):
        return None


class _EmptyAssetStore:
    def references_for_lesson(self, *, owner_user_id: str, lesson_id: str):
        return []


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(lesson_package_export, "source_evidence_store", _EmptyEvidenceStore())
    monkeypatch.setattr(lesson_package_export, "get_board_asset_store", lambda: _EmptyAssetStore())
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(documents_router, "EXPORT_DIR", tmp_path / "exports")
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def test_export_ridoc_returns_valid_full_lesson_history(api_client: TestClient, tmp_path: Path) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Portable lesson", "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]
    branched = api_client.post(
        f"/api/lessons/{lesson['id']}/branches",
        json={"name": "alternate"},
    )
    assert branched.status_code == 200

    response = api_client.get(f"/api/lessons/{lesson['id']}/document/export-ridoc")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openclass.ridoc+zip"
    assert "portable-lesson.ridoc" in response.headers["content-disposition"].lower()
    target = tmp_path / "download.ridoc"
    target.write_bytes(response.content)
    archive = read_ridoc(target)
    assert archive.manifest["lesson"]["title"] == "Portable lesson"
    assert set(archive.graph["branches"]) == {"main", "alternate"}
    assert archive.events[-1]["seq"] == len(archive.events)


def test_export_ridoc_rejects_unknown_source_mode(api_client: TestClient) -> None:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Portable lesson", "start_blank": True},
    )
    lesson = generated.json()["lessons"][0]

    response = api_client.get(
        f"/api/lessons/{lesson['id']}/document/export-ridoc?source_mode=full"
    )

    assert response.status_code == 422
