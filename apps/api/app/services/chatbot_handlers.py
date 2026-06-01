from __future__ import annotations

from app.constants import (
    COMMIT_KIND_BOARD_DOCUMENT_GENERATION,
    COMMIT_KIND_BOARD_DOCUMENT_IMPORT,
    COMMIT_KIND_DOCUMENT_EVIDENCE_GENERATION,
    COMMIT_KIND_DOCUMENT_EVIDENCE_INSERT,
    COMMIT_KIND_DOCUMENT_EVIDENCE_LOOKUP,
    COMMIT_KIND_INTERACTION_FLOW,
)
from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    DocumentEvidence,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.board_document_editor import generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.interaction_rules import (
    apply_interaction_decision,
    build_interaction_start,
    decide_interaction_turn,
    interaction_context_payload,
    interaction_session_metadata,
    should_start_interaction,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.document_locator import (
    document_evidence_from_id,
    locate_document_evidence,
    looks_like_document_request,
    queued_resource_message,
)
from app.services.resource_document_import import (
    apply_resource_document_import,
    requests_pending_resource_document_import,
    requests_resource_document_import,
    resource_import_operation,
    select_resource_import_payload,
)
from app.services.rich_document import build_document
from app.services.resource_resolver import ResourceResolution
from app.services.chatbot_support import (
    _board_summary,
    _clear_task_requirements,
    _compact_text,
    _conversation_summary,
    _generate_focus_candidate_message,
    _latest_learning_clarification,
    _reference_metadata,
    _resource_generation_metadata,
    _resource_summary,
    _response,
    _task_metadata,
    _with_task_details,
)

def _generate_resource_import_chatbot_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    result_context: str,
    imported_excerpt: str | None = None,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=(
            f"用户原始请求：{request.message}\n"
            "系统已经判断这是一条资料导入黑板的通用文档操作。"
            "请只根据下面的实际处理结果自然回复用户，不要要求用户再点击资料库按钮，也不要输出被导入的全文。\n"
            f"实际处理结果：{result_context}"
        ),
        selection_excerpt=_compact_text(imported_excerpt, limit=1200) if imported_excerpt else None,
        interaction_mode=request.interaction_mode,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot" if chatbot_message else "chatbot_empty"


def _handle_resource_document_import_request(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    resource_resolution: ResourceResolution,
    selection_excerpt: str | None,
) -> ChatResponse | None:
    is_direct_import_request = requests_resource_document_import(request.message, resources=resources)
    is_pending_import_request = requests_pending_resource_document_import(
        request.message,
        resources=resources,
        requirements=requirements,
        has_selection=bool(selection_excerpt),
    )
    if not is_direct_import_request and not is_pending_import_request:
        return None

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    operation = resource_import_operation(
        lesson=lesson,
        user_message=request.message,
        has_selection=bool(selection_excerpt),
        pending_location_confirmation=is_pending_import_request,
    )

    if operation is None:
        result_context = "当前黑板已有内容，系统还没有执行导入；需要用户说明是追加到现有内容后面，还是替换当前黑板。"
        chatbot_message, chatbot_message_source = _generate_resource_import_chatbot_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            result_context=result_context,
        )
        commit_operations(
            lesson,
            [],
            label="Resource document import",
            message="Asked the learner how to apply an uploaded resource to a non-empty board",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_BOARD_DOCUMENT_IMPORT,
                "import_status": "await_write_mode",
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
            board_decision=BoardDecision(action="no_change", reason="已有黑板内容，需要先确认导入方式。"),
            resource_matches=resource_resolution.matches,
            selected_reference=resource_resolution.selected_reference,
        )

    payload = select_resource_import_payload(
        resources=resources,
        user_message=request.message,
        resource_resolution=resource_resolution,
        operation=operation,
    )
    if payload is None:
        result_context = "系统还不能唯一确定要导入哪份资料，或者当前资料没有可写入的已抽取文本；本轮没有修改黑板。"
        chatbot_message, chatbot_message_source = _generate_resource_import_chatbot_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            result_context=result_context,
        )
        commit_operations(
            lesson,
            [],
            label="Resource document import",
            message="Could not resolve a resource document import request",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_BOARD_DOCUMENT_IMPORT,
                "import_status": "unresolved",
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
            board_decision=BoardDecision(action="no_change", reason="没有解析出可导入的资料内容。"),
            resource_matches=resource_resolution.matches,
            selected_reference=resource_resolution.selected_reference,
        )

    apply_resource_document_import(
        lesson=lesson,
        payload=payload,
        requirements=lesson.learning_requirements,
        selection_excerpt=selection_excerpt,
    )
    if payload.operation == "append_section":
        operation_label = "追加到当前黑板末尾"
    elif payload.operation == "replace_selection":
        operation_label = "替换到当前选中位置"
    else:
        operation_label = "替换当前黑板内容"
    result_context = (
        f"已将资料“{payload.resource.name}”的{payload.import_scope}内容{operation_label}；"
        f"导入文本长度约 {len(payload.content_text)} 个字符。"
    )
    chatbot_message, chatbot_message_source = _generate_resource_import_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        result_context=result_context,
        imported_excerpt=payload.content_text,
    )
    commit_operations(
        lesson,
        [],
        label="Resource document import",
        message="Imported uploaded resource text into the board document",
        new_document=lesson.board_document,
        metadata={
            "kind": COMMIT_KIND_BOARD_DOCUMENT_IMPORT,
            "import_status": "imported",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "resource_id": payload.resource.id,
            "resource_name": payload.resource.name,
            "resource_import_scope": payload.import_scope,
            "board_edit_operation": payload.operation,
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
        board_decision=BoardDecision(
            action="append_section" if payload.operation == "append_section" else "edit_board",
            reason="已按用户要求把上传资料导入黑板。",
        ),
        resource_matches=resource_resolution.matches,
        selected_reference=payload.selected_reference or resource_resolution.selected_reference,
    )


def _document_evidence_resource_summary(evidence: DocumentEvidence) -> str:
    path = " / ".join(evidence.heading_path) if evidence.heading_path else evidence.resource_name
    page = f"PDF 实际页 {evidence.page_range}" if evidence.page_range else "无页码"
    printed = f"，印刷页 {evidence.printed_page_range}" if evidence.printed_page_range else ""
    return (
        f"资料证据：{evidence.resource_name} / {path}\n"
        f"位置：{page}{printed}\n"
        f"定位置信度：{round(evidence.confidence * 100)}%\n"
        f"正文：{_compact_text(evidence.full_text or evidence.excerpt, limit=3200)}"
    )


def _format_document_evidence_message(evidence: list[DocumentEvidence]) -> str:
    if not evidence:
        return "我还没有找到可用的正文证据。"
    lines = ["我已经按资料索引定位到下面的正文证据："]
    for index, item in enumerate(evidence, start=1):
        path = " / ".join(item.heading_path) if item.heading_path else item.resource_name
        page = f"实际页 {item.page_range}" if item.page_range else "无页码"
        printed = f"，印刷页 {item.printed_page_range}" if item.printed_page_range else ""
        lines.extend(
            [
                "",
                f"{index}. {item.resource_name} / {path}",
                f"位置：{page}{printed}；置信度 {round(item.confidence * 100)}%。",
                f"摘录：{item.excerpt}",
            ]
        )
        if item.trace:
            lines.append("定位过程：" + " ".join(item.trace[:3]))
    lines.append("")
    lines.append("你可以在证据卡里预览原页，也可以把原文插入板书，或让 AI 参考这段证据生成板书。")
    return "\n".join(lines)


def _handle_document_evidence_action(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
) -> ChatResponse | None:
    if request.document_evidence_action is None or not request.document_evidence_id:
        return None

    evidence = document_evidence_from_id(
        workspace_state.get_store().path,
        resources=resources,
        evidence_id=request.document_evidence_id,
    )
    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    if evidence is None:
        chatbot_message = "这条资料证据已经失效或资料索引已更新，请重新让我定位一次。"
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="资料证据 ID 已失效。"),
        )

    if request.document_evidence_action == "insert_original":
        existing_text = lesson.board_document.content_text.strip()
        heading = " / ".join(evidence.heading_path) if evidence.heading_path else evidence.resource_name
        inserted_text = f"{heading}\n\n{evidence.full_text or evidence.excerpt}".strip()
        next_text = "\n\n".join(part for part in [existing_text, inserted_text] if part)
        new_document = build_document(
            title=lesson.board_document.title or lesson.title,
            content_text=next_text,
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
        refresh_lesson_runtime(lesson, document=new_document, requirements=lesson.learning_requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        chatbot_message = f"已把“{evidence.resource_name}”中定位到的原文插入板书。"
        commit_operations(
            lesson,
            [],
            label="Document evidence insert",
            message="Inserted located document evidence into the board",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_DOCUMENT_EVIDENCE_INSERT,
                "document_evidence": evidence.model_dump(mode="json", exclude={"full_text"}),
                "assistant_message": chatbot_message,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
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
            board_decision=BoardDecision(action="append_section", reason="已插入资料原文。"),
            document_evidence=[evidence],
        )

    task_requirements = _with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message or f"参考资料证据生成板书：{evidence.resource_name}",
    )
    edit_outcome = generate_from_requirements(
        lesson=lesson,
        requirements=task_requirements,
        clarification=learning_clarification,
        resource_summary=_document_evidence_resource_summary(evidence),
        conversation_summary=_conversation_summary(request.conversation),
        user_instruction=request.message,
    )
    chatbot_message = edit_outcome.chatbot_message
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=task_requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
    commit_operations(
        lesson,
        [],
        label="Document evidence generation",
        message="Generated board document from located document evidence",
        new_document=lesson.board_document,
        metadata={
            "kind": COMMIT_KIND_DOCUMENT_EVIDENCE_GENERATION,
            "document_evidence": evidence.model_dump(mode="json", exclude={"full_text"}),
            "assistant_message": chatbot_message,
            "board_edit_operation": edit_outcome.operation,
            **_task_metadata(
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=edit_outcome.changed,
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
        requirements=task_requirements,
        learning_clarification=learning_clarification,
        board_decision=edit_outcome.board_decision,
        document_evidence=[evidence],
        requirement_cleared=edit_outcome.changed,
    )


def _handle_document_lookup_request(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
) -> ChatResponse | None:
    if request.interaction_mode == "direct_edit" or request.board_generation_action == "start":
        return None
    if requests_resource_document_import(request.message, resources=resources):
        return None
    if not resources or not looks_like_document_request(request.message):
        return None

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    evidence = locate_document_evidence(
        workspace_state.get_store().path,
        resources=resources,
        query=request.message,
    )
    if evidence:
        chatbot_message = _format_document_evidence_message(evidence)
        commit_operations(
            lesson,
            [],
            label="Document evidence lookup",
            message="Located uploaded document evidence",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_DOCUMENT_EVIDENCE_LOOKUP,
                "document_evidence": [item.model_dump(mode="json", exclude={"full_text"}) for item in evidence],
                "assistant_message": chatbot_message,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
            },
        )
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="已定位资料正文证据，等待用户选择是否写入板书。"),
            document_evidence=evidence,
        )

    pending_message = queued_resource_message(resources)
    if pending_message:
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=pending_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="资料索引尚未就绪。"),
        )
    return None


