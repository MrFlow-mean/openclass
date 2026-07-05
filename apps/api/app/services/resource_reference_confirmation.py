from __future__ import annotations

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.course_runtime import effective_requirements
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements
from app.services.resource_requirement_bridge import (
    confirm_requirement_resource_reference,
    skip_requirement_resource_reference,
)


def run_resource_reference_confirmation_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
    history_state,
    visible_resources: list[ResourceLibraryItem],
) -> ChatResponse | None:
    active_requirement = _learning_requirement_from_history_or_lesson(lesson, history_state)
    active_clarification = _learning_clarification_from_history(history_state)
    if active_requirement is None or active_clarification is None:
        explicit_state = _explicit_resource_selection_state(
            lesson=lesson,
            request=request,
            visible_resources=visible_resources,
        )
        if explicit_state is None:
            return None
        active_requirement, active_clarification = explicit_state

    if request.resource_reference_action == "confirm":
        if not request.resource_reference_resource_id or not request.resource_reference_chapter_id:
            return None
        alignment = confirm_requirement_resource_reference(
            resources=visible_resources,
            requirement=active_requirement,
            resource_id=request.resource_reference_resource_id,
            chapter_id=request.resource_reference_chapter_id,
            user_message=request.message,
        )
        chatbot_message = _resource_confirmation_message(alignment.requirement)
        change_summary = "用户确认以指定资料位置作为空白板书生成依据。"
    else:
        alignment = skip_requirement_resource_reference(requirement=active_requirement)
        chatbot_message = "已记录：这次生成先不采用推荐资料位置。"
        change_summary = "用户跳过推荐资料位置。"

    lesson.learning_requirements = alignment.requirement
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    stamp = recorder.record_update(
        requirements=alignment.requirement,
        clarification=active_clarification,
        change_summary=change_summary,
        metadata={
            "resource_reference_action": request.resource_reference_action,
            "resource_reference_resource_id": request.resource_reference_resource_id,
            "resource_reference_chapter_id": request.resource_reference_chapter_id,
            "selected_resource_reference": (
                alignment.requirement.selected_resource_reference.model_dump(mode="json")
                if alignment.requirement.selected_resource_reference is not None
                else None
            ),
        },
    )
    board_decision = BoardDecision(
        action="no_change",
        reason="资料引用确认只更新学习需求清单，不修改右侧文档。",
    )
    commit_operations(
        lesson,
        [],
        label="Learning resource reference confirmation",
        message="Recorded a resource reference decision for blank-board generation",
        new_document=lesson.board_document,
        metadata={
            "kind": "learning_requirement_refinement",
            "refinement_route": "requirement_refining",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "workflow",
            "board_document_state": board_document_state.model_context(),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "document_changed": False,
            "active_requirement_sheet_after": alignment.requirement.model_dump(mode="json"),
            "learning_clarification_after": active_clarification.model_dump(mode="json"),
            "requirement_run_id": stamp.run_id,
            "requirement_version_id": stamp.version_id,
            "requirement_phase": stamp.phase,
            "requirement_history_changed": bool(recorder.operations),
            "resource_reference_action": request.resource_reference_action,
            "resource_reference_resource_id": request.resource_reference_resource_id,
            "resource_reference_chapter_id": request.resource_reference_chapter_id,
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_and_learning_requirement_history_for_user(
        user_id,
        workspace,
        learning_requirement_history_operations=recorder.operations,
    )
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=effective_requirements(lesson),
        active_requirement_sheet=alignment.requirement,
        active_interaction_session=None,
        interaction_decision=None,
        learning_clarification=active_clarification,
        requirement_run_id=stamp.run_id,
        requirement_version_id=stamp.version_id,
        requirement_phase=stamp.phase,
        board_task_sheet=None,
        active_board_task_sheet=None,
        board_task_questions=[],
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        resource_matches=alignment.resource_matches,
        selected_reference=alignment.selected_reference,
        focus_candidates=[],
        requirement_cleared=False,
        board_document_operation_status="none",
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _learning_requirement_from_history_or_lesson(
    lesson,
    history_state,
) -> LearningRequirementSheet | None:
    if history_state:
        raw = history_state.get("latest_sheet_json")
        if isinstance(raw, str) and raw.strip():
            try:
                return LearningRequirementSheet.model_validate_json(raw)
            except Exception:
                pass
    return lesson.learning_requirements


def _learning_clarification_from_history(history_state) -> LearningClarificationStatus | None:
    if history_state:
        raw = history_state.get("latest_clarification_json")
        if isinstance(raw, str) and raw.strip():
            try:
                return LearningClarificationStatus.model_validate_json(raw)
            except Exception:
                pass
    return None


def _explicit_resource_selection_state(
    *,
    lesson,
    request: ChatRequest,
    visible_resources: list[ResourceLibraryItem],
) -> tuple[LearningRequirementSheet, LearningClarificationStatus] | None:
    if request.resource_reference_action != "confirm":
        return None
    if not request.resource_reference_resource_id or not request.resource_reference_chapter_id:
        return None
    resource = next(
        (candidate for candidate in visible_resources if candidate.id == request.resource_reference_resource_id),
        None,
    )
    if resource is None:
        return None
    chapter = next(
        (candidate for candidate in resource.outline if candidate.id == request.resource_reference_chapter_id),
        None,
    )
    if chapter is None:
        return None

    chapter_path = " / ".join(chapter.path) if chapter.path else chapter.title
    requirement = build_requirements(chapter.title)
    requirement.theme = chapter.title
    requirement.learning_goal = f"围绕资料章节“{chapter_path}”生成板书文档。"
    requirement.current_questions = ["请说明希望这节资料生成成什么样的板书。"]
    requirement.target_depth = "根据用户后续指令决定讲解深度。"
    requirement.output_preference = "板书文档"
    requirement.boundary = f"优先围绕《{resource.name}》中的“{chapter_path}”。"
    requirement.board_workflow = "generate_from_scratch"
    requirement.work_mode = "knowledge_board"
    requirement.granularity = "single_knowledge_point"

    clarification = LearningClarificationStatus(
        progress=40,
        label="collecting",
        reason=f"已选择《{resource.name}》中的“{chapter_path}”作为后续板书资料范围。",
        missing_items=["生成指令"],
        can_start=False,
        forced_start=False,
        summary=f"后续板书将优先参考资料章节“{chapter_path}”。",
        next_question="你希望基于这一节生成怎样的板书？",
        ready_for_board=False,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )
    return requirement, clarification


def _resource_confirmation_message(requirement: LearningRequirementSheet) -> str:
    reference = requirement.selected_resource_reference
    if reference is None or reference.status != "confirmed":
        return "已记录这次资料选择。"
    return f"已选择《{reference.resource_name}》中的“{reference.chapter_title}”作为后续板书资料范围。"
