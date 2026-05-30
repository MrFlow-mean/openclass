import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import auth_service as auth_service_module
from app.services import collaboration as collaboration_module
from app.services.auth_service import AuthService
from app.services.collaboration import CourseCollaborationService
from app.services.course_store import SqliteCourseStore
from app.services.lesson_factory import create_empty_lesson
from app.services.resource_library import build_resource_item


@pytest.fixture(autouse=True)
def email_delivery(monkeypatch):
    sent: list[dict[str, str]] = []
    monkeypatch.setenv("OPENCLASS_EMAIL_DELIVERY", "log")
    monkeypatch.setattr(auth_service_module, "send_transactional_email", lambda **kwargs: sent.append(kwargs))
    return sent


def _token_from_latest_email(sent: list[dict[str, str]]) -> str:
    match = re.search(r"token=([A-Za-z0-9_.~%-]+)", sent[-1]["text_body"])
    assert match
    return match.group(1)


def _verified_user(auth: AuthService, sent: list[dict[str, str]], email: str):
    auth.register(email, "correct-password")
    _, user, _, _ = auth.verify_email(_token_from_latest_email(sent))
    return user


def _collaboration(tmp_path, monkeypatch):
    db_path = tmp_path / "openclass.sqlite3"
    upload_dir = tmp_path / "uploads"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    auth = AuthService(db_path)
    service = CourseCollaborationService(db_path, upload_dir)
    monkeypatch.setattr(collaboration_module, "load_workspace_for_user", store.load_for_user)
    monkeypatch.setattr(collaboration_module, "save_workspace_for_user", store.save_for_user)
    return store, auth, service, upload_dir


def test_course_collaboration_publish_fork_submit_and_merge(tmp_path, monkeypatch, email_delivery) -> None:
    store, auth, service, upload_dir = _collaboration(tmp_path, monkeypatch)
    owner = _verified_user(auth, email_delivery, "owner@example.com")
    contributor = _verified_user(auth, email_delivery, "contributor@example.com")

    owner_workspace = store.load_for_user(owner.id)
    package = owner_workspace.packages[0]
    package.title = "协作课程"
    package.summary = "一套可共同维护的课程"
    lesson = create_empty_lesson("第一课")
    lesson.board_document.content_text = "原始讲义"
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    resource_path = tmp_path / "source.md"
    resource_path.write_text("# 资料\n可复制的课程资料", encoding="utf-8")
    package.resources.append(build_resource_item(resource_path, "source.md"))
    store.save_for_user(owner.id, owner_workspace)

    published = service.publish_package(owner, package.id)
    public_list = service.list_open_courses()

    assert [course.id for course in public_list.courses] == [published.course.id]
    assert published.course.stats.lessons == 1

    fork, fork_view = service.fork_open_course(contributor, published.course.id)
    contributor_workspace = store.load_for_user(contributor.id)
    fork_package = next(item for item in contributor_workspace.packages if item.id == fork.fork_package_id)

    assert fork_view.id == fork_package.id
    assert fork_package.id != package.id
    assert fork_package.lessons[0].id != lesson.id
    assert fork_package.resources[0].source_path
    assert fork_package.resources[0].source_path != str(resource_path)
    assert upload_dir in Path(fork_package.resources[0].source_path).parents

    source_workspace = store.load_for_user(owner.id)
    source_package = next(item for item in source_workspace.packages if item.id == package.id)
    source_package.lessons[0].board_document.content_text = "维护者同时更新了主线"
    store.save_for_user(owner.id, source_workspace)

    fork_package.lessons[0].board_document.content_text = "贡献者改进后的讲义"
    store.save_for_user(contributor.id, contributor_workspace)

    contribution = service.submit_contribution(
        contributor,
        fork.id,
        title="改进第一课",
        description="补充讲解结构",
    )

    assert contribution.status == "open"
    assert contribution.lesson_changes[0].status == "edited"
    assert contribution.lesson_changes[0].current_changed is True

    with pytest.raises(HTTPException) as exc_info:
        service.review_contribution(contributor, contribution.id, action="merge", message="")
    assert exc_info.value.status_code == 403

    merged = service.review_contribution(owner, contribution.id, action="merge", message="accept")
    reloaded_owner_workspace = store.load_for_user(owner.id)
    reloaded_package = next(item for item in reloaded_owner_workspace.packages if item.id == package.id)
    reloaded_lesson = reloaded_package.lessons[0]

    assert merged.status == "merged"
    assert reloaded_lesson.board_document.content_text == "贡献者改进后的讲义"
    assert reloaded_lesson.history_graph.commits[-1].metadata["contribution_id"] == contribution.id
    assert reloaded_lesson.history_graph.commits[-1].metadata["contributor_user_id"] == contributor.id
