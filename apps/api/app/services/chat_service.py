from __future__ import annotations

from app.models import ChatRequest, ChatResponse, CourseGraphEdge, Lesson, SelectionRef
from app.services.ai_logging import ai_usage_logger, log_ai_interaction_message
from app.services.ai_workflow import course_workflow
from app.services.openai_course_ai import bind_text_model_selection
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import (
    commit_document_snapshot,
    find_lesson_package,
    lesson_view,
    load_workspace_for_user,
    package_context_for_lesson,
    package_view_for_lesson,
    save_workspace_for_user,
)


def short_text(value: str, limit: int = 96) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def chat_flow_label(request: ChatRequest, action: str, *, auto_applied: bool) -> str:
    if auto_applied:
        prefix = "AI 写入"
    elif request.interaction_mode == "direct_edit":
        prefix = "AI 直接编辑"
    else:
        prefix = {
            "clarify_request": "AI 澄清",
            "no_change": "AI 讲解",
            "edit_board": "AI 文档生成",
            "append_section": "AI 追加章节",
            "create_new_lesson": "AI 新开课程",
            "await_scope_choice": "AI 范围选择",
            "await_reference_choice": "AI 资料确认",
        }.get(action, "AI 流程")
    return f"{prefix} · {short_text(request.message, 28)}"


def chat_flow_metadata(
    *,
    request: ChatRequest,
    teacher_message: str,
    workflow_result: dict[str, object],
    created_lesson: Lesson | None,
    auto_applied: bool,
) -> dict[str, object]:
    board_decision = workflow_result["board_decision"]
    learning_clarification = workflow_result["learning_clarification"]
    return {
        "kind": "chat_flow",
        "user_message": request.message,
        "assistant_message": teacher_message,
        "interaction_mode": request.interaction_mode,
        "scope_action": request.scope_action,
        "resource_reference_action": request.resource_reference_action,
        "board_edit_action": request.board_edit_action,
        "board_edit_topic": request.board_edit_topic,
        "teaching_action": request.teaching_action,
        "board_action": board_decision.action,
        "selection": request.selection.model_dump(mode="json") if request.selection else None,
        "learning_need_checklist": workflow_result["learning_requirement_sheet"].learning_need_checklist,
        "learning_need_catalog": [
            item.model_dump(mode="json")
            for item in workflow_result["learning_requirement_sheet"].learning_need_catalog
        ],
        "learning_clarification": learning_clarification.model_dump(mode="json"),
        "board_edit_prompt": (
            workflow_result["board_edit_prompt"].model_dump(mode="json")
            if workflow_result.get("board_edit_prompt") is not None
            else None
        ),
        "board_teaching_guide": (
            workflow_result["board_teaching_guide"].model_dump(mode="json")
            if workflow_result.get("board_teaching_guide") is not None
            else None
        ),
        "board_teaching_progress": (
            workflow_result["board_teaching_progress"].model_dump(mode="json")
            if workflow_result.get("board_teaching_progress") is not None
            else None
        ),
        "teaching_progress": (
            workflow_result["teaching_progress"].model_dump(mode="json")
            if workflow_result.get("teaching_progress") is not None
            else None
        ),
        "created_lesson_id": created_lesson.id if created_lesson else None,
        "created_lesson_title": created_lesson.title if created_lesson else None,
        "auto_applied": auto_applied,
    }


