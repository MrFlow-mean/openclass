from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.routers import auth as auth_router
from app.routers import collaboration as collaboration_router
from app.routers import documents as documents_router
from app.routers import resources as resources_router
from app.services import auth_service as auth_service_module
from app.services import collaboration as collaboration_module
from app.services import workspace_state
from app.services.auth_service import AuthService
from app.services.collaboration import CourseCollaborationService
from app.services.course_store import SqliteCourseStore


def token_from_latest_email(sent: list[dict[str, str]], *, name: str = "token") -> str:
    match = re.search(rf"{name}=([A-Za-z0-9_.~%-]+)", sent[-1]["text_body"])
    assert match
    return match.group(1)


def verified_headers(
    client: TestClient,
    sent: list[dict[str, str]],
    *,
    email: str,
    password: str = "correct-password",
) -> dict[str, str]:
    client.post("/api/auth/register", json={"email": email, "password": password})
    client.get(
        "/api/auth/email/verify",
        params={"token": token_from_latest_email(sent)},
        follow_redirects=False,
    )
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_app(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, AuthService, SqliteCourseStore, list[dict[str, str]]]]:
    sent: list[dict[str, str]] = []
    monkeypatch.setenv("OPENCLASS_EMAIL_DELIVERY", "log")
    monkeypatch.setattr(
        auth_service_module,
        "send_transactional_email",
        lambda **kwargs: sent.append(kwargs),
    )

    db_path = tmp_path / "openclass.sqlite3"
    upload_dir = tmp_path / "uploads"
    export_dir = tmp_path / "exports"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    collaboration = CourseCollaborationService(db_path, upload_dir)

    monkeypatch.setattr(auth_router, "auth_service", auth)
    monkeypatch.setattr(collaboration_router, "collaboration_service", collaboration)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "DATABASE_PATH", db_path)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(documents_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(resources_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(collaboration_module, "load_workspace_for_user", store.load_for_user)
    monkeypatch.setattr(collaboration_module, "save_workspace_for_user", store.save_for_user)
    workspace_state.ensure_data_dirs()

    main_module.app.dependency_overrides.clear()
    client = TestClient(main_module.app)
    try:
        yield client, auth, store, sent
    finally:
        main_module.app.dependency_overrides.clear()
