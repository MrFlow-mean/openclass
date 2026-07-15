from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel

from app.models import (
    AgentActivityEvent,
    BoardExplanationDirective,
    BoardFocusRef,
    BoardSectionTeachingPlan,
    BoardTaskRequirementSheet,
    BoardTeachingGuide,
    BoardTeachingProgress,
    SectionTeachingProgressView,
    new_id,
)
from app.services import workspace_state
from app.services.ai_execution_adapter import AIExecutionAdapter, CodexAIExecutionAdapter
from app.services.history import commit_operations, current_head_commit


BOARD_DIRECTIVE_INSTRUCTIONS = """
You are the Board AI in OpenClass. You do not talk to the learner and you do not edit the board.
Authorize a bounded explanation only when the supplied target excerpt supports it. The directive
must keep the Chatbot inside that excerpt, identify a useful teaching order, and forbid inventing
facts that are absent from the target. Return needs_clarification or blocked when the excerpt is not
usable. Do not output learner-facing prose.
"""

CHATBOT_EXPLANATION_INSTRUCTIONS = """
You are the learner-facing Chatbot in OpenClass. The Board AI has already selected and authorized
one board section. Explain only from the supplied directive. Do not claim that you read the whole
board, do not add unsupported facts, do not write or edit the board, and do not ask whether to begin.
Teach the current section naturally and stop after this section so the learner can continue later.
"""


class _LearnerExplanation(BaseModel):
    chatbot_message: str


@dataclass(frozen=True)
class AutoTeachingResult:
    status: Literal["succeeded", "failed"]
    chatbot_message: str = ""
    failure_reason: str | None = None
    board_task: BoardTaskRequirementSheet | None = None
    board_task_run_id: str | None = None
    board_task_version_id: str | None = None
    progress: SectionTeachingProgressView | None = None
    activity: list[AgentActivityEvent] = field(default_factory=list)


def start_auto_board_teaching(
    *,
    owner_user_id: str,
    lesson_id: str,
    model: str,
) -> AutoTeachingResult:
    return _teach_section(
        owner_user_id=owner_user_id,
        lesson_id=lesson_id,
        model=model,
        requested_index=0,
        rebuild_guide=True,
        trigger="post_generation_auto_teach",
    )


def continue_board_teaching(
    *,
    owner_user_id: str,
    lesson_id: str,
    model: str,
    restart: bool,
) -> AutoTeachingResult:
    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    current_index = lesson.board_teaching_progress.current_section_index if lesson.board_teaching_progress else -1
    return _teach_section(
        owner_user_id=owner_user_id,
        lesson_id=lesson_id,
        model=model,
        requested_index=0 if restart else current_index + 1,
        rebuild_guide=restart or lesson.board_teaching_guide is None,
        trigger="board_teaching_restart" if restart else "board_teaching_continue",
    )


