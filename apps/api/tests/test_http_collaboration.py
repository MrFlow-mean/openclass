from __future__ import annotations

from copy import deepcopy

from app.constants import COMMIT_KIND_MANUAL_DOCUMENT_SAVE, CONTRIBUTION_STATUS_MERGED
from conftest import verified_headers


def test_collaboration_publish_list_and_fork_via_http(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app
    owner_headers = verified_headers(client, sent, email="owner@example.com")
    learner_headers = verified_headers(client, sent, email="learner@example.com")

    package = client.post(
        "/api/packages",
        json={"title": "Collaboration HTTP", "summary": "integration"},
        headers=owner_headers,
    )
    assert package.status_code == 200
    package_id = package.json()["active_package_id"]

    lesson = client.post(
        "/api/lessons/generate",
        json={"topic": "Collaboration lesson", "start_blank": True, "target_package_id": package_id},
        headers=owner_headers,
    )
    assert lesson.status_code == 200

    publish = client.post(
        f"/api/packages/{package_id}/publish",
        headers=owner_headers,
        json={"summary": "Published via HTTP"},
    )
    assert publish.status_code == 200
    publication_id = publish.json()["course"]["id"]

    listing = client.get("/api/open-courses")
    assert listing.status_code == 200
    courses = listing.json()["courses"]
    assert any(course["id"] == publication_id for course in courses)

    detail = client.get(f"/api/open-courses/{publication_id}", headers=learner_headers)
    assert detail.status_code == 200
    assert detail.json()["course"]["id"] == publication_id

    fork = client.post(
        f"/api/open-courses/{publication_id}/fork",
        headers=learner_headers,
    )
    assert fork.status_code == 200
    forked = fork.json()["course_package"]
    fork_meta = fork.json()["fork"]
    assert forked["id"] != package_id
    assert len(forked["lessons"]) >= 1

    foreign_publish = client.post(
        f"/api/packages/{package_id}/publish",
        headers=learner_headers,
        json={"summary": "Should fail"},
    )
    assert foreign_publish.status_code in {403, 404}

    fork_lesson = forked["lessons"][0]
    document = deepcopy(fork_lesson["board_document"])
    document["content_text"] = "Contributor improvement"
    document["content_html"] = "<p>Contributor improvement</p>"

    save = client.post(
        f"/api/lessons/{fork_lesson['id']}/document/save",
        headers=learner_headers,
        json={
            "document": document,
            "label": "Contributor save",
            "message": "Improved lesson body",
            "metadata": {"kind": COMMIT_KIND_MANUAL_DOCUMENT_SAVE},
        },
    )
    assert save.status_code == 200

    contribution = client.post(
        f"/api/forks/{fork_meta['id']}/contributions",
        headers=learner_headers,
        json={"title": "Improve lesson", "description": "HTTP integration contribution"},
    )
    assert contribution.status_code == 200
    contribution_id = contribution.json()["id"]

    review = client.post(
        f"/api/contributions/{contribution_id}/review",
        headers=owner_headers,
        json={"action": "merge", "message": "accept"},
    )
    assert review.status_code == 200
    assert review.json()["status"] == CONTRIBUTION_STATUS_MERGED
