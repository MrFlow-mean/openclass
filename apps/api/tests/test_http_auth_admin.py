from __future__ import annotations

from app.constants import (
    AUTH_ERROR_ADMIN_REQUIRED,
    AUTH_ERROR_PASSWORD_RESET_INVALID,
    AUTH_ERROR_UNAUTHENTICATED,
)
from conftest import token_from_latest_email, verified_headers


def test_auth_me_requires_token(isolated_app) -> None:
    client, _auth, _store, _sent = isolated_app

    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == AUTH_ERROR_UNAUTHENTICATED


def test_password_reset_via_http(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app

    client.post("/api/auth/register", json={"email": "reset@example.com", "password": "old-password"})
    client.get(
        "/api/auth/email/verify",
        params={"token": token_from_latest_email(sent)},
        follow_redirects=False,
    )

    forgot = client.post("/api/auth/password/forgot", json={"email": "reset@example.com"})
    assert forgot.status_code == 200

    reset_token = token_from_latest_email(sent, name="reset_token")
    reset = client.post("/api/auth/password/reset", json={"token": reset_token, "password": "new-password"})
    assert reset.status_code == 200

    old_login = client.post("/api/auth/login", json={"email": "reset@example.com", "password": "old-password"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"email": "reset@example.com", "password": "new-password"})
    assert new_login.status_code == 200

    reuse = client.post("/api/auth/password/reset", json={"token": reset_token, "password": "another-password"})
    assert reuse.status_code == 400
    assert reuse.json()["detail"]["code"] == AUTH_ERROR_PASSWORD_RESET_INVALID


def test_admin_overview_requires_admin_role(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app
    admin_headers = verified_headers(client, sent, email="admin@example.com")
    member_headers = verified_headers(client, sent, email="member@example.com")

    admin_overview = client.get("/api/admin/overview", headers=admin_headers)
    assert admin_overview.status_code == 200
    assert admin_overview.json()["stats"]["users"] >= 2

    forbidden = client.get("/api/admin/overview", headers=member_headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == AUTH_ERROR_ADMIN_REQUIRED


def test_admin_can_disable_user(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app
    admin_headers = verified_headers(client, sent, email="owner-admin@example.com")
    target_headers = verified_headers(client, sent, email="target@example.com")

    target_me = client.get("/api/auth/me", headers=target_headers)
    target_id = target_me.json()["id"]

    patch = client.patch(
        f"/api/admin/users/{target_id}",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "disabled"

    login = client.post("/api/auth/login", json={"email": "target@example.com", "password": "correct-password"})
    assert login.status_code == 403
