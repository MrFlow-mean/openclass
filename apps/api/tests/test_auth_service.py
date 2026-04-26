import pytest
from fastapi import HTTPException

from app.services.auth_service import AuthService
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
