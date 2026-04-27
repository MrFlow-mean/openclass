import pytest
from fastapi import HTTPException

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


def test_login_rejects_wrong_password(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)

    auth.register("student@example.com", "correct-password")

    with pytest.raises(HTTPException) as exc_info:
        auth.login("student@example.com", "wrong-password")

    assert exc_info.value.status_code == 401


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
