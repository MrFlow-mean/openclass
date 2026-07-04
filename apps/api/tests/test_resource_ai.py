from __future__ import annotations

from app.models import (
    CoursePackage,
    LibraryChapter,
    ResourceAIQueryRequest,
    ResourceLibraryItem,
    ResourceSourceUnit,
    UserView,
    WorkspaceState,
)
from app.routers import resources as resources_router
from app.services.lesson_factory import create_empty_lesson
from app.services.rag_anything_adapter import source_units_to_rag_content_list
from app.services.resource_ai import build_resource_ai_index_status, query_resource_ai


def _resource() -> ResourceLibraryItem:
    chapter = LibraryChapter(
        id="chapter_evidence",
        title="Evidence workflow",
        summary="How source evidence is selected before downstream writing.",
        keywords=["evidence", "workflow"],
        page_start=1,
        page_end=2,
    )
    return ResourceLibraryItem(
        id="resource_1",
        name="workflow-notes.pdf",
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=1024,
        outline=[chapter],
        concept_index={"evidence": [chapter.id]},
        extracted_text_available=True,
        text_content=(
            "Evidence workflow\n"
            "Source evidence must be selected before a downstream writer consumes it.\n"
            "The handoff records locator and page metadata."
        ),
        parser_provider="raganything:mineru",
        parser_artifacts_path="/tmp/rag-artifacts/workflow",
        source_units=[
            ResourceSourceUnit(
                id="unit_text",
                content_type="text",
                text="Source evidence must be selected before a downstream writer consumes it.",
                page_idx=0,
                page_no=1,
                source_locator="raganything:text:item=0:page=1",
                order_index=0,
            ),
            ResourceSourceUnit(
                id="unit_table",
                content_type="table",
                text="Stage | Output\nLibrary | source units\nResolver | selected evidence",
                page_idx=1,
                page_no=2,
                source_locator="raganything:table:item=1:page=2",
                order_index=1,
            ),
        ],
    )


def _user() -> UserView:
    return UserView(
        id="user_resource_ai",
        email="resource-ai@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_resource_ai_index_status_reports_rag_content_list_units() -> None:
    status = build_resource_ai_index_status([_resource()])[0]

    assert status.parser_provider == "raganything:mineru"
    assert status.source_unit_count == 2
    assert status.text_unit_count == 1
    assert status.multimodal_unit_count == 1
    assert status.rag_content_list_available is True


def test_source_units_convert_to_rag_anything_content_list() -> None:
    content_list = source_units_to_rag_content_list(_resource().source_units)

    assert content_list[0]["type"] == "text"
    assert content_list[0]["text"].startswith("Source evidence")
    assert content_list[1]["type"] == "table"
    assert "Resolver" in content_list[1]["table_body"]


def test_resource_ai_query_returns_traceable_evidence_and_reference_context() -> None:
    response = query_resource_ai(
        [_resource()],
        ResourceAIQueryRequest(query="selected evidence", max_results=3),
    )

    assert response.used_rag_anything is True
    assert response.evidence_units
    assert response.evidence_units[0].resource_id == "resource_1"
    assert response.evidence_units[0].chapter_id == "chapter_evidence"
    assert "selected" in response.evidence_units[0].excerpt.lower()
    assert response.resource_matches[0].chapter_id == "chapter_evidence"
    assert response.selected_reference is not None
    assert response.selected_reference.resource_id == "resource_1"


def test_resource_ai_router_only_queries_resources_visible_to_lesson(monkeypatch) -> None:
    lesson_a = create_empty_lesson("Lesson A")
    lesson_b = create_empty_lesson("Lesson B")
    visible_resource = _resource().model_copy(update={"scope_lesson_id": lesson_a.id})
    hidden_resource = _resource().model_copy(update={"id": "hidden_resource", "scope_lesson_id": lesson_b.id})
    package = CoursePackage(
        title="Standalone",
        summary="",
        lessons=[lesson_a, lesson_b],
        resources=[visible_resource, hidden_resource],
        active_lesson_id=lesson_a.id,
    )
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    monkeypatch.setattr(resources_router, "load_workspace_for_user", lambda user_id: workspace)

    response = resources_router.query_lesson_resources(
        lesson_a.id,
        ResourceAIQueryRequest(query="selected evidence"),
        user=_user(),
    )

    assert [status.resource_id for status in response.index_status] == ["resource_1"]
    assert {unit.resource_id for unit in response.evidence_units} == {"resource_1"}
