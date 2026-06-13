from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import ResourceCopyrightAudit, UserView
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import workspace as workspace_router
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore


OWNER_USER = UserView(
    id="user_owner",
    email="owner@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)
OTHER_USER = UserView(
    id="user_other",
    email="other@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)
ADMIN_USER = UserView(
    id="user_admin",
    email="admin@example.com",
    role="admin",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def copyright_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    upload_dir = tmp_path / "uploads"
    export_dir = tmp_path / "exports"
    current = {"user": OWNER_USER}

    def blocked_audit(resource):
        return ResourceCopyrightAudit(
            status="public_blocked",
            public_distribution="blocked",
            risk_level="high",
            signals=["commercial_publication_match"],
            evidence_urls=["https://example.com/catalog"],
            checked_at="2026-01-01T00:00:00+00:00",
            reason="自动审核禁止公开传播。",
            provider="fake",
            file_hash=f"hash:{resource.name}",
        )

    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(documents_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(documents_router, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(workspace_router, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(workspace_router, "audit_resource_public_distribution", blocked_audit)
    workspace_state.ensure_data_dirs()

    main_module.app.dependency_overrides[auth_router.current_user] = lambda: current["user"]
    try:
        yield TestClient(main_module.app), current
    finally:
        main_module.app.dependency_overrides.clear()


def _upload_resource(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/resources/upload",
        files={"file": (name, b"# Notes\nBody.", "text/markdown")},
    )
    assert response.status_code == 200
    return response.json()["resources"][-1]


def test_user_can_submit_appeal_and_admin_can_approve_single_resource(copyright_client) -> None:
    client, current = copyright_client
    client.post("/api/packages", json={"title": "Resources", "summary": ""})
    first = _upload_resource(client, "first.md")
    second = _upload_resource(client, "second.md")

    appeal_response = client.post(
        f"/api/resources/{first['id']}/copyright-appeals",
        json={"message": "", "evidence_text": "", "evidence_urls": []},
    )

    assert appeal_response.status_code == 200
    appeal = appeal_response.json()
    assert appeal["resource_id"] == first["id"]
    assert appeal["status"] == "open"

    user_admin_response = client.get("/api/admin/copyright-appeals")
    assert user_admin_response.status_code == 403

    current["user"] = ADMIN_USER
    admin_list = client.get("/api/admin/copyright-appeals")
    assert admin_list.status_code == 200
    assert [item["id"] for item in admin_list.json()] == [appeal["id"]]

    approve = client.post(
        f"/api/admin/copyright-appeals/{appeal['id']}/resolve",
        json={"decision": "approved", "resolution_reason": ""},
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    current["user"] = OWNER_USER
    package = client.get("/api/course-package").json()
    resources = {resource["id"]: resource for resource in package["resources"]}
    assert resources[first["id"]]["copyright_audit"]["public_distribution"] == "allowed"
    assert resources[first["id"]]["copyright_audit"]["override_source"] == "admin_appeal"
    assert resources[second["id"]]["copyright_audit"]["public_distribution"] == "blocked"


def test_other_user_cannot_appeal_someone_elses_resource(copyright_client) -> None:
    client, current = copyright_client
    client.post("/api/packages", json={"title": "Resources", "summary": ""})
    resource = _upload_resource(client, "owned.md")

    current["user"] = OTHER_USER
    response = client.post(
        f"/api/resources/{resource['id']}/copyright-appeals",
        json={"message": "I own this", "evidence_text": "", "evidence_urls": []},
    )

    assert response.status_code == 404
