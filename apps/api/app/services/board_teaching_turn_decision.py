from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.models import AgentActivityEvent
from app.services import workspace_state
from app.services.ai_execution_adapter import AIExecutionAdapter, CodexAIExecutionAdapter
from app.services.board_heading_outline import (
    BoardHeadingTargetError,
    board_heading_outline_payload,
    build_board_heading_teaching_units,
)
from app.services.rich_document import document_to_markdown


TEACHING_TURN_DECISION_INSTRUCTIONS = """
You are TurnDecision for ordered board teaching in OpenClass. You do not teach, edit, or write the
board. Decide whether the learner's current message explicitly starts, continues, or restarts an
ordered explanation of the current board. A broad heading request should start an ordered sequence
when that heading has child headings. A request for one specific leaf heading remains an ordinary
targeted explanation unless the learner explicitly asks for ordered or sequential teaching. While
an ordered sequence is active, a short request to continue advances it, but a new question, a
location-specific explanation, or any write/edit/generate request must return `none`. For `start`,
copy one exact heading string from the supplied outline into `target_heading`; leave it empty only
when the learner clearly asks to start from the whole board. Return no learner-facing prose.
"""


class BoardTeachingTurnDecision(BaseModel):
    action: Literal["none", "start", "continue", "restart"] = "none"
    target_heading: str = ""
    reason: str = ""


@dataclass(frozen=True)
class BoardTeachingDecisionResult:
    decision: BoardTeachingTurnDecision = field(default_factory=BoardTeachingTurnDecision)
    activity: list[AgentActivityEvent] = field(default_factory=list)


def decide_board_teaching_turn(
    *,
    owner_user_id: str,
    lesson_id: str,
    model: str,
    user_message: str,
    has_selection: bool,
) -> BoardTeachingDecisionResult:
    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    if _has_explicit_document_mutation_request(user_message):
        return BoardTeachingDecisionResult()
    if not lesson.board_teaching_progress and not _may_be_ordered_teaching_request(user_message):
        return BoardTeachingDecisionResult()

    board_markdown = document_to_markdown(lesson.board_document)
    outline = board_heading_outline_payload(board_markdown)
    if not outline:
        return BoardTeachingDecisionResult()
    guide = lesson.board_teaching_guide
    progress = lesson.board_teaching_progress
    adapter: AIExecutionAdapter = CodexAIExecutionAdapter(
        owner_user_id=owner_user_id,
        model=model,
    )
    try:
        response = adapter.parse_structured(
            system_prompt=TEACHING_TURN_DECISION_INSTRUCTIONS,
            user_prompt=json.dumps(
                {
                    "user_message": user_message,
                    "has_current_selection": has_selection,
                    "active_sequence": bool(progress and progress.waiting_for_continue),
                    "active_target_heading": guide.target_heading if guide else "",
                    "current_heading": (
                        guide.section_plans[progress.current_section_index].heading
                        if guide
                        and progress
                        and 0 <= progress.current_section_index < len(guide.section_plans)
                        else ""
                    ),
                    "heading_outline": outline,
                    "response_contract": BoardTeachingTurnDecision.model_json_schema(),
                },
                ensure_ascii=False,
            ),
            schema=BoardTeachingTurnDecision,
        )
    except Exception:
        return BoardTeachingDecisionResult()
    decision = BoardTeachingTurnDecision.model_validate(response.output_parsed)
    if has_selection and decision.action == "start" and not decision.target_heading.strip():
        decision = BoardTeachingTurnDecision(
            action="none",
            reason="A selection-specific explanation is not a whole-board ordered sequence.",
        )
    if decision.action in {"continue", "restart"} and not progress:
        decision = BoardTeachingTurnDecision(
            action="start",
            target_heading=decision.target_heading,
            reason=decision.reason,
        )
    if decision.action == "continue" and progress and not progress.waiting_for_continue:
        decision = BoardTeachingTurnDecision(
            action="none",
            reason="The active ordered teaching sequence is already complete.",
        )
    if decision.action == "start" and decision.target_heading.strip():
        try:
            build_board_heading_teaching_units(
                board_markdown,
                target_heading=decision.target_heading,
            )
        except BoardHeadingTargetError:
            decision = BoardTeachingTurnDecision(
                action="none",
                reason="The proposed heading does not resolve uniquely in the current board.",
            )
    return BoardTeachingDecisionResult(decision=decision, activity=response.activity)


def _may_be_ordered_teaching_request(message: str) -> bool:
    normalized = re.sub(r"\s+", "", message or "").casefold()
    return bool(
        re.search(
            r"依次|按顺序|逐(?:项|节|个|段)?讲|从头|从开头|"
            r"开始(?:为我|给我|帮我)?讲(?:解)?|继续(?:为我|给我|帮我)?讲(?:解)?|"
            r"接着讲|下一(?:节|项|部分).{0,8}讲|重来.{0,8}讲|讲解|解释|说明",
            normalized,
        )
        or re.search(
            r"inorder|stepbystep|start(?:the)?teaching|continueteaching|"
            r"teach(?:the)?next(?:section|item)|restart(?:the)?teaching|teach|explain",
            normalized,
        )
    )


def _has_explicit_document_mutation_request(message: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?、；;：:]+", "", message or "").casefold()
    if not normalized:
        return False
    action = (
        r"(?:生成|补写|写入|新增|添加|扩展|完善|修改|改写|重写|删除|"
        r"generate|write|append|add|edit|rewrite|delete)"
    )
    target = r"(?:板书|文档|讲义|章节|小节|内容|board|document|lesson|section|content)"
    return bool(
        re.search(rf"{action}.{{0,16}}{target}", normalized)
        or re.search(rf"{target}.{{0,16}}{action}", normalized)
    )