def _teach_section(
    *,
    owner_user_id: str,
    lesson_id: str,
    model: str,
    requested_index: int,
    rebuild_guide: bool,
    trigger: str,
) -> AutoTeachingResult:
    adapter: AIExecutionAdapter = CodexAIExecutionAdapter(
        owner_user_id=owner_user_id,
        model=model,
    )
    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    branch_name = lesson.history_graph.current_branch
    base_commit_id = current_head_commit(lesson).id
    board_text = lesson.board_document.content_text
    board_hash = _text_hash(board_text)
    guide = lesson.board_teaching_guide
    if rebuild_guide or guide is None or guide.board_snapshot_hash != board_hash:
        guide = _build_teaching_guide(
            document_id=lesson.board_document.id,
            board_title=lesson.board_document.title,
            board_text=board_text,
        )
    if requested_index < 0 or requested_index >= len(guide.section_plans):
        return AutoTeachingResult(status="failed", failure_reason="no_teachable_section")

    section = guide.section_plans[requested_index]
    focus = _focus_for_section(
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        section=section,
    )
    task = BoardTaskRequirementSheet(
        location_kind="target_range",
        target_hint=section.heading,
        target_location=focus,
        location_status="resolved",
        requested_action="explain",
        question_or_topic=section.heading,
        missing_items=[],
        progress=100,
        confirmation_status="confirmed",
    )
    run_id = new_id("boardtaskrun")
    version_id = new_id("boardtaskver")
    lesson.board_task_requirements = task
    lesson.board_teaching_guide = guide
    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=board_hash,
        current_section_index=requested_index,
        completed_section_indexes=list(
            range(requested_index)
        ),
        waiting_for_continue=False,
    )
    decision_trace = {
        "intent_signals": [trigger],
        "matched_rules": ["structured_post_generation_teaching"],
        "selected_action": "explain",
        "target_resolver": "markdown_section_resolver",
        "sequence_mode": "section_order",
        "role_executed": "board_ai",
        "document_changed": False,
        "reason": "A generated board has a deterministic first or next teachable section.",
    }
    commit_operations(
        lesson,
        operations=[],
        label="Board explanation task ready",
        message="Prepared a bounded board explanation task.",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_task_requirement_ready",
            "document_changed": False,
            "board_task_run_id": run_id,
            "board_task_version_id": version_id,
            "board_task_phase": "ready",
            "board_task_route": "explain",
            "active_board_task_sheet_after": task.model_dump(mode="json"),
            "board_task_cleared": False,
            "resolved_focus": focus.model_dump(mode="json"),
            "decision_trace": decision_trace,
        },
    )
    if not workspace_state.save_lesson_for_user_if_head(
        owner_user_id,
        lesson,
        expected_branch_name=branch_name,
        expected_head_commit_id=base_commit_id,
    ):
        return AutoTeachingResult(status="failed", failure_reason="lesson_changed")
    ready_commit_id = current_head_commit(lesson).id

    activity: list[AgentActivityEvent] = []
    try:
        directive_response = adapter.parse_structured(
            system_prompt=BOARD_DIRECTIVE_INSTRUCTIONS,
            user_prompt=json.dumps(
                {
                    "target_focus": focus.model_dump(mode="json"),
                    "target_excerpt": section.board_excerpt,
                    "requested_action": "explain",
                    "response_contract": BoardExplanationDirective.model_json_schema(),
                },
                ensure_ascii=False,
            ),
            schema=BoardExplanationDirective,
        )
        activity.extend(directive_response.activity)
        directive = BoardExplanationDirective.model_validate(directive_response.output_parsed)
        if directive.status != "approved" or not directive.target_excerpt.strip():
            raise RuntimeError(directive.reason or "board_explanation_not_approved")
        explanation_response = adapter.explain_from_directive(
            system_prompt=CHATBOT_EXPLANATION_INSTRUCTIONS,
            user_prompt=json.dumps(
                {
                    "board_explanation_directive": directive.model_dump(mode="json"),
                    "response_contract": _LearnerExplanation.model_json_schema(),
                },
                ensure_ascii=False,
            ),
            schema=_LearnerExplanation,
        )
        activity.extend(explanation_response.activity)
        explanation = _LearnerExplanation.model_validate(explanation_response.output_parsed)
        chatbot_message = explanation.chatbot_message.strip()
        if not chatbot_message:
            raise RuntimeError("empty_board_explanation")
    except Exception as exc:
        _record_auto_teaching_failure(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            branch_name=branch_name,
            expected_head_commit_id=ready_commit_id,
            run_id=run_id,
            version_id=version_id,
            task=task,
            reason=str(exc),
            decision_trace=decision_trace,
        )
        return AutoTeachingResult(
            status="failed",
            failure_reason=str(exc),
            board_task=task,
            board_task_run_id=run_id,
            board_task_version_id=version_id,
            activity=activity,
        )

    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    if current_head_commit(lesson).id != ready_commit_id:
        return AutoTeachingResult(status="failed", failure_reason="lesson_changed")
    lesson.board_task_requirements = None
    lesson.board_teaching_guide = guide
    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=board_hash,
        current_section_index=requested_index,
        completed_section_indexes=list(range(requested_index + 1)),
        waiting_for_continue=True,
    )
    progress = _progress_view(guide, requested_index)
    commit_operations(
        lesson,
        operations=[],
        label="Board-directed explanation",
        message="Chatbot explained the Board AI-authorized section.",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_directed_explanation",
            "user_message": "",
            "assistant_message": chatbot_message,
            "assistant_message_source": "chatbot_board_directed",
            "document_changed": False,
            "board_task_run_id": run_id,
            "board_task_version_id": version_id,
            "board_task_phase": "consumed",
            "board_task_route": "explain",
            "board_task_decision": "explain",
            "board_task_cleared": True,
            "active_board_task_sheet_after": None,
            "resolved_focus": focus.model_dump(mode="json"),
            "board_explanation_directive": directive.model_dump(mode="json"),
            "teaching_progress": progress.model_dump(mode="json"),
            "decision_trace": {**decision_trace, "role_executed": "chatbot"},
        },
    )
    if not workspace_state.save_lesson_for_user_if_head(
        owner_user_id,
        lesson,
        expected_branch_name=branch_name,
        expected_head_commit_id=ready_commit_id,
    ):
        return AutoTeachingResult(status="failed", failure_reason="lesson_changed")
    return AutoTeachingResult(
        status="succeeded",
        chatbot_message=chatbot_message,
        board_task=task,
        board_task_run_id=run_id,
        board_task_version_id=version_id,
        progress=progress,
        activity=activity,
    )


