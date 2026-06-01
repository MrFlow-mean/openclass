from __future__ import annotations

from copy import deepcopy

from conftest import verified_headers


def _document_with_text(document: dict, text: str) -> dict:
    next_document = deepcopy(document)
    next_document["content_text"] = text
    next_document["content_html"] = f"<p>{text}</p>"
    next_document["content_json"] = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return next_document


def test_workspace_crud_and_isolation_via_http(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app
    owner_headers = verified_headers(client, sent, email="workspace-owner@example.com")
    other_headers = verified_headers(client, sent, email="workspace-other@example.com")

    workspace = client.get("/api/workspace", headers=owner_headers)
    assert workspace.status_code == 200
    assert workspace.json()["packages"]

    created = client.post(
        "/api/packages",
        json={"title": "HTTP Workspace", "summary": "integration"},
        headers=owner_headers,
    )
    assert created.status_code == 200
    package_id = created.json()["active_package_id"]

    updated = client.post(
        f"/api/packages/{package_id}",
        json={"title": "HTTP Workspace Updated", "summary": "changed"},
        headers=owner_headers,
    )
    assert updated.status_code == 200
    assert any(pkg["title"] == "HTTP Workspace Updated" for pkg in updated.json()["packages"])

    lesson = client.post(
        "/api/lessons/generate",
        json={"topic": "Isolation lesson", "start_blank": True, "target_package_id": package_id},
        headers=owner_headers,
    )
    assert lesson.status_code == 200
    lesson_id = lesson.json()["lessons"][0]["id"]

    opened = client.post(f"/api/packages/{package_id}/open", headers=owner_headers)
    assert opened.status_code == 200
    assert opened.json()["active_package_id"] == package_id

    foreign_delete = client.post(f"/api/lessons/{lesson_id}/delete", headers=other_headers)
    assert foreign_delete.status_code == 404

    foreign_move = client.post(
        f"/api/lessons/{lesson_id}/move",
        headers=other_headers,
        json={"target_package_id": package_id},
    )
    assert foreign_move.status_code == 404

    document = _document_with_text(lesson.json()["lessons"][0]["board_document"], "Owner content")
    save = client.post(
        f"/api/lessons/{lesson_id}/document/save",
        headers=owner_headers,
        json={
            "document": document,
            "label": "Owner save",
            "message": "Saved by owner",
            "metadata": {"kind": "manual_document_save"},
        },
    )
    assert save.status_code == 200

    reorder = client.post(
        "/api/workspace/reorder",
        headers=owner_headers,
        json={"ordered_lesson_ids": [lesson_id], "active_lesson_id": lesson_id},
    )
    assert reorder.status_code == 200

    deleted = client.post(f"/api/lessons/{lesson_id}/delete", headers=owner_headers)
    assert deleted.status_code == 200
    active_package = next(pkg for pkg in deleted.json()["packages"] if pkg["id"] == package_id)
    assert all(lesson_item["id"] != lesson_id for lesson_item in active_package["lessons"])
