from __future__ import annotations

import pytest

from conftest import token_from_latest_email, verified_headers
from app.constants import AUTH_ERROR_EMAIL_NOT_VERIFIED


def test_auth_register_verify_login_and_me(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app

    register = client.post(
        "/api/auth/register",
        json={"email": "teacher@example.com", "password": "correct-password"},
    )
    assert register.status_code == 200
    assert register.json()["verification_required"] is True

    verify = client.get(
        "/api/auth/email/verify",
        params={"token": token_from_latest_email(sent)},
        follow_redirects=False,
    )
    assert verify.status_code == 303

    login = client.post(
        "/api/auth/login",
        json={"email": "teacher@example.com", "password": "correct-password"},
    )
    assert login.status_code == 200
    token = login.json()["token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "teacher@example.com"
    assert me.json()["role"] == "admin"


def test_auth_guest_session(isolated_app) -> None:
    client, _auth, _store, _sent = isolated_app

    guest = client.post("/api/auth/guest")
    assert guest.status_code == 200
    token = guest.json()["token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "guest"


def test_auth_logout_invalidates_session(isolated_app) -> None:
    client, _auth, _store, sent = isolated_app

    client.post(
        "/api/auth/register",
        json={"email": "logout@example.com", "password": "correct-password"},
    )
    client.get(
        "/api/auth/email/verify",
        params={"token": token_from_latest_email(sent)},
        follow_redirects=False,
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "logout@example.com", "password": "correct-password"},
    )
    token = login.json()["token"]

    logout = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 200

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


def test_auth_login_requires_verified_email(isolated_app) -> None:
    client, _auth, _store, _sent = isolated_app

    register = client.post(
        "/api/auth/register",
        json={"email": "pending@example.com", "password": "correct-password"},
    )
    assert register.status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"email": "pending@example.com", "password": "correct-password"},
    )
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == AUTH_ERROR_EMAIL_NOT_VERIFIED