def _build_teaching_guide(
    *,
    document_id: str,
    board_title: str,
    board_text: str,
) -> BoardTeachingGuide:
    lines = board_text.splitlines()
    headings: list[tuple[int, int, str]] = []
    tokens = MarkdownIt().parse(board_text)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        level = int(token.tag[1]) if token.tag.startswith("h") else 1
        title = tokens[index + 1].content.strip() if index + 1 < len(tokens) else ""
        headings.append((token.map[0], level, title))
    selected_headings = headings[1:] if len(headings) > 1 and headings[0][1] == 1 else headings
    sections: list[BoardSectionTeachingPlan] = []
    for order_index, (start, level, title) in enumerate(selected_headings):
        next_start = len(lines)
        for candidate_start, candidate_level, _candidate_title in selected_headings[order_index + 1 :]:
            if candidate_level <= level:
                next_start = candidate_start
                break
        excerpt = "\n".join(lines[start:next_start]).strip()
        if excerpt:
            sections.append(
                BoardSectionTeachingPlan(
                    order_index=order_index,
                    heading=title or f"Section {order_index + 1}",
                    board_excerpt=excerpt[:6000],
                )
            )
    if not sections and board_text.strip():
        sections.append(
            BoardSectionTeachingPlan(
                order_index=0,
                heading=board_title or "Board",
                board_excerpt=board_text.strip()[:6000],
            )
        )
    return BoardTeachingGuide(
        board_document_id=document_id,
        board_snapshot_hash=_text_hash(board_text),
        board_title=board_title,
        section_plans=sections,
        generation_rationale="deterministic_markdown_sections",
    )


def _focus_for_section(
    *,
    lesson_id: str,
    document_id: str,
    section: BoardSectionTeachingPlan,
) -> BoardFocusRef:
    excerpt = section.board_excerpt.strip()
    return BoardFocusRef(
        source="board",
        lesson_id=lesson_id,
        document_id=document_id,
        kind="heading",
        heading_path=[section.heading],
        excerpt=excerpt,
        text_hash=_text_hash(excerpt),
        excerpt_hash=_text_hash(excerpt),
        confidence=1.0,
        reason="Resolved from deterministic Markdown section order.",
        display_label=section.heading,
        order_start=section.order_index,
        order_end=section.order_index,
    )


def _progress_view(guide: BoardTeachingGuide, index: int) -> SectionTeachingProgressView:
    return SectionTeachingProgressView(
        section_index=index,
        section_count=len(guide.section_plans),
        current_section_title=guide.section_plans[index].heading,
        has_next_section=index + 1 < len(guide.section_plans),
        waiting_for_continue=True,
    )


def _record_auto_teaching_failure(
    *,
    owner_user_id: str,
    lesson_id: str,
    branch_name: str,
    expected_head_commit_id: str,
    run_id: str,
    version_id: str,
    task: BoardTaskRequirementSheet,
    reason: str,
    decision_trace: dict[str, object],
) -> None:
    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    if current_head_commit(lesson).id != expected_head_commit_id:
        return
    lesson.board_task_requirements = None
    commit_operations(
        lesson,
        operations=[],
        label="Automatic explanation failed",
        message="The generated board was preserved for an explanation retry.",
        new_document=lesson.board_document,
        metadata={
            "kind": "auto_explain_failed",
            "document_changed": False,
            "board_task_run_id": run_id,
            "board_task_version_id": version_id,
            "board_task_phase": "not_executed",
            "board_task_route": "explain",
            "board_task_cleared": True,
            "active_board_task_sheet_after": None,
            "failed_board_task_sheet": task.model_dump(mode="json"),
            "failure_reason": reason,
            "decision_trace": {**decision_trace, "role_executed": "board_ai"},
        },
    )
    workspace_state.save_lesson_for_user_if_head(
        owner_user_id,
        lesson,
        expected_branch_name=branch_name,
        expected_head_commit_id=expected_head_commit_id,
    )


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
