from app.models import CoursePackage, CreatePackageRequest, GenerateLessonRequest, UserView, WorkspaceState
from app.routers import workspace as workspace_router
from app.services.lesson_factory import create_empty_lesson


def _user() -> UserView:
    return UserView(
        id="user_test",
        email="test@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_create_package_without_summary_keeps_summary_empty(monkeypatch) -> None:
    workspace = WorkspaceState(packages=[], active_package_id=None)
    saved_workspaces = []

    monkeypatch.setattr(workspace_router, "load_workspace_for_user", lambda user_id: workspace)
    monkeypatch.setattr(
        workspace_router,
        "save_workspace_for_user",
        lambda user_id, next_workspace: saved_workspaces.append(next_workspace),
    )

    workspace_router.create_package(CreatePackageRequest(title="空课程包", summary=""), user=_user())

    assert workspace.packages[0].summary == ""
    assert workspace.active_package_id == workspace.packages[0].id
    assert saved_workspaces == [workspace]


def test_generate_lesson_without_target_uses_standalone_pool(monkeypatch) -> None:
    standalone_package = CoursePackage(id="course_standalone", title="单独课程", summary="", lessons=[])
    package_lesson = create_empty_lesson("包内旧课")
    course_package = CoursePackage(
        id="course_package",
        title="课程包",
        summary="",
        lessons=[package_lesson],
        open_lesson_ids=[package_lesson.id],
        active_lesson_id=package_lesson.id,
        workspace_tab_order=[package_lesson.id],
    )
    workspace = WorkspaceState(packages=[standalone_package, course_package], active_package_id=course_package.id)
    saved_workspaces = []

    monkeypatch.setattr(workspace_router, "load_workspace_for_user", lambda user_id: workspace)
    monkeypatch.setattr(workspace_router, "save_workspace_for_user", lambda user_id, next_workspace: saved_workspaces.append(next_workspace))

    response = workspace_router.generate_lesson(
        GenerateLessonRequest(topic="独立新课", start_blank=True),
        user=_user(),
    )

    assert [lesson.title for lesson in standalone_package.lessons] == ["独立新课"]
    assert [lesson.title for lesson in course_package.lessons] == ["包内旧课"]
    assert workspace.active_package_id == standalone_package.id
    assert response.is_standalone is True
    assert saved_workspaces == [workspace]


def test_generate_lesson_with_target_keeps_course_package_content_isolated(monkeypatch) -> None:
    standalone_package = CoursePackage(id="course_standalone", title="单独课程", summary="", lessons=[])
    course_package = CoursePackage(id="course_package", title="课程包", summary="", lessons=[])
    workspace = WorkspaceState(packages=[standalone_package, course_package], active_package_id=standalone_package.id)
    saved_workspaces = []

    monkeypatch.setattr(workspace_router, "load_workspace_for_user", lambda user_id: workspace)
    monkeypatch.setattr(workspace_router, "save_workspace_for_user", lambda user_id, next_workspace: saved_workspaces.append(next_workspace))

    response = workspace_router.generate_lesson(
        GenerateLessonRequest(topic="包内新课", target_package_id=course_package.id, start_blank=True),
        user=_user(),
    )

    assert standalone_package.lessons == []
    assert [lesson.title for lesson in course_package.lessons] == ["包内新课"]
    assert workspace.active_package_id == course_package.id
    assert response.is_standalone is False
    assert saved_workspaces == [workspace]


def test_generate_lesson_without_blank_flag_still_creates_codex_only_document(monkeypatch) -> None:
    package = CoursePackage(id="course", title="Course", summary="", lessons=[])
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    monkeypatch.setattr(workspace_router, "load_workspace_for_user", lambda _user_id: workspace)
    monkeypatch.setattr(workspace_router, "save_workspace_for_user", lambda *_args: None)

    workspace_router.generate_lesson(
        GenerateLessonRequest(topic="Codex document", start_blank=False),
        user=_user(),
    )

    lesson = package.lessons[0]
    assert lesson.board_document.content_text == ""
    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.active_interaction_session is None
