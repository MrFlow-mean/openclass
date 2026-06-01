from __future__ import annotations

from conftest import token_from_latest_email


def _verified_headers(client, sent, *, email: str, password: str = "correct-password") -> dict[str, str]:
    client.post("/api/auth/register", json={"email": email, "password": password})
    client.get(
        "/api/auth/email/verify",
        params={"token": token_from_latest_email(sent)},
        follow_redirects=False,
    )
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_collaboration_publish_list_and_fork_via_http(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app
    owner_headers = _verified_headers(client, sent, email="owner@example.com")
    learner_headers = _verified_headers(client, sent, email="learner@example.com")

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

    fork = client.post(
        f"/api/open-courses/{publication_id}/fork",
        headers=learner_headers,
    )
    assert fork.status_code == 200
    forked = fork.json()["course_package"]
    assert forked["id"] != package_id
    assert len(forked["lessons"]) >= 1
