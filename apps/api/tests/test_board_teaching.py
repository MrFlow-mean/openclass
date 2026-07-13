from app.models import BoardTeachingProgress, ChatRequest
from app.services.board_teaching import _build_section_plan
from app.services.board_teaching_orchestrator import should_continue_board_teaching
from app.services.lesson_factory import create_empty_lesson


def test_section_teaching_plan_keeps_only_board_derived_context() -> None:
    plan = _build_section_plan(
        index=0,
        heading="任意小节",
        excerpt="结论成立时需要满足这个条件。",
    )

    assert plan.core_points == ["结论成立时需要满足这个条件。"]
    assert plan.teaching_steps == []
    assert plan.teaching_method == ""
    assert plan.example_or_analogy == ""
    assert plan.common_pitfalls == []
    assert plan.check_question == ""
    assert plan.transition_to_next == ""


def test_explicit_board_generation_request_does_not_continue_teaching() -> None:
    lesson = create_empty_lesson("任意课程")
    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash="snapshot",
        current_section_index=1,
        completed_section_indexes=[0, 1],
        waiting_for_continue=True,
    )

    assert not should_continue_board_teaching(
        lesson,
        ChatRequest(message="继续为我生成下一个章节的板书"),
    )
