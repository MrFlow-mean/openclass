import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services import auth_service as auth_service_module
from app.services.auth_service import AuthService, OAuthProfile
from app.services.course_store import SqliteCourseStore


@pytest.fixture(autouse=True)
def email_delivery(monkeypatch):
    sent: list[dict[str, str]] = []
    monkeypatch.setenv("OPENCLASS_EMAIL_DELIVERY", "log")
    monkeypatch.setattr(
        auth_service_module,
        "send_transactional_email",
        lambda **kwargs: sent.append(kwargs),
    )
    return sent


def auth_for_path(db_path):
    SqliteCourseStore(db_path, legacy_json_path=None)
    return AuthService(db_path)


def token_from_latest_email(sent: list[dict[str, str]], name: str = "token") -> str:
    assert sent
    match = re.search(rf"{name}=([A-Za-z0-9_.~%-]+)", sent[-1]["text_body"])
    assert match
    return match.group(1)


def detail_code(exc: HTTPException) -> str:
    assert isinstance(exc.detail, dict)
    return str(exc.detail["code"])


def test_register_requires_email_verification_before_login(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    result = auth.register("Teacher@Example.com", "correct-password")

    assert result.email == "teacher@example.com"
    assert result.verification_required is True
    with pytest.raises(HTTPException) as exc_info:
        auth.login("teacher@example.com", "correct-password")
    assert detail_code(exc_info.value) == "email_not_verified"

    verify_token = token_from_latest_email(email_delivery)
    session_token, verified_user, _, _ = auth.verify_email(verify_token)

    assert verified_user.email == "teacher@example.com"
    assert verified_user.email_verified_at is not None
    assert verified_user.role == "admin"
    assert auth.get_user_by_token(session_token).id == verified_user.id

    login_token, logged_in = auth.login("teacher@example.com", "correct-password")

    assert logged_in.id == verified_user.id
    assert auth.get_user_by_token(login_token).last_login_at is not None


def test_email_verification_token_is_one_time(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("student@example.com", "correct-password")
    verify_token = token_from_latest_email(email_delivery)

    auth.verify_email(verify_token)
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_email(verify_token)

    assert detail_code(exc_info.value) == "email_verification_invalid"


def test_register_rejects_duplicate_email(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("student@example.com", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.register("student@example.com", "correct-password")

    assert exc_info.value.status_code == 409
    assert detail_code(exc_info.value) == "email_already_registered"


def test_guest_session_can_use_workspace_without_creating_user(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    guest_token, guest_user = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest_user.id)
    guest_workspace.packages[0].title = "游客临时课程包"
    store.save_for_user(guest_user.id, guest_workspace)

    reloaded_guest = auth.get_user_by_token(guest_token)

    assert reloaded_guest.role == "guest"
    assert store.load_for_user(guest_user.id).packages[0].title == "游客临时课程包"


def test_email_verification_claims_guest_workspace(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    guest_token, guest_user = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest_user.id)
    guest_workspace.packages[0].title = "登录前学习记录"
    store.save_for_user(guest_user.id, guest_workspace)

    auth.register("student@example.com", "correct-password", guest_token=guest_token)
    _, user, _, _ = auth.verify_email(token_from_latest_email(email_delivery))

    assert store.load_for_user(user.id).packages[0].title == "登录前学习记录"
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_by_token(guest_token)
    assert exc_info.value.status_code == 401


def test_login_rejects_wrong_password(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("student@example.com", "correct-password")
    auth.verify_email(token_from_latest_email(email_delivery))

    with pytest.raises(HTTPException) as exc_info:
        auth.login("student@example.com", "wrong-password")

    assert exc_info.value.status_code == 401
    assert detail_code(exc_info.value) == "invalid_credentials"


def test_password_reset_token_is_one_time(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("student@example.com", "correct-password")
    auth.verify_email(token_from_latest_email(email_delivery))
    auth.request_password_reset("student@example.com")
    reset_token = token_from_latest_email(email_delivery, "reset_token")

    auth.reset_password(reset_token, "new-password")
    with pytest.raises(HTTPException) as exc_info:
        auth.login("student@example.com", "correct-password")
    assert detail_code(exc_info.value) == "invalid_credentials"

    login_token, user = auth.login("student@example.com", "new-password")
    assert auth.get_user_by_token(login_token).id == user.id
    with pytest.raises(HTTPException) as exc_info:
        auth.reset_password(reset_token, "another-password")
    assert detail_code(exc_info.value) == "password_reset_invalid"


def test_provider_list_is_public_login_set(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    provider_ids = [provider.id for provider in auth.providers()]

    assert provider_ids == ["email", "google", "wechat", "github"]


def test_provider_configuration_reflects_env(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    monkeypatch.setenv("OPENCLASS_OAUTH_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("OPENCLASS_OAUTH_GOOGLE_CLIENT_SECRET", "google-secret")

    providers = {provider.id: provider for provider in auth.providers()}

    assert providers["google"].configured is True
    assert providers["wechat"].configured is False


def test_x_oauth_authorization_url_still_uses_pkce_when_enabled(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)
    monkeypatch.setenv("OPENCLASS_OAUTH_X_CLIENT_ID", "x-client")
    monkeypatch.setenv("OPENCLASS_OAUTH_X_CLIENT_SECRET", "x-secret")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/oauth/x/start",
            "headers": [
                (b"host", b"example.com"),
                (b"x-forwarded-proto", b"https"),
            ],
            "query_string": b"",
            "server": ("example.com", 443),
            "scheme": "https",
        }
    )

    target = auth.oauth_authorization_url("x", "/studio", request)

    assert target.startswith("https://x.com/i/oauth2/authorize?")
    assert "code_challenge=" in target
    assert "code_challenge_method=S256" in target
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fapi%2Fauth%2Foauth%2Fx%2Fcallback" in target


def test_oauth_login_links_existing_verified_email(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("student@example.com", "correct-password")
    _, email_user, _, _ = auth.verify_email(token_from_latest_email(email_delivery))
    google_token, google_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-1",
            email="student@example.com",
            display_name="Student From Google",
            avatar_url="https://example.com/avatar.png",
            email_verified=True,
        )
    )
    _, second_google_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-1",
            email="student@example.com",
            display_name="Student From Google",
            avatar_url="https://example.com/avatar.png",
            email_verified=True,
        )
    )

    assert google_user.id == email_user.id
    assert second_google_user.id == email_user.id
    assert auth.get_user_by_token(google_token).id == email_user.id
    assert {identity.provider for identity in google_user.auth_identities} == {"email", "google"}


def test_oauth_unverified_email_does_not_merge_existing_account(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("student@example.com", "correct-password")
    _, email_user, _, _ = auth.verify_email(token_from_latest_email(email_delivery))

    _, oauth_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-2",
            email="student@example.com",
            display_name="Unverified Google",
            email_verified=False,
        )
    )

    assert oauth_user.id != email_user.id
    assert oauth_user.email.endswith("@oauth.openclass.local")


def test_oauth_login_without_email_gets_stable_synthetic_email(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    _, user = auth.login_with_oauth(
        OAuthProfile(
            provider="github",
            subject="12345",
            email=None,
            display_name="octocat",
            avatar_url=None,
        )
    )

    assert user.email == "github-12345@oauth.openclass.local"
    assert user.auth_identities[0].provider == "github"


def test_disabled_user_session_is_rejected_and_audited(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("admin@example.com", "correct-password")
    admin_token, admin_user, _, _ = auth.verify_email(token_from_latest_email(email_delivery))
    auth.register("student@example.com", "correct-password")
    student_token, student_user, _, _ = auth.verify_email(token_from_latest_email(email_delivery))

    updated = auth.update_admin_user(actor=admin_user, target_user_id=student_user.id, role=None, status="disabled")

    assert updated.status == "disabled"
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_by_token(student_token)
    assert detail_code(exc_info.value) == "unauthenticated"
    assert auth.get_user_by_token(admin_token).role == "admin"
    assert auth.audit_logs().logs[0].action == "user.update"


def test_admin_cannot_disable_or_demote_self(tmp_path, email_delivery) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    auth = auth_for_path(db_path)

    auth.register("admin@example.com", "correct-password")
    _, admin_user, _, _ = auth.verify_email(token_from_latest_email(email_delivery))

    with pytest.raises(HTTPException) as exc_info:
        auth.update_admin_user(actor=admin_user, target_user_id=admin_user.id, role="user", status=None)
    assert detail_code(exc_info.value) == "admin_self_lockout"

    with pytest.raises(HTTPException) as exc_info:
        auth.update_admin_user(actor=admin_user, target_user_id=admin_user.id, role=None, status="disabled")
    assert detail_code(exc_info.value) == "admin_self_lockout"
