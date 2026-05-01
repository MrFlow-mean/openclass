from __future__ import annotations

import hashlib
import json
import re
from typing import Any, TypedDict

from app.models import (
    BoardDecision,
    BoardDocument,
    ChatRequest,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    TeachingGuide,
)
from app.services.course_runtime import effective_requirements
from app.services.lesson_factory import build_teaching_guide

RESET_REASON = "旧版角色流程节点已删除；课堂内 AI 主链路已停止，等待新架构接入。"
RESET_TEACHER_MESSAGE = (
    "旧版课堂 AI 主链路已删除。当前这条消息不会触发 PM、板书、讲师、资料匹配或实时语音等旧流程；"
    "后端只保留课堂状态、文档保存和历史记录，等待新的 AI 架构接入。"
)


class WorkflowState(TypedDict, total=False):
    lesson: Lesson
    course_package: CoursePackage
    request: ChatRequest
    learning_requirement_sheet: LearningRequirementSheet
    learning_clarification: LearningClarificationStatus
    board_decision: BoardDecision
    teaching_guide: TeachingGuide
    teacher_message: str
    teacher_document: BoardDocument
    document_updated: bool
    resource_matches: list[ResourceMatch]


def classify_scope(message: str, lesson: Lesson) -> str:
    _ = message, lesson
    return "in_scope"


def match_resources(*args: Any, **kwargs: Any) -> list[ResourceMatch]:
    _ = args, kwargs
    return []


def _is_append_document_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    return any(keyword in compact for keyword in ["新增章节", "补充一节", "新开一节", "追加章节", "新增页面", "新增一页", "续写章节"])


def _board_snapshot_hash(document: BoardDocument) -> str:
    payload = document.model_dump(mode="json", exclude={"id"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SimpleCourseWorkflow:
    def invoke(self, state: WorkflowState) -> dict[str, object]:
        lesson = state["lesson"]
        requirements = effective_requirements(lesson)
        teaching_guide = lesson.teaching_guide or build_teaching_guide(
            lesson.id,
            lesson.title,
            lesson.board_document,
            requirements,
        )

        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": LearningClarificationStatus(
                progress=0,
                label="旧版课堂主链路已删除",
                reason=RESET_REASON,
                missing_items=[],
                can_start=False,
                forced_start=False,
            ),
            "needs_clarification": False,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="no_change", reason=RESET_REASON),
            "teaching_guide": teaching_guide,
            "teacher_message": RESET_TEACHER_MESSAGE,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "scope_options": [],
            "resource_matches": [],
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": None,
            "board_teaching_progress": None,
            "teaching_progress": None,
        }


course_workflow = SimpleCourseWorkflow()
