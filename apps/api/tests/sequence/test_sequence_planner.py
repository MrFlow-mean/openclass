from __future__ import annotations

from app.models import BoardFocusRef, BoardTaskRequirementSheet
from app.services.board_segment_index import build_board_segment_index
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.sequence_planner import (
    maybe_apply_sequential_explanation_choice,
    plan_explanation_sequence,
    requests_collection_explanation_sequence,
)


def _lesson_with_board(content_text: str):
    lesson = create_empty_lesson("顺序讲解测试")
    lesson.board_document = build_document(title="已有板书", content_text=content_text)
    return lesson


def _focus_from_text(lesson, text: str) -> BoardFocusRef:
    segment = next(
        segment
        for segment in build_board_segment_index(lesson.board_document).segments
        if text in segment.text
    )
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=segment.segment_id,
        kind=segment.kind,
        heading_path=segment.heading_path,
        excerpt=segment.text,
        confidence=0.9,
        reason="测试定位结果。",
        display_label=" / ".join(segment.heading_path) or segment.text,
        source_segment_ids=[segment.segment_id],
        order_start=segment.order_index,
        order_end=segment.order_index,
    )


def _explain_task(*, target_hint: str, question_or_topic: str) -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint=target_hint,
        location_status="resolved",
        requested_action="explain",
        question_or_topic=question_or_topic,
        progress=100,
        missing_items=[],
    )


def _resolved_resolution(focus: BoardFocusRef, candidates: list[BoardFocusRef] | None = None) -> FocusResolution:
    return FocusResolution(
        focus=focus,
        candidates=candidates or [focus],
        status="resolved",
        question="",
    )


def test_all_sections_request_generates_section_sequence() -> None:
    lesson = _lesson_with_board(
        "# 主线\n"
        "## 核心章节\n"
        "### 第一小节\n第一小节内容。\n"
        "### 第二小节\n第二小节内容。\n"
        "### 第三小节\n第三小节内容。"
    )
    parent_focus = _focus_from_text(lesson, "核心章节")
    board_task = _explain_task(target_hint="核心章节", question_or_topic="讲解所有小节")
    decision = BoardTaskRouteDecision(route="explain", location_status="found", target_focus=parent_focus)

    plan = plan_explanation_sequence(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=_resolved_resolution(parent_focus),
        request_message="讲解所有小节",
    )

    assert plan is not None
    assert plan.mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert plan.start_index == 0
    assert plan.scope_label == "主线 / 核心章节"
    assert [item.heading_path[-1] for item in plan.items] == ["第一小节", "第二小节", "第三小节"]
    assert [item.excerpt for item in plan.items] == ["第一小节内容。", "第二小节内容。", "第三小节内容。"]


def test_exercise_collection_generates_atomic_sequence() -> None:
    lesson = _lesson_with_board(
        "# 主线\n"
        "## 练习题\n"
        "练习1：根据提示完成下列题目。\n"
        "- 第一题：说明现象 A。\n"
        "- 第二题：比较方法 B 和方法 C。\n"
        "- 第三题：判断结论 D。"
    )
    first_candidate = _focus_from_text(lesson, "第一题")
    second_candidate = _focus_from_text(lesson, "第二题")
    board_task = _explain_task(target_hint="练习题", question_or_topic="讲解练习题")
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=first_candidate,
        candidate_focuses=[first_candidate, second_candidate],
    )

    plan = plan_explanation_sequence(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=_resolved_resolution(first_candidate, [first_candidate, second_candidate]),
        request_message="为我讲解练习题",
    )

    assert plan is not None
    assert plan.mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert [item.excerpt for item in plan.items] == [
        "题目：第一题：说明现象 A。",
        "题目：第二题：比较方法 B 和方法 C。",
        "题目：第三题：判断结论 D。",
    ]


def test_specific_question_does_not_generate_collection_sequence() -> None:
    lesson = _lesson_with_board(
        "# 主线\n"
        "## 练习题\n"
        "- 第一题：说明现象 A。\n"
        "- 第二题：比较方法 B 和方法 C。\n"
        "- 第三题：判断结论 D。"
    )
    second_question = _focus_from_text(lesson, "第二题")
    board_task = _explain_task(target_hint="第 2 题", question_or_topic="讲解第 2 题")
    decision = BoardTaskRouteDecision(route="explain", location_status="found", target_focus=second_question)

    assert requests_collection_explanation_sequence(board_task=board_task, request_message="讲解第 2 题") is False
    assert (
        plan_explanation_sequence(
            lesson=lesson,
            board_task=board_task,
            decision=decision,
            resolution=_resolved_resolution(second_question),
            request_message="讲解第 2 题",
        )
        is None
    )


def test_ambiguous_collection_candidates_can_become_explain_route() -> None:
    lesson = _lesson_with_board(
        "# 主线\n"
        "## 练习题\n"
        "- 第一题：说明现象 A。\n"
        "- 第二题：比较方法 B 和方法 C。"
    )
    first_candidate = _focus_from_text(lesson, "第一题")
    second_candidate = _focus_from_text(lesson, "第二题")
    board_task = _explain_task(target_hint="练习题", question_or_topic="讲解练习题")
    decision = BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous",
        candidate_focuses=[first_candidate, second_candidate],
        reason="有多个练习题候选。",
    )

    updated = maybe_apply_sequential_explanation_choice(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=FocusResolution(
            focus=None,
            candidates=[first_candidate, second_candidate],
            status="ambiguous",
            question="请选择要讲哪一道题。",
        ),
        request_message="为我讲解练习题",
    )

    assert updated.route == "explain"
    assert updated.location_status == "found"
    assert updated.target_focus == first_candidate
    assert updated.candidate_focuses == [first_candidate, second_candidate]


def test_single_selection_is_not_sequence() -> None:
    lesson = _lesson_with_board("# 主线\n## 核心章节\n这一段是被选中的内容。")
    selected_focus = _focus_from_text(lesson, "这一段")
    board_task = _explain_task(target_hint="这一段", question_or_topic="讲解这一段")
    decision = BoardTaskRouteDecision(route="explain", location_status="found", target_focus=selected_focus)

    plan = plan_explanation_sequence(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=_resolved_resolution(selected_focus),
        request_message="讲解这一段",
    )

    assert plan is None


def test_sequence_item_order_is_stable_even_when_candidates_are_reversed() -> None:
    lesson = _lesson_with_board(
        "# 主线\n"
        "## 练习题\n"
        "- 第一题：说明现象 A。\n"
        "- 第二题：比较方法 B 和方法 C。\n"
        "- 第三题：判断结论 D。"
    )
    first_candidate = _focus_from_text(lesson, "第一题")
    third_candidate = _focus_from_text(lesson, "第三题")
    board_task = _explain_task(target_hint="练习题", question_or_topic="讲解练习题")
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=third_candidate,
        candidate_focuses=[third_candidate, first_candidate],
    )

    plan = plan_explanation_sequence(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=_resolved_resolution(third_candidate, [third_candidate, first_candidate]),
        request_message="为我讲解练习题",
    )

    assert plan is not None
    assert [item.excerpt for item in plan.items] == [
        "题目：第一题：说明现象 A。",
        "题目：第二题：比较方法 B 和方法 C。",
        "题目：第三题：判断结论 D。",
    ]
