"""Chat 主流程：interaction_mode 分支、requirement 更新、SSE 事件组装。

Router 经 chat_service 调用；模型与 prompt 在 openai_course_ai，intent 分支在 chatbot_handlers。
"""
from __future__ import annotations

from app.constants import (
    COMMIT_KIND_BOARD_DOCUMENT_EDIT,
    COMMIT_KIND_BOARD_DOCUMENT_GENERATION,
    COMMIT_KIND_CHAT_FLOW,
)
from app.models import BoardDecision, ChatRequest, ChatResponse, ConversationTurn
from app.services import workspace_state
from app.services.board_document_editor import edit_existing_document, generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide, teach_first_section, teach_next_section
from app.services.course_runtime import effective_requirements, refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.learning_requirement_manager import (
    is_generation_control_request,
    update_learning_requirements_from_chat,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import is_document_empty
from app.services.resource_resolver import resolve_resource_reference
from app.services.segment_resolver import focus_context, resolve_board_focus
from app.services.chatbot_handlers import (
    _generate_board_from_confirmed_resource,
    _handle_document_evidence_action,
    _handle_document_lookup_request,
    _handle_existing_interaction_session,
    _handle_resource_document_import_request,
    _maybe_start_interaction_session,
)
from app.services.chatbot_patterns import DOCUMENT_WRITE_ACTIONS, EDIT_ACTIONS
from app.services.chatbot_support import (
    _board_summary,
    _build_strong_reasoning_prompt,
    _chatbot_message_with_solver_context,
    _clear_task_requirements,
    _conversation_summary,
    _generate_focus_candidate_message,
    _generate_strong_reasoning_recommendation,
    _infer_board_task_action,
    _latest_learning_clarification,
    _merge_selection_and_reference,
    _prefer_requirement_action,
    _reference_metadata,
    _requests_contextual_continuation_explanation,
    _requests_document_artifact_generation,
    _requests_learning_start,
    _requests_resource_backed_answer,
    _requests_resource_output_explanation,
    _requests_whole_document_transform,
    _resource_generation_metadata,
    _resource_resolution_query,
    _resource_summary,
    _resource_summary_with_reference,
    _response,
    _selection_excerpt,
    _should_generate_board_after_reference_confirmation,
    _should_generate_board_from_explicit_request,
    _should_offer_strong_reasoning,
    _should_preserve_requirement_update_for_action,
    _should_prompt_resource_reference,
    _strong_reasoning_prompt_metadata,
    _task_metadata,
    _with_task_details,
)


# ---------------------------------------------------------------------------
# Chat 主入口
# ---------------------------------------------------------------------------


def _chat_response(
    *,
    lesson_id: str,
    request: ChatRequest,
    user_id: str,
    selection_text: str | None = None,
) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    requirements = effective_requirements(lesson)
    visible_package = workspace_state.package_context_for_lesson(workspace, package, lesson.id)
    selection_excerpt = _selection_excerpt(request.selection, selection_text)
    action_type = _infer_board_task_action(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=is_document_empty(lesson.board_document),
    )
    action_type = _prefer_requirement_action(
        action_type,
        requirements.action_type,
        request_message=request.message,
        requirements=requirements,
    )
    document_action_response = _handle_document_evidence_action(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        resources=visible_package.resources,
    )
    if document_action_response is not None:
        return document_action_response

    document_lookup_response = _handle_document_lookup_request(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        resources=visible_package.resources,
    )
    if document_lookup_response is not None:
        return document_lookup_response

    resource_query = _resource_resolution_query(request, requirements)
    resource_resolution = resolve_resource_reference(
        resources=visible_package.resources,
        user_message=resource_query,
        reference_action=request.resource_reference_action,
        reference_resource_id=request.resource_reference_resource_id,
        reference_chapter_id=request.resource_reference_chapter_id,
        reference_segment_id=request.resource_reference_segment_id,
        allow_direct_reference=(
            (
                _requests_resource_backed_answer(request.message)
                and request.interaction_mode != "direct_edit"
                and action_type not in DOCUMENT_WRITE_ACTIONS
                and request.board_generation_action != "start"
                and not _requests_document_artifact_generation(request.message)
                and not _requests_resource_output_explanation(request.message)
                and not _requests_learning_start(request.message)
            )
            or (request.board_generation_action == "start" and resource_query != request.message)
        ),
    )
    selected_reference = resource_resolution.selected_reference
    selection_or_reference_excerpt = _merge_selection_and_reference(selection_excerpt, selected_reference)
    resource_summary_for_turn = _resource_summary_with_reference(visible_package.resources, selected_reference)

    resource_import_response = _handle_resource_document_import_request(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        resources=visible_package.resources,
        resource_resolution=resource_resolution,
        selection_excerpt=selection_excerpt,
    )
    if resource_import_response is not None:
        return resource_import_response

    interaction_response = _handle_existing_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        resources=visible_package.resources,
        selection_excerpt=selection_or_reference_excerpt,
    )
    if interaction_response is not None:
        return interaction_response

    if request.board_generation_action == "start":
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        requirements = _with_task_details(
            requirements,
            action_type="generate_board",
            instruction=request.message,
        )
        edit_outcome = generate_from_requirements(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_instruction=request.message,
        )
        chatbot_message = edit_outcome.chatbot_message
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
            requirements = lesson.learning_requirements
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
        requirement_cleared = edit_outcome.changed
        metadata = {
            "kind": COMMIT_KIND_BOARD_DOCUMENT_GENERATION,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": edit_outcome.assistant_message_source,
            "board_generation_action": request.board_generation_action,
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_resource_generation_metadata(None),
        }

        commit_operations(
            lesson,
            [],
            label="Board document generation",
            message="Generated board document from the learning requirement sheet",
            new_document=lesson.board_document,
            metadata=metadata,
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            requirement_cleared=requirement_cleared,
        )

    if request.teaching_action in {"continue", "restart"}:
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        if request.teaching_action == "restart":
            lesson.board_teaching_progress = None
            teaching_result = teach_first_section(
                lesson=lesson,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
            )
        else:
            teaching_result = teach_next_section(
                lesson=lesson,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
            )
        commit_operations(
            lesson,
            [],
            label="Board teaching turn",
            message="Recorded a section-by-section board teaching turn",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_CHAT_FLOW,
                "user_message": request.message,
                "assistant_message": teaching_result.chatbot_message,
                "assistant_message_source": "chatbot",
                "interaction_mode": request.interaction_mode,
                "teaching_action": request.teaching_action,
                "teaching_progress": teaching_result.progress_view.model_dump(mode="json"),
                "learning_clarification": learning_clarification.model_dump(mode="json"),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=teaching_result.chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="本轮是分节讲解，不修改板书。"),
            teaching_progress=teaching_result.progress_view,
        )

    if _requests_contextual_continuation_explanation(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=is_document_empty(lesson.board_document),
    ):
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_message=(
                f"用户原始请求：{request.message}\n"
                "系统已判断这是基于当前文档和上一轮总结的继续讲解请求。"
                "请直接顺着当前文档或最近对话中已经总结出的结构展开讲解；"
                "不要追问用户选择整篇还是某一块，不要写入或改动右侧文档。"
            ),
            selection_excerpt=None,
            interaction_mode=request.interaction_mode,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
        chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"
        commit_operations(
            lesson,
            [],
            label="Chat turn",
            message="Recorded a contextual continuation explanation chat turn",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_CHAT_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **_reference_metadata(resolution=resource_resolution),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="本轮是基于当前文档的继续讲解，不修改板书。"),
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
        )

    if request.interaction_mode == "direct_edit" and action_type != "append_section":
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message="",
        )
        action_type = _prefer_requirement_action(
            action_type,
            requirements.action_type,
            request_message=request.message,
            requirements=requirements,
        ) or "rewrite_target"
        resolution = resolve_board_focus(
            lesson=lesson,
            user_message=request.message,
            selection=request.selection,
            selection_text=selection_text,
            action_type=action_type,
        )
        requirements = _with_task_details(
            requirements,
            action_type=action_type,
            instruction=request.message,
            focus=resolution.focus,
            resolution=resolution,
        )
        if not resolution.resolved:
            lesson.learning_requirements = requirements
            chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
                lesson=lesson,
                requirements=requirements,
                resources=visible_package.resources,
                conversation=request.conversation,
                request=request,
                resolution=resolution,
            )
            commit_operations(
                lesson,
                [],
                label="Board focus clarification",
                message="Asked the learner to confirm the board focus before editing",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_CHAT_FLOW,
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=None,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=False,
                    ),
                },
            )
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
                focus_candidates=resolution.candidates,
            )

        edit_outcome = edit_existing_document(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_instruction=request.message,
            selection_excerpt=selection_excerpt,
            focus=resolution.focus,
        )
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
            requirements = lesson.learning_requirements
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
        requirement_cleared = edit_outcome.changed
        commit_operations(
            lesson,
            [],
            label="Board document edit",
            message="Applied a Board Document Editor AI update",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_BOARD_DOCUMENT_EDIT,
                "user_message": request.message,
                "assistant_message": edit_outcome.chatbot_message,
                "assistant_message_source": edit_outcome.assistant_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                "selection_text": selection_excerpt,
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_section_titles": edit_outcome.section_titles,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=resolution.focus,
                    focus_candidates=resolution.candidates,
                    requirement_cleared=requirement_cleared,
                ),
            },
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            resolved_focus=resolution.focus,
            focus_candidates=resolution.candidates,
            requirement_cleared=requirement_cleared,
        )

    if action_type in {*DOCUMENT_WRITE_ACTIONS, "explain_target"} and not is_document_empty(lesson.board_document):
        if _should_preserve_requirement_update_for_action(request):
            requirement_conversation = [
                *request.conversation,
                ConversationTurn(role="user", content=request.message),
            ]
            requirements, learning_clarification = update_learning_requirements_from_chat(
                lesson=lesson,
                resources=visible_package.resources,
                conversation=requirement_conversation,
                user_message=request.message,
                chatbot_message="",
            )
            interaction_start_response = _maybe_start_interaction_session(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resources=visible_package.resources,
                selection_text=selection_text,
            )
            if interaction_start_response is not None:
                return interaction_start_response
            action_type = _prefer_requirement_action(
                action_type,
                requirements.action_type,
                request_message=request.message,
                requirements=requirements,
            )
        else:
            learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)

        if action_type == "append_section":
            requirements = _with_task_details(
                requirements,
                action_type=action_type,
                instruction=request.message,
            )
            edit_outcome = edit_existing_document(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
                user_instruction=request.message,
                selection_excerpt=None,
                focus=None,
            )
            if edit_outcome.changed:
                refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
                requirements = lesson.learning_requirements
                lesson.board_teaching_guide = build_board_teaching_guide(lesson)
                lesson.board_teaching_progress = None
            requirement_cleared = edit_outcome.changed
            commit_operations(
                lesson,
                [],
                label="Board document edit",
                message="Appended new board content at the end of the current document",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_BOARD_DOCUMENT_EDIT,
                    "user_message": request.message,
                    "assistant_message": edit_outcome.chatbot_message,
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "selection_text": None,
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=requirement_cleared,
                    ),
                },
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                requirement_cleared=requirement_cleared,
            )

        if action_type in EDIT_ACTIONS and not selection_excerpt and _requests_whole_document_transform(request.message):
            requirements = _with_task_details(
                requirements,
                action_type=action_type,
                instruction=request.message,
            )
            edit_outcome = edit_existing_document(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
                user_instruction=request.message,
                selection_excerpt=None,
                focus=None,
                allow_replace_document=True,
            )
            if edit_outcome.changed:
                refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
                requirements = lesson.learning_requirements
                lesson.board_teaching_guide = build_board_teaching_guide(lesson)
                lesson.board_teaching_progress = None
            requirement_cleared = edit_outcome.changed
            commit_operations(
                lesson,
                [],
                label="Board document edit",
                message="Transformed the current board document",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_BOARD_DOCUMENT_EDIT,
                    "user_message": request.message,
                    "assistant_message": edit_outcome.chatbot_message,
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "selection_text": None,
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=requirement_cleared,
                    ),
                },
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                requirement_cleared=requirement_cleared,
            )

        resolution = resolve_board_focus(
            lesson=lesson,
            user_message=request.message,
            selection=request.selection,
            selection_text=selection_text,
            action_type=action_type,
        )
        requirements = _with_task_details(
            requirements,
            action_type=action_type,
            instruction=request.message,
            focus=resolution.focus,
            resolution=resolution,
        )
        if not resolution.resolved:
            lesson.learning_requirements = requirements
            chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
                lesson=lesson,
                requirements=requirements,
                resources=visible_package.resources,
                conversation=request.conversation,
                request=request,
                resolution=resolution,
            )
            commit_operations(
                lesson,
                [],
                label="Board focus clarification",
                message="Asked the learner to confirm the board focus before acting",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_CHAT_FLOW,
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=None,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=False,
                    ),
                },
            )
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
                focus_candidates=resolution.candidates,
            )

        if action_type in EDIT_ACTIONS:
            edit_outcome = edit_existing_document(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
                user_instruction=request.message,
                selection_excerpt=selection_excerpt,
                focus=resolution.focus,
            )
            if edit_outcome.changed:
                refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
                requirements = lesson.learning_requirements
                lesson.board_teaching_guide = build_board_teaching_guide(lesson)
                lesson.board_teaching_progress = None
            requirement_cleared = edit_outcome.changed
            commit_operations(
                lesson,
                [],
                label="Board document edit",
                message="Applied a Board Document Editor AI update",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_BOARD_DOCUMENT_EDIT,
                    "user_message": request.message,
                    "assistant_message": edit_outcome.chatbot_message,
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "selection_text": selection_excerpt,
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=resolution.focus,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=requirement_cleared,
                    ),
                },
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                resolved_focus=resolution.focus,
                focus_candidates=resolution.candidates,
                requirement_cleared=requirement_cleared,
            )

        focus_excerpt = focus_context(resolution.focus) if resolution.focus else ""
        if _should_offer_strong_reasoning(request=request, target_excerpt=focus_excerpt):
            strong_prompt = _build_strong_reasoning_prompt(request)
            chatbot_message, chatbot_message_source = _generate_strong_reasoning_recommendation(
                lesson=lesson,
                requirements=requirements,
                resources=visible_package.resources,
                conversation=request.conversation,
                request=request,
                target_excerpt=focus_excerpt,
            )
            lesson.learning_requirements = requirements
            commit_operations(
                lesson,
                [],
                label="Strong reasoning prompt",
                message="Asked the learner to confirm strong reasoning before solving a complex target",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_CHAT_FLOW,
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=resolution.focus,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=False,
                    ),
                    **_strong_reasoning_prompt_metadata(
                        prompt=strong_prompt,
                        action=request.strong_reasoning_action,
                    ),
                },
            )
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="no_change", reason=strong_prompt.reason),
                resolved_focus=resolution.focus,
                focus_candidates=resolution.candidates,
                strong_reasoning_prompt=strong_prompt,
            )

        solver_user_message, solver_metadata = _chatbot_message_with_solver_context(
            lesson=lesson,
            request=request,
            user_message=request.message,
            target_excerpt=focus_excerpt,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
        )
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_message=solver_user_message,
            selection_excerpt=focus_excerpt,
            interaction_mode=request.interaction_mode,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
        chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"

        requirement_cleared = bool(chatbot_message)
        commit_operations(
            lesson,
            [],
            label="Board target explanation",
            message="Answered a learner question about a resolved board segment",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_CHAT_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=resolution.focus,
                    focus_candidates=resolution.candidates,
                    requirement_cleared=requirement_cleared,
                ),
                **_strong_reasoning_prompt_metadata(
                    prompt=None,
                    action=request.strong_reasoning_action,
                ),
                **solver_metadata,
            },
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="本轮是目标文段讲解，不修改板书。"),
            resolved_focus=resolution.focus,
            focus_candidates=resolution.candidates,
            requirement_cleared=requirement_cleared,
        )

    if (
        is_generation_control_request(request.message)
        or _requests_document_artifact_generation(request.message)
        or _requests_resource_output_explanation(request.message)
    ):
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message="",
        )
        if resource_resolution.reference_prompt is not None and request.resource_reference_action is None:
            lesson.learning_requirements = requirements
            chatbot_message = resource_resolution.reference_prompt.question
            commit_operations(
                lesson,
                [],
                label="Resource reference prompt",
                message="Asked the learner to confirm a relevant resource chapter before continuing",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_CHAT_FLOW,
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": "resource_resolver",
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=False,
                    ),
                    **_reference_metadata(resolution=resource_resolution),
                },
            )
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                learning_clarification=learning_clarification,
                requirements=requirements,
                board_decision=BoardDecision(
                    action="await_reference_choice",
                    reason=resource_resolution.reference_prompt.reason,
                ),
                resource_matches=resource_resolution.matches,
                reference_prompt=resource_resolution.reference_prompt,
            )
        if request.resource_reference_action == "confirm" and selected_reference is not None:
            return _generate_board_from_confirmed_resource(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resource_resolution=resource_resolution,
                resource_summary_for_turn=resource_summary_for_turn,
                conversation_summary=_conversation_summary(request.conversation),
            )
        if _should_generate_board_from_explicit_request(
            lesson=lesson,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
        ):
            requirements = _with_task_details(
                requirements,
                action_type="generate_board",
                instruction=request.message,
            )
            edit_outcome = generate_from_requirements(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=resource_summary_for_turn,
                conversation_summary=_conversation_summary(requirement_conversation),
                user_instruction=request.message,
            )
            if edit_outcome.changed:
                refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
                requirements = lesson.learning_requirements
                lesson.board_teaching_guide = build_board_teaching_guide(lesson)
                lesson.board_teaching_progress = None
            requirement_cleared = edit_outcome.changed
            commit_operations(
                lesson,
                [],
                label="Board document generation",
                message="Generated board document from an explicit learner request",
                new_document=lesson.board_document,
                metadata={
                    "kind": COMMIT_KIND_BOARD_DOCUMENT_GENERATION,
                    "user_message": request.message,
                    "assistant_message": edit_outcome.chatbot_message,
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "board_generation_action": "explicit_board_request",
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=requirement_cleared,
                    ),
                    **_resource_generation_metadata(selected_reference),
                    **_reference_metadata(resolution=resource_resolution),
                },
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                learning_clarification=learning_clarification,
                requirements=requirements,
                board_decision=edit_outcome.board_decision,
                resource_matches=resource_resolution.matches,
                selected_reference=selected_reference,
                requirement_cleared=requirement_cleared,
            )
        lesson.learning_requirements = requirements
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=learning_clarification.summary or requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=resource_summary_for_turn,
            conversation_summary=_conversation_summary(request.conversation),
            user_message=request.message,
            selection_excerpt=selection_or_reference_excerpt,
            interaction_mode=request.interaction_mode,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
        chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"

        commit_operations(
            lesson,
            [],
            label="Chat turn",
            message="Recorded a learner and PM handoff chat turn",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_CHAT_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **_reference_metadata(resolution=resource_resolution),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason="本轮是需求确认到板书生成的交接，不自动写入板书。"),
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
        )

    if (
        resource_resolution.reference_prompt is not None
        and request.resource_reference_action is None
        and _should_prompt_resource_reference(request.message)
    ):
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        chatbot_message = resource_resolution.reference_prompt.question
        commit_operations(
            lesson,
            [],
            label="Resource reference prompt",
            message="Asked the learner to confirm a relevant resource chapter before answering",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_CHAT_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": "resource_resolver",
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **_reference_metadata(resolution=resource_resolution),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(
                action="await_reference_choice",
                reason=resource_resolution.reference_prompt.reason,
            ),
            resource_matches=resource_resolution.matches,
            reference_prompt=resource_resolution.reference_prompt,
        )

    if (
        request.resource_reference_action == "confirm"
        and selected_reference is not None
        and _should_generate_board_after_reference_confirmation(request.message)
    ):
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message="",
        )
        return _generate_board_from_confirmed_resource(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_resolution=resource_resolution,
            resource_summary_for_turn=resource_summary_for_turn,
            conversation_summary=_conversation_summary(request.conversation),
        )

    if _should_offer_strong_reasoning(request=request, target_excerpt=selection_or_reference_excerpt):
        strong_prompt = _build_strong_reasoning_prompt(request)
        chatbot_message, chatbot_message_source = _generate_strong_reasoning_recommendation(
            lesson=lesson,
            requirements=requirements,
            resources=visible_package.resources,
            conversation=request.conversation,
            request=request,
            target_excerpt=selection_or_reference_excerpt,
        )
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        if chatbot_message:
            requirement_conversation.append(ConversationTurn(role="assistant", content=chatbot_message))
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message=chatbot_message,
        )
        lesson.learning_requirements = requirements
        commit_operations(
            lesson,
            [],
            label="Strong reasoning prompt",
            message="Asked the learner to confirm strong reasoning before solving a complex request",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_CHAT_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **_reference_metadata(resolution=resource_resolution),
                **_strong_reasoning_prompt_metadata(
                    prompt=strong_prompt,
                    action=request.strong_reasoning_action,
                ),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=strong_prompt.reason),
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
            strong_reasoning_prompt=strong_prompt,
        )

    solver_user_message, solver_metadata = _chatbot_message_with_solver_context(
        lesson=lesson,
        request=request,
        user_message=request.message,
        target_excerpt=selection_or_reference_excerpt,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary_for_turn,
        conversation_summary=_conversation_summary(request.conversation),
    )
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary_for_turn,
        conversation_summary=_conversation_summary(request.conversation),
        user_message=solver_user_message,
        selection_excerpt=selection_or_reference_excerpt,
        interaction_mode=request.interaction_mode,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"
    requirement_conversation = [
        *request.conversation,
        ConversationTurn(role="user", content=request.message),
    ]
    if chatbot_message:
        requirement_conversation.append(ConversationTurn(role="assistant", content=chatbot_message))
    requirements, learning_clarification = update_learning_requirements_from_chat(
        lesson=lesson,
        resources=visible_package.resources,
        conversation=requirement_conversation,
        user_message=request.message,
        chatbot_message=chatbot_message,
    )
    lesson.learning_requirements = requirements

    interaction_start_response = _maybe_start_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=visible_package.resources,
        selection_text=selection_text,
        visible_chatbot_message=chatbot_message,
        visible_chatbot_message_source=chatbot_message_source,
        extra_metadata={
            **_strong_reasoning_prompt_metadata(
                prompt=None,
                action=request.strong_reasoning_action,
            ),
            **solver_metadata,
        },
    )
    if interaction_start_response is not None:
        return interaction_start_response

    board_decision = BoardDecision(action="no_change", reason="本轮是通用问答聊天，不自动修改讲义。")
    requirement_cleared = False

    commit_operations(
        lesson,
        [],
        label="Chat turn",
        message="Recorded a learner and chatbot chat turn",
        new_document=lesson.board_document,
        metadata={
            "kind": COMMIT_KIND_CHAT_FLOW,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_reference_metadata(resolution=resource_resolution),
            **_strong_reasoning_prompt_metadata(
                prompt=None,
                action=request.strong_reasoning_action,
            ),
            **solver_metadata,
        },
    )
    if requirement_cleared:
        _clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_for_user(user_id, workspace)
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
    )


