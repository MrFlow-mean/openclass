from app.models import CoursePackage, ResourceLibraryItem, WorkspaceState
from app.services.resource_service import delete_uploaded_resource_file, remove_resource_from_package
from app.services.workspace_state import package_context_for_lesson, package_view_for_lesson
from support import create_test_lesson


def test_remove_resource_from_package_returns_removed_resource() -> None:
    resource = ResourceLibraryItem(
        id="resource_1",
        name="lesson.png",
        mime_type="image/png",
        resource_type="image",
        size_bytes=12,
    )
    package = CoursePackage(title="测试课程", summary="", lessons=[], resources=[resource])

    removed = remove_resource_from_package(package, "resource_1")

    assert removed == resource
    assert package.resources == []


def test_delete_uploaded_resource_file_only_removes_upload_dir_files(tmp_path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    uploaded_file = upload_dir / "lesson.png"
    uploaded_file.write_bytes(b"image")
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"keep")

    uploaded_resource = ResourceLibraryItem(
        name="lesson.png",
        mime_type="image/png",
        resource_type="image",
        size_bytes=5,
        source_path=str(uploaded_file),
    )
    outside_resource = ResourceLibraryItem(
        name="outside.png",
        mime_type="image/png",
        resource_type="image",
        size_bytes=4,
        source_path=str(outside_file),
    )

    assert delete_uploaded_resource_file(uploaded_resource, upload_dir) is True
    assert delete_uploaded_resource_file(outside_resource, upload_dir) is False
    assert not uploaded_file.exists()
    assert outside_file.exists()


def test_standalone_package_resources_are_visible_only_to_their_lesson() -> None:
    lesson_a = create_test_lesson("单独课程 A")
    lesson_b = create_test_lesson("单独课程 B")
    standalone_package = CoursePackage(
        title="单独课程",
        summary="",
        lessons=[lesson_a, lesson_b],
        resources=[
            ResourceLibraryItem(
                id="resource_a",
                name="a.png",
                mime_type="image/png",
                resource_type="image",
                size_bytes=1,
                scope_lesson_id=lesson_a.id,
            ),
            ResourceLibraryItem(
                id="resource_b",
                name="b.png",
                mime_type="image/png",
                resource_type="image",
                size_bytes=1,
                scope_lesson_id=lesson_b.id,
            ),
            ResourceLibraryItem(
                id="legacy_global",
                name="legacy.png",
                mime_type="image/png",
                resource_type="image",
                size_bytes=1,
            ),
        ],
        active_lesson_id=lesson_a.id,
    )
    workspace = WorkspaceState(packages=[standalone_package], active_package_id=standalone_package.id)

    view = package_view_for_lesson(workspace, standalone_package, lesson_a.id)
    context = package_context_for_lesson(workspace, standalone_package, lesson_a.id)

    assert [resource.id for resource in view.resources] == ["resource_a"]
    assert [resource.id for resource in context.resources] == ["resource_a"]


def test_course_package_resources_are_shared_across_lessons() -> None:
    standalone_lesson = create_test_lesson("单独课程")
    package_lesson_a = create_test_lesson("包内课程 A")
    package_lesson_b = create_test_lesson("包内课程 B")
    standalone_package = CoursePackage(title="单独课程", summary="", lessons=[standalone_lesson])
    course_package = CoursePackage(
        title="课程包",
        summary="",
        lessons=[package_lesson_a, package_lesson_b],
        resources=[
            ResourceLibraryItem(
                id="package_resource",
                name="shared.pdf",
                mime_type="application/pdf",
                resource_type="document",
                size_bytes=10,
            )
        ],
        active_lesson_id=package_lesson_a.id,
    )
    workspace = WorkspaceState(packages=[standalone_package, course_package], active_package_id=course_package.id)

    view = package_view_for_lesson(workspace, course_package, package_lesson_b.id)
    context = package_context_for_lesson(workspace, course_package, package_lesson_b.id)

    assert [resource.id for resource in view.resources] == ["package_resource"]
    assert [resource.id for resource in context.resources] == ["package_resource"]
