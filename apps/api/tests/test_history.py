import pytest

from app.models import BlockStyle, BoardBlock, BoardDocument, ChatRequest, PatchOperation, SelectionRef
from app.services.ai_workflow import classify_scope, course_workflow
from app.services.course_store import build_initial_course_package
from app.services.document_ops import apply_patch
from app.services.history import create_branch, restore_commit
from app.services.lesson_factory import create_lesson
from app.services.openai_course_ai import openai_course_ai


@pytest.fixture(autouse=True)
def disable_openai_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "client", None)


def test_apply_patch_supports_insert_and_update() -> None:
    document = BoardDocument(
        title="Test",
        blocks=[
            BoardBlock(type="paragraph", title="A", content="first"),
            BoardBlock(type="paragraph", title="B", content="second"),
        ],
    )
    operations = [
        PatchOperation(
            op="update_block_content",
            block_id=document.blocks[0].id,
            content="first updated",
            title="A revised",
        ),
        PatchOperation(
            op="insert_block",
            after_block_id=document.blocks[0].id,
            block=BoardBlock(
                type="note",
                title="Inserted",
                content="new note",
                style=BlockStyle(emphasis="callout"),
            ),
        ),
    ]

    next_document, diff = apply_patch(document, operations)

    assert next_document.blocks[0].title == "A revised"
    assert next_document.blocks[1].title == "Inserted"
    assert len(diff) == 2


def test_branch_and_restore_keep_history() -> None:
    lesson = create_lesson("勾股定理")
    first_commit_id = lesson.history_graph.commits[0].id

    create_branch(lesson, "alt-proof", first_commit_id)
    assert lesson.history_graph.current_branch == "alt-proof"
    assert lesson.history_graph.branches["alt-proof"].base_commit_id == first_commit_id

    restore_commit(lesson, first_commit_id, "Restore origin")
    assert lesson.history_graph.branches["alt-proof"].head_commit_id == lesson.history_graph.commits[-1].id


def test_scope_escalation_detects_out_of_domain_question() -> None:
    lesson = create_lesson("微积分入门")
    assert classify_scope("什么是袋代数中的环和层？什么是光滑？", lesson) == "scope_escalation"
    assert classify_scope("为这些内容出几道习题", lesson) == "in_scope"


def test_workflow_asks_for_clarification_when_request_is_too_vague() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="这里没懂"),
        }
    )

    assert result["needs_clarification"] is True
    assert result["board_decision"].action == "clarify_request"
    assert result["patch_proposal"] is None
    assert result["clarification_questions"]


def test_workflow_can_answer_without_changing_the_board() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="请解释一下勾股定理的核心公式"),
        }
    )

    assert result["needs_clarification"] is False
    assert result["board_decision"].action == "no_change"
    assert result["patch_proposal"] is None
    assert "勾股定理" in result["teacher_message"] or "直角三角形" in result["teacher_message"]


def test_workflow_generates_patch_preview_for_edit_request() -> None:
    package = build_initial_course_package()
    lesson = package.lessons[0]
    target_block = lesson.board_document.blocks[1]

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="把这一段讲得更易懂",
                selection=SelectionRef(
                    kind="board",
                    lesson_id=lesson.id,
                    block_id=target_block.id,
                    excerpt=target_block.content,
                ),
            ),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["patch_proposal"] is not None
    assert result["patch_proposal"].diff_preview
