from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.services import workspace_state
from app.services.course_store import SqliteCourseStore


TEST_USER = UserView(
    id="merge_api_user",
    email="merge-api@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def _document_with_text(document: dict, text: str) -> dict:
    result = deepcopy(document)
    result["content_text"] = text
    result["content_html"] = f"<p>{text}</p>"
    result["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return result


def _save(api_client: TestClient, lesson: dict, text: str) -> dict:
    response = api_client.post(
        f"/api/lessons/{lesson['id']}/document/save",
        json={
            "document": _document_with_text(lesson["board_document"], text),
            "label": text,
            "message": text,
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert response.status_code == 200
    return response.json()["lessons"][0]


def _divergent_lesson(api_client: TestClient) -> dict:
    generated = api_client.post(
        "/api/lessons/generate",
        json={"topic": "Merge API", "start_blank": True},
    )
    assert generated.status_code == 200
    lesson = generated.json()["lessons"][0]
    lesson = _save(api_client, lesson, "Base")
    branched = api_client.post(
        f"/api/lessons/{lesson['id']}/branches",
        json={"name": "source"},
    )
    assert branched.status_code == 200
    lesson = branched.json()["lessons"][0]
    lesson = _save(api_client, lesson, "Source change")
    checked_out = api_client.post(
        f"/api/lessons/{lesson['id']}/branches/checkout",
        json={"name": "main"},
    )
    assert checked_out.status_code == 200
    lesson = checked_out.json()["lessons"][0]
    return _save(api_client, lesson, "Target change")


def test_merge_session_api_persists_patch_and_submits_double_parent_commit(
    api_client: TestClient,
) -> None:
    lesson = _divergent_lesson(api_client)
    created = api_client.post(
        f"/api/lessons/{lesson['id']}/merge-sessions",
        json={"source_branch_name": "source", "mode": "manual"},
    )
    assert created.status_code == 200
    session = created.json()
    assert session["conflicts"]

    active = api_client.get(f"/api/lessons/{lesson['id']}/merge-sessions/active")
    assert active.status_code == 200
    assert active.json()["id"] == session["id"]

    conflict = session["conflicts"][0]
    patched = api_client.patch(
        f"/api/lessons/{lesson['id']}/merge-sessions/{session['id']}",
        json={
            "expected_version": session["version"],
            "resolutions": [
                {"conflict_id": conflict["id"], "resolution": "source"}
            ],
        },
    )
    assert patched.status_code == 200
    session = patched.json()
    assert session["status"] == "ready"

    stale_patch = api_client.patch(
        f"/api/lessons/{lesson['id']}/merge-sessions/{session['id']}",
        json={"expected_version": session["version"] - 1, "resolutions": []},
    )
    assert stale_patch.status_code == 409

    submitted = api_client.post(
        f"/api/lessons/{lesson['id']}/merge-sessions/{session['id']}/submit",
        json={"expected_version": session["version"]},
    )
    assert submitted.status_code == 200
    merged_lesson = submitted.json()["lessons"][0]
    merge_commit = merged_lesson["history_graph"]["commits"][-1]
    assert len(merge_commit["parent_ids"]) == 2
    assert merge_commit["metadata"]["history_node_kind"] == "merge"
    assert "source" in merged_lesson["history_graph"]["branches"]

    persisted = api_client.get(
        f"/api/lessons/{lesson['id']}/merge-sessions/{session['id']}"
    )
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "committed"
    assert api_client.get(
        f"/api/lessons/{lesson['id']}/merge-sessions/active"
    ).json() is None