def chat_flow_message(request: ChatRequest, teacher_message: str) -> str:
    return f"用户：{short_text(request.message)}\nAI：{short_text(teacher_message, 120)}"


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation,
    *,
    user_id: str,
) -> ChatResponse:
    selection = None
    if selection_text:
        selection = SelectionRef(kind="board", lesson_id=lesson_id, excerpt=selection_text)
    return process_chat_on_lesson(
        lesson_id,
        ChatRequest(
            message=instruction,
            selection=selection,
            interaction_mode="direct_edit",
            conversation=conversation,
        ),
        user_id=user_id,
    )


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    workspace = load_workspace_for_user(user_id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        lesson=lesson,
        trace_prefix="chat",
        selection_kind=request.selection.kind if request.selection else None,
    ):
        log_ai_interaction_message(
            channel="text",
            direction="input",
            role="user",
            transport="typed_text",
            content=request.message,
            metadata={
                "selection": request.selection,
                "interaction_mode": request.interaction_mode,
                "scope_action": request.scope_action,
                "text_model": request.text_model,
                "resource_reference_action": request.resource_reference_action,
                "board_edit_action": request.board_edit_action,
                "board_edit_topic": request.board_edit_topic,
            },
        )
        ai_usage_logger.log_event(
            "chat_request",
            message=request.message,
            text_model=request.text_model,
            selection=request.selection,
            interaction_mode=request.interaction_mode,
            scope_action=request.scope_action,
            resource_chapter_id=request.resource_chapter_id,
            resource_reference_action=request.resource_reference_action,
            resource_reference_resource_id=request.resource_reference_resource_id,
            resource_reference_chapter_id=request.resource_reference_chapter_id,
            board_edit_action=request.board_edit_action,
            board_edit_topic=request.board_edit_topic,
            teaching_action=request.teaching_action,
            conversation=request.conversation,
        )

        try:
            workflow_package = package_context_for_lesson(workspace, package, lesson_id)
            with bind_text_model_selection(request.text_model):
                workflow_result = course_workflow.invoke(
                    {"lesson": lesson, "course_package": workflow_package, "request": request}
                )
            lesson.learning_requirements = workflow_result["learning_requirement_sheet"]
            lesson.summary = workflow_result["learning_requirement_sheet"].learning_goal
            lesson.board_teaching_guide = workflow_result.get("board_teaching_guide")
            if workflow_result.get("board_teaching_progress") is not None:
                lesson.board_teaching_progress = workflow_result["board_teaching_progress"]
            created_lesson = workflow_result.get("generated_lesson")
            if created_lesson is not None and workflow_result.get("board_teaching_progress") is not None:
                created_lesson.board_teaching_progress = workflow_result["board_teaching_progress"]
            if created_lesson is None:
                lesson.teaching_guide = workflow_result["teaching_guide"]
            teacher_message = workflow_result["teacher_message"]
            teacher_document = workflow_result.get("teacher_document")
            auto_applied_document = (
                created_lesson is None
                and workflow_result["board_decision"].action in {"edit_board", "append_section"}
                and bool(workflow_result.get("document_updated"))
                and teacher_document is not None
            )

            if auto_applied_document and teacher_document is not None:
                lesson.board_document = teacher_document
                lesson.teaching_guide = workflow_result["teaching_guide"]

            if created_lesson is not None:
                package.lessons.append(created_lesson)
                package.course_graph.append(
                    CourseGraphEdge(
                        source_lesson_id=lesson.id,
                        target_lesson_id=created_lesson.id,
                        relationship="deep_dive",
                    )
                )
                package.open_lesson_ids.append(created_lesson.id)
                package.workspace_tab_order.append(created_lesson.id)
                package.active_lesson_id = created_lesson.id

            response_selected_reference = workflow_result.get("selected_reference")

            metadata = chat_flow_metadata(
                request=request,
                teacher_message=teacher_message,
                workflow_result=workflow_result,
                created_lesson=created_lesson,
                auto_applied=auto_applied_document,
            )
            label = chat_flow_label(
                request,
                workflow_result["board_decision"].action,
                auto_applied=auto_applied_document,
            )
            commit_document_snapshot(
                lesson,
                label=label,
                message=chat_flow_message(request, teacher_message),
                metadata=metadata,
            )
            save_workspace_for_user(user_id, workspace)

            response = ChatResponse(
                teacher_message=teacher_message,
                learning_requirement_sheet=workflow_result["learning_requirement_sheet"],
                learning_clarification=workflow_result["learning_clarification"],
                board_decision=workflow_result["board_decision"],
                needs_clarification=workflow_result.get("needs_clarification", False),
                clarification_questions=workflow_result.get("clarification_questions", []),
                patch_proposal=None,
                scope_options=workflow_result.get("scope_options", []),
                resource_matches=workflow_result.get("resource_matches", []),
                reference_prompt=workflow_result.get("reference_prompt"),
                board_edit_prompt=workflow_result.get("board_edit_prompt"),
                selected_reference=response_selected_reference,
                created_lesson=lesson_view(created_lesson) if created_lesson else None,
                teaching_progress=workflow_result.get("teaching_progress"),
                course_package=package_view_for_lesson(workspace, package, package.active_lesson_id),
            )
        except Exception as exc:
            ai_usage_logger.log_event("chat_error", error=str(exc))
            raise

        ai_usage_logger.log_event(
            "chat_response",
            teacher_message=response.teacher_message,
            learning_requirement_sheet=response.learning_requirement_sheet,
            learning_clarification=response.learning_clarification,
            board_decision=response.board_decision,
            needs_clarification=response.needs_clarification,
            clarification_questions=response.clarification_questions,
            patch_proposal=response.patch_proposal,
            scope_options=response.scope_options,
            resource_matches=response.resource_matches,
            reference_prompt=response.reference_prompt,
            board_edit_prompt=response.board_edit_prompt,
            selected_reference=response.selected_reference,
            created_lesson=response.created_lesson,
        )
        log_ai_interaction_message(
            channel="text",
            direction="output",
            role="assistant",
            transport="chat_response",
            content=response.teacher_message,
            metadata={
                "board_action": response.board_decision.action,
                "needs_clarification": response.needs_clarification,
                "created_lesson_id": response.created_lesson.id if response.created_lesson else None,
            },
        )
        return response
