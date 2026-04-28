from __future__ import annotations

from app.models import BoardDecision
from app.services.ai_workflow import (
    WorkflowState,
    _available_reference_resources,
    _build_reference_prompt,
    _build_board_edit_prompt,
    _build_scope_options,
    _fallback_board_decision,
    _is_append_document_request,
    _is_board_generation_request,
    _is_explicit_board_edit_request,
    _is_forced_start_request,
    _extract_requested_outline_reference,
    _reference_context_for_match,
    _resource_file_clarification_question,
    _selected_reference_context,
    _should_auto_attach_reference_for_direct_teaching,
    _should_clarify_resource_file,
    _should_offer_board_edit_prompt,
    _should_use_fast_board_path,
    match_resources,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import is_document_empty


def run_board_manager(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    matches = match_resources(state["course_package"], lesson, request, requirements)
    resources = _available_reference_resources(state["course_package"], lesson)
    top_match = matches[0] if matches else None
    second_match = matches[1] if len(matches) > 1 else None

    if request.interaction_mode == "direct_edit":
        if _is_append_document_request(request.message) and not is_document_empty(lesson.board_document):
            return {
                "board_decision": BoardDecision(action="append_section", reason="用户要求在现有讲义后新增页面或章节内容。"),
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
            }
        return {
            "board_decision": BoardDecision(action="edit_board", reason="用户通过选区编辑入口明确要求直接修改讲义。"),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
        }

    if state.get("needs_clarification"):
        return {
            "board_decision": BoardDecision(action="clarify_request", reason=state.get("pm_reason", "当前需求仍需要继续澄清。")),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
        }

    if request.board_edit_action == "confirm":
        ai_decision = openai_course_ai.generate_board_decision(
            lesson_title=lesson.title,
            request_message=request.message,
            selection=request.selection.model_dump(mode="json") if request.selection else None,
            interaction_mode=request.interaction_mode,
            scope_action=request.scope_action,
            board_edit_action=request.board_edit_action,
            requirements=requirements,
            document=lesson.board_document,
            resource_matches=[match.model_dump(mode="json") for match in matches],
        )
        fallback_decision = _fallback_board_decision(lesson, request, requirements, matches)
        decision = ai_decision if ai_decision and ai_decision.action in {"edit_board", "append_section", "create_new_lesson"} else fallback_decision
    elif _should_use_fast_board_path(lesson=lesson, request=request, requirements=requirements):
        decision = _fallback_board_decision(lesson, request, requirements, matches)
    else:
        ai_decision = openai_course_ai.generate_board_decision(
            lesson_title=lesson.title,
            request_message=request.message,
            selection=request.selection.model_dump(mode="json") if request.selection else None,
            interaction_mode=request.interaction_mode,
            scope_action=request.scope_action,
            board_edit_action=request.board_edit_action,
            requirements=requirements,
            document=lesson.board_document,
            resource_matches=[match.model_dump(mode="json") for match in matches],
        )
        decision = ai_decision or _fallback_board_decision(lesson, request, requirements, matches)

    if request.board_edit_action == "skip":
        decision = BoardDecision(action="no_change", reason="用户选择暂不把这次内容扩选到版书。")
    elif (
        is_document_empty(lesson.board_document)
        and top_match is not None
        and request.board_edit_action is None
        and (
            _is_board_generation_request(request.message)
            or _is_forced_start_request(request.message)
            or _extract_requested_outline_reference(request.message)[0] is not None
        )
    ):
        decision = BoardDecision(action="edit_board", reason="当前版书为空，且用户已指向上传资料章节，直接生成初始版书。")
    elif is_document_empty(lesson.board_document) and _is_board_generation_request(request.message):
        decision = BoardDecision(action="edit_board", reason="用户明确要求生成讲义/板书，当前讲义为空，直接生成可写入版本。")
    elif _is_append_document_request(request.message):
        decision = BoardDecision(action="append_section", reason="用户要求在现有讲义后新增页面或章节内容。")
    elif decision.action == "no_change" and _is_board_generation_request(request.message):
        decision = BoardDecision(action="edit_board", reason="用户明确要求生成讲义/对话内容，应直接产出文档。")
    elif decision.action in {"edit_board", "append_section", "create_new_lesson"} and not _is_explicit_board_edit_request(request.message) and request.board_edit_action != "confirm":
        decision = BoardDecision(action="no_change", reason="普通追问默认先生成内部讲义讲解，不直接改动版书。")

    if decision.action == "await_scope_choice":
        return {
            "board_decision": decision,
            "scope_options": _build_scope_options(matches),
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
        }

    ambiguous_reference = (
        top_match is not None
        and second_match is not None
        and top_match.resource_id != second_match.resource_id
        and top_match.is_high_overlap
        and abs(top_match.score - second_match.score) <= 0.06
    )
    if request.resource_reference_action is None and decision.action in {"edit_board", "append_section", "create_new_lesson", "no_change"}:
        if _should_clarify_resource_file(resources=resources, matches=matches, request=request):
            return {
                "board_decision": BoardDecision(
                    action="clarify_request",
                    reason="这次追问指向上传资料，但当前课程里有多份资料，需先确认具体文件。",
                ),
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "needs_clarification": True,
                "clarification_questions": [_resource_file_clarification_question(resources, matches)],
            }
        if _should_auto_attach_reference_for_direct_teaching(
            request=request,
            decision=decision,
            top_match=top_match,
        ) or (
            decision.action == "no_change"
            and top_match is not None
            and len(resources) == 1
            and not ambiguous_reference
            and is_document_empty(lesson.board_document)
        ) or (
            request.board_edit_action == "confirm"
            and top_match is not None
            and len(resources) == 1
            and not ambiguous_reference
        ):
            auto_reference = _reference_context_for_match(
                state["course_package"],
                lesson,
                request,
                requirements,
                top_match,
            )
            if auto_reference is None:
                return {
                    "board_decision": BoardDecision(
                        action="clarify_request",
                        reason="用户明确要讲资料章节，但目标文件页无法提取出可引用正文。",
                    ),
                    "scope_options": [],
                    "resource_matches": matches,
                    "reference_prompt": None,
                    "board_edit_prompt": None,
                    "selected_reference": None,
                    "needs_clarification": True,
                    "clarification_questions": ["这份资料的目标页没有抽到可读正文。请换成可复制文字的 PDF，或上传对应页截图/文本，我再按原文讲。"],
                }
            board_edit_prompt = (
                _build_board_edit_prompt(lesson=lesson, request=request, requirements=requirements, matches=matches)
                if _should_offer_board_edit_prompt(lesson=lesson, request=request, requirements=requirements, decision=decision)
                else None
            )
            return {
                "board_decision": decision,
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": None,
                "board_edit_prompt": board_edit_prompt,
                "selected_reference": auto_reference,
            }
        if decision.action != "no_change" and top_match is not None and (ambiguous_reference or top_match.is_high_overlap):
            return {
                "board_decision": BoardDecision(
                    action="await_reference_choice",
                    reason="当前板书或请求和资料目录里的章节高度相关，先确认是否参考资料正文再生成。",
                ),
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": _build_reference_prompt(top_match),
                "board_edit_prompt": None,
                "selected_reference": None,
            }

    confirmed_reference = _selected_reference_context(state["course_package"], lesson, request, requirements)
    if request.resource_reference_action == "confirm" and confirmed_reference is None:
        return {
            "board_decision": BoardDecision(
                action="clarify_request",
                reason="用户确认了参考资料，但当前文件页无法提取出可引用正文，不能继续补写。",
            ),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "needs_clarification": True,
            "clarification_questions": ["这份资料的目标页没有抽到可读正文。请换成可复制文字的 PDF，或上传对应页截图/文本，我再按原文讲。"],
        }
    board_edit_prompt = (
        _build_board_edit_prompt(lesson=lesson, request=request, requirements=requirements, matches=matches)
        if _should_offer_board_edit_prompt(lesson=lesson, request=request, requirements=requirements, decision=decision)
        else None
    )

    return {
        "board_decision": decision,
        "scope_options": [],
        "resource_matches": matches,
        "reference_prompt": None,
        "board_edit_prompt": board_edit_prompt,
        "selected_reference": confirmed_reference,
    }