def _generate_interaction_chatbot_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    session: InteractionSession,
    decision: InteractionTurnDecision | None,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=session.interaction_goal or requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=request.message,
        selection_excerpt=session.reference_context,
        interaction_mode="interaction_rule",
        interaction_context=interaction_context_payload(session=session, decision=decision),
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot_interaction" if chatbot_message else "chatbot_empty"


def _handle_existing_interaction_session(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
) -> ChatResponse | None:
    session_before = lesson.active_interaction_session
    if session_before is None:
        return None

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    decision = decide_interaction_turn(
        lesson=lesson,
        session=session_before,
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=selection_excerpt,
    )
    if decision is None:
        chatbot_message = ""
        lesson.active_interaction_session = session_before
        commit_operations(
            lesson,
            [],
            label="Interaction turn",
            message="Recorded an interaction-rule turn without a route decision",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_INTERACTION_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": "interaction_decision_empty",
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=session_before, after=session_before),
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
            board_decision=BoardDecision(action="no_change", reason=""),
        )

    session_after = apply_interaction_decision(session_before, decision)
    reply_session = session_after or session_before
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source = _generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=reply_session,
        decision=decision,
    )
    commit_operations(
        lesson,
        [],
        label="Interaction turn",
        message="Recorded an interaction-rule chat turn",
        new_document=lesson.board_document,
        metadata={
            "kind": COMMIT_KIND_INTERACTION_FLOW,
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
            **interaction_session_metadata(before=session_before, after=session_after, decision=decision),
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
    )


def _maybe_start_interaction_session(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_text: str | None,
    visible_chatbot_message: str | None = None,
    visible_chatbot_message_source: str | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> ChatResponse | None:
    if request.interaction_mode == "direct_edit":
        return None
    if not should_start_interaction(requirements.interaction_rule_draft):
        return None

    start_resolution = build_interaction_start(
        lesson=lesson,
        draft=requirements.interaction_rule_draft,
        user_message=request.message,
        selection=request.selection,
        selection_text=selection_text,
    )
    if start_resolution.session is None and start_resolution.focus_resolution is not None:
        if visible_chatbot_message is not None:
            return None
        chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            resolution=start_resolution.focus_resolution,
        )
        lesson.learning_requirements = requirements
        commit_operations(
            lesson,
            [],
            label="Interaction focus clarification",
            message="Asked the learner to confirm the source content for an interaction rule",
            new_document=lesson.board_document,
            metadata={
                "kind": COMMIT_KIND_INTERACTION_FLOW,
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=None,
                    focus_candidates=start_resolution.focus_resolution.candidates,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=None, after=None),
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
                action="await_focus_choice",
                reason=start_resolution.focus_resolution.question,
            ),
            focus_candidates=start_resolution.focus_resolution.candidates,
        )

    if start_resolution.session is None:
        return None

    session_before = lesson.active_interaction_session
    lesson.active_interaction_session = start_resolution.session
    if visible_chatbot_message is None:
        chatbot_message, chatbot_message_source = _generate_interaction_chatbot_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=start_resolution.session,
            decision=None,
        )
    else:
        chatbot_message = visible_chatbot_message
        chatbot_message_source = visible_chatbot_message_source or "chatbot"
    _clear_task_requirements(lesson)
    commit_operations(
        lesson,
        [],
        label="Interaction session start",
        message="Started a rule-based interaction session",
        new_document=lesson.board_document,
        metadata={
            "kind": COMMIT_KIND_INTERACTION_FLOW,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=start_resolution.session.target_focus,
                focus_candidates=(
                    start_resolution.focus_resolution.candidates
                    if start_resolution.focus_resolution
                    else []
                ),
                requirement_cleared=True,
            ),
            **interaction_session_metadata(
                before=session_before,
                after=start_resolution.session,
            ),
            **(extra_metadata or {}),
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
            action="no_change",
            reason=start_resolution.session.interaction_goal,
        ),
        resolved_focus=start_resolution.session.target_focus,
        focus_candidates=(
            start_resolution.focus_resolution.candidates
            if start_resolution.focus_resolution
            else []
        ),
        requirement_cleared=True,
    )


def _generate_board_from_confirmed_resource(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary_for_turn: str,
    conversation_summary: str,
) -> ChatResponse:
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
        conversation_summary=conversation_summary,
        user_instruction=request.message,
    )
    chatbot_message = edit_outcome.chatbot_message
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        requirements = lesson.learning_requirements
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
    requirement_cleared = edit_outcome.changed
    commit_operations(
        lesson,
        [],
        label="Resource-backed board generation",
        message="Generated board document from a confirmed uploaded resource chapter",
        new_document=lesson.board_document,
        metadata={
            "kind": COMMIT_KIND_BOARD_DOCUMENT_GENERATION,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": edit_outcome.assistant_message_source,
            "interaction_mode": request.interaction_mode,
            "resource_reference_action": request.resource_reference_action,
            "board_generation_action": "resource_reference_confirm",
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_resource_generation_metadata(resource_resolution.selected_reference),
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
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=edit_outcome.board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=resource_resolution.selected_reference,
        requirement_cleared=requirement_cleared,
    )
