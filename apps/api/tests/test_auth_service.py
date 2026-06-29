import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services.auth_service import AuthService, OAuthProfile
from app.services.course_store import SqliteCourseStore


def test_register_login_and_admin_overview(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    admin_token, admin_user = auth.register("Teacher@Example.com", "correct-password")
    _, student_user = auth.register("student@example.com", "correct-password")

    assert admin_user.email == "teacher@example.com"
    assert admin_user.role == "admin"
    assert student_user.role == "user"
    assert auth.get_user_by_token(admin_token).id == admin_user.id

    login_token, logged_in = auth.login("teacher@example.com", "correct-password")

    assert logged_in.id == admin_user.id
    assert auth.get_user_by_token(login_token).last_login_at is not None

    overview = auth.overview()

    assert overview.stats.users == 2
    assert overview.stats.admins == 1
    assert overview.stats.packages == 0
    assert [user.email for user in overview.users] == ["student@example.com", "teacher@example.com"]
    assert logged_in.auth_identities[0].provider == "email"


def test_register_rejects_duplicate_email(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("student@example.com", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.register("student@example.com", "correct-password")

    assert exc_info.value.status_code == 409


def test_register_and_login_with_phone(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    token, user = auth.register("13800138000", "correct-password")

    assert user.phone == "13800138000"
    assert user.email.endswith("@phone.openclass.local")
    assert user.auth_identities[0].provider == "phone"
    assert auth.get_user_by_token(token).id == user.id

    login_token, logged_in = auth.login("+86 138 0013 8000", "correct-password")

    assert logged_in.id == user.id
    assert auth.get_user_by_token(login_token).phone == "13800138000"


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


def test_register_claims_guest_workspace(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    guest_token, guest_user = auth.start_guest_session()
    guest_workspace = store.load_for_user(guest_user.id)
    guest_workspace.packages[0].title = "登录前学习记录"
    store.save_for_user(guest_user.id, guest_workspace)

    _, user = auth.register("student@example.com", "correct-password", guest_token=guest_token)

    assert store.load_for_user(user.id).packages[0].title == "登录前学习记录"
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_by_token(guest_token)
    assert exc_info.value.status_code == 401


def test_register_rejects_duplicate_phone(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("13800138000", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.register("13800138000", "correct-password")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "该手机号已注册"


def test_login_rejects_wrong_password(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("student@example.com", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.login("student@example.com", "wrong-password")

    assert exc_info.value.status_code == 401


def test_provider_list_includes_supported_social_logins(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    provider_ids = {provider.id for provider in auth.providers()}

    assert {"google", "apple", "github", "microsoft", "x"}.issubset(provider_ids)
    assert "wechat" not in provider_ids


def test_provider_configuration_reflects_env(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    monkeypatch.setenv("OPENCLASS_OAUTH_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("OPENCLASS_OAUTH_GOOGLE_CLIENT_SECRET", "google-secret")

    providers = {provider.id: provider for provider in auth.providers()}

    assert providers["google"].configured is True
    assert "wechat" not in providers


def test_x_oauth_authorization_url_uses_pkce(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    monkeypatch.setenv("OPENCLASS_OAUTH_X_CLIENT_ID", "x-client")
    monkeypatch.setenv("OPENCLASS_OAUTH_X_CLIENT_SECRET", "x-secret")
    monkeypatch.delenv("OPENCLASS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("OPENCLASS_WEB_ORIGIN", raising=False)
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

    target = auth.oauth_authorization_url("x", "/", request)

    assert target.startswith("https://x.com/i/oauth2/authorize?")
    assert "code_challenge=" in target
    assert "code_challenge_method=S256" in target
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fapi%2Fauth%2Foauth%2Fx%2Fcallback" in target


def test_oauth_login_links_existing_email_and_reuses_unique_account(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    _, email_user = auth.register("student@example.com", "correct-password")
    google_token, google_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-1",
            email="student@example.com",
            display_name="Student From Google",
            avatar_url="https://example.com/avatar.png",
        )
    )
    _, second_google_user = auth.login_with_oauth(
        OAuthProfile(
            provider="google",
            subject="google-subject-1",
            email="student@example.com",
            display_name="Student From Google",
            avatar_url="https://example.com/avatar.png",
        )
    )

    assert google_user.id == email_user.id
    assert second_google_user.id == email_user.id
    assert auth.get_user_by_token(google_token).id == email_user.id
    assert {identity.provider for identity in google_user.auth_identities} == {"email", "google"}


def test_oauth_login_without_email_gets_stable_synthetic_email(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

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
