from __future__ import annotations

from app.models import BoardFocusRef, BoardSegment, BoardTaskRequirementSheet
from app.services.board_segment_index import build_board_segment_index
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.sequence_planner import (
    maybe_apply_sequential_explanation_choice,
    plan_explanation_sequence,
    requests_collection_explanation_sequence,
)


BOARD_TEXT = """# 已有板书

## 第一节
第一节第一句。第一节第二句。

## 第二节
第二节第一句。第二节第二句。

## 练习题
- 第 1 题：判断结论 A。
- 第 2 题：比较方法 B 和方法 C。
"""


def _lesson():
    lesson = create_empty_lesson("测试主题")
    lesson.board_document = build_document(title="已有板书", content_text=BOARD_TEXT)
    return lesson


def _segments() -> list[BoardSegment]:
    return build_board_segment_index(_lesson().board_document).segments


def _focus_from_segment(segment: BoardSegment) -> BoardFocusRef:
    lesson = _lesson()
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=segment.segment_id,
        kind=segment.kind,
        heading_path=segment.heading_path,
        excerpt=segment.text,
        text_hash=segment.text_hash,
        confidence=0.95,
        reason="测试构造的板书目标。",
        display_label=" / ".join(segment.heading_path) or segment.text,
        match_id=f"test:{segment.segment_id}",
        source_segment_ids=[segment.segment_id],
        order_start=segment.order_index,
        order_end=segment.order_index,
    )


def _heading_focus(title: str) -> BoardFocusRef:
    segment = next(segment for segment in _segments() if segment.kind == "heading" and segment.text == title)
    return _focus_from_segment(segment)


def _list_focus(text: str) -> BoardFocusRef:
    segment = next(segment for segment in _segments() if segment.kind == "list" and text in segment.text)
    return _focus_from_segment(segment)


def test_all_sections_request_builds_sequence_plan() -> None:
    section_1 = _heading_focus("第一节")
    section_2 = _heading_focus("第二节")
    board_task = BoardTaskRequirementSheet(requested_action="explain", target_hint="所有小节", question_or_topic="讲解所有小节")
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=section_1,
        candidate_focuses=[section_1, section_2],
        reason="测试定位到多个小节。",
    )

    plan = plan_explanation_sequence(
        lesson=_lesson(),
        board_task=board_task,
        decision=decision,
        resolution=None,
        request_message="讲解所有小节",
    )

    assert plan is not None
    assert plan.start_index == 0
    assert plan.mode == "atomic_explanation"
    assert plan.planner_name == "sequence_planner"
    assert len(plan.items) >= 2


def test_exercise_collection_request_builds_atomic_sequence() -> None:
    exercise_heading = _heading_focus("练习题")
    board_task = BoardTaskRequirementSheet(requested_action="explain", target_hint="练习题", question_or_topic="为我讲解练习题")
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=exercise_heading,
        candidate_focuses=[exercise_heading],
        reason="测试定位到练习题。",
    )

    plan = plan_explanation_sequence(
        lesson=_lesson(),
        board_task=board_task,
        decision=decision,
        resolution=None,
        request_message="为我讲解练习题",
    )

    assert plan is not None
    excerpts = [item.excerpt for item in plan.items]
    assert "第 1 题" in excerpts[0]
    assert "第 2 题" in excerpts[1]


def test_single_numbered_question_is_not_collection_sequence() -> None:
    board_task = BoardTaskRequirementSheet(requested_action="explain", target_hint="第 2 题", question_or_topic="讲解第 2 题")

    assert not requests_collection_explanation_sequence(board_task=board_task, request_message="讲解第 2 题")

    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=_list_focus("第 2 题"),
        candidate_focuses=[],
        reason="测试定位到单题。",
    )
    assert plan_explanation_sequence(
        lesson=_lesson(),
        board_task=board_task,
        decision=decision,
        resolution=None,
        request_message="讲解第 2 题",
    ) is None


def test_ambiguous_collection_explain_can_turn_into_explain_route() -> None:
    section_1 = _heading_focus("第一节")
    section_2 = _heading_focus("第二节")
    board_task = BoardTaskRequirementSheet(requested_action="explain", target_hint="所有小节", question_or_topic="讲解所有小节")
    decision = BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous",
        target_focus=None,
        candidate_focuses=[section_1, section_2],
        reason="有多个候选小节。",
    )

    routed = maybe_apply_sequential_explanation_choice(
        lesson=_lesson(),
        board_task=board_task,
        decision=decision,
        resolution=None,
        request_message="讲解所有小节",
    )

    assert routed.route == "explain"
    assert routed.target_focus == section_1
    assert routed.candidate_focuses == [section_1, section_2]


def test_single_selection_is_not_sequence_plan() -> None:
    board_task = BoardTaskRequirementSheet(requested_action="explain", target_hint="这一段", question_or_topic="解释这里")
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=_list_focus("第 1 题"),
        candidate_focuses=[],
        reason="测试定位到单个选区。",
    )

    assert plan_explanation_sequence(
        lesson=_lesson(),
        board_task=board_task,
        decision=decision,
        resolution=None,
        request_message="解释这里",
    ) is None


def test_sequence_item_order_is_stable() -> None:
    exercise_heading = _heading_focus("练习题")
    board_task = BoardTaskRequirementSheet(requested_action="explain", target_hint="练习题", question_or_topic="为我讲解练习题")
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=exercise_heading,
        candidate_focuses=[exercise_heading],
        reason="测试定位到练习题。",
    )

    plan = plan_explanation_sequence(
        lesson=_lesson(),
        board_task=board_task,
        decision=decision,
        resolution=None,
        request_message="为我讲解练习题",
    )

    assert plan is not None
    assert [item.excerpt.split("：", maxsplit=1)[0] for item in plan.items] == ["题目", "题目"]
    assert "第 1 题" in plan.items[0].excerpt
    assert "第 2 题" in plan.items[1].excerpt
