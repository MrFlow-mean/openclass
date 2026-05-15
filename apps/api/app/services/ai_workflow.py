from __future__ import annotations

from typing import Any

from app.models import BoardDecision, ChatRequest, Lesson
from app.services.course_runtime import refresh_lesson_runtime
from app.services.rich_document import (
    document_changed,
    html_to_text,
    is_document_empty,
    replace_selection_in_document,
)
from app.services.workflow_roles.board import (
    append_or_replace_document,
    reference_section_html,
    request_section_html,
)
from app.services.workflow_roles.materials import (
    match_resource_chapters,
    reference_from_request,
    reference_prompt as build_reference_prompt,
)
from app.services.workflow_roles.pm import clarification_status, update_requirements
from app.services.workflow_roles.shared import WorkflowResult, is_low_substance_message
from app.services.workflow_roles.teacher import (
    empty_board_prompt_message,
    rank_board_excerpts,
    teacher_after_board_write,
    teacher_from_board,
    teaching_progress,
)


class GenericCourseWorkflow:
    def invoke(self, state: dict[str, Any]) -> WorkflowResult:
        lesson = state["lesson"]
        request = state["request"]
        resources = list(state.get("resources") or [])

        if not isinstance(lesson, Lesson) or not isinstance(request, ChatRequest):
            raise TypeError("GenericCourseWorkflow requires a Lesson and ChatRequest")

        requirements = update_requirements(lesson, request)
        query = request.message
        resource_matches = match_resource_chapters(resources, query)
        selected_reference = reference_from_request(resources, request)

        if request.resource_reference_action == "skip":
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message=teacher_from_board(lesson, request, requirements, []),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="用户选择暂不引用推荐资料，继续用当前板书和需求清单推进。",
                ),
                board_decision=BoardDecision(action="no_change", reason="已跳过资料引用，本轮不改动板书。"),
                resource_matches=resource_matches,
                teaching_progress=teaching_progress(lesson.board_document),
            )

        if selected_reference is not None:
            before = lesson.board_document
            next_document = append_or_replace_document(
                before,
                reference_section_html(lesson, selected_reference),
            )
            refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
            changed = document_changed(before, lesson.board_document)
            return WorkflowResult(
                teacher_message=teacher_after_board_write(
                    lesson.learning_requirements or requirements,
                    reference_context=selected_reference,
                ),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="已确认参考资料章节，可以据此更新板书并开始讲解。",
                ),
                board_decision=BoardDecision(
                    action="edit_board" if is_document_empty(before) else "append_section",
                    reason="使用用户确认的资料章节补全当前板书。",
                ),
                resource_matches=resource_matches,
                selected_reference=selected_reference,
                teaching_progress=teaching_progress(lesson.board_document),
                document_changed=changed,
                commit_label="Reference-based board update",
                commit_message="Updated board from a confirmed resource chapter",
                commit_metadata={
                    "kind": "workflow_reference_board_update",
                    "resource_id": selected_reference.resource_id,
                    "chapter_id": selected_reference.chapter_id,
                },
            )

        if request.interaction_mode == "direct_edit":
            before = lesson.board_document
            replacement = request_section_html(lesson, request, requirements)
            if request.selection and request.selection.excerpt.strip():
                next_document = replace_selection_in_document(
                    before,
                    selection_text=request.selection.excerpt,
                    replacement_text=html_to_text(replacement),
                    replacement_html=replacement,
                )
            else:
                next_document = append_or_replace_document(before, replacement)
            refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
            changed = document_changed(before, lesson.board_document)
            return WorkflowResult(
                teacher_message="已按你的指令更新当前板书。你可以继续让我讲解、重写或扩展任意一段。",
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="用户明确要求直接编辑板书。",
                ),
                board_decision=BoardDecision(
                    action="edit_board",
                    reason="本轮是直接编辑模式，按用户指令写入当前板书。",
                ),
                resource_matches=resource_matches,
                teaching_progress=teaching_progress(lesson.board_document),
                document_changed=changed,
                commit_label="Direct board edit",
                commit_message="Updated board from a direct edit request",
                commit_metadata={"kind": "workflow_direct_board_edit"},
            )

        if request.board_edit_action == "skip":
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message="好，本轮先不扩展板书。我会只围绕当前问题做口头讲解，后面你再决定是否写入。",
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="用户选择暂不扩展板书。",
                ),
                board_decision=BoardDecision(action="no_change", reason="用户跳过了板书扩展确认。"),
                resource_matches=resource_matches,
                teaching_progress=teaching_progress(lesson.board_document),
            )

        if request.board_edit_action == "confirm":
            before = lesson.board_document
            next_document = append_or_replace_document(
                before,
                request_section_html(lesson, request, requirements),
            )
            refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
            changed = document_changed(before, lesson.board_document)
            return WorkflowResult(
                teacher_message=teacher_after_board_write(lesson.learning_requirements or requirements),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="用户确认扩展板书。",
                ),
                board_decision=BoardDecision(
                    action="append_section",
                    reason="用户确认后，将学习需求写入当前板书。",
                ),
                resource_matches=resource_matches,
                teaching_progress=teaching_progress(lesson.board_document),
                document_changed=changed,
                commit_label="Confirmed board expansion",
                commit_message="Expanded board from a confirmed workflow prompt",
                commit_metadata={"kind": "workflow_confirmed_board_expansion"},
            )

        board_excerpts = rank_board_excerpts(lesson.board_document, query)
        if board_excerpts:
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message=teacher_from_board(lesson, request, requirements, board_excerpts),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="当前板书已有可支撑讲解的相关内容。",
                ),
                board_decision=BoardDecision(
                    action="no_change",
                    reason="当前板书已经包含相关内容，本轮先讲解不改动。",
                ),
                resource_matches=resource_matches,
                teaching_progress=teaching_progress(lesson.board_document),
            )

        if resource_matches:
            refresh_lesson_runtime(lesson, requirements=requirements)
            top_match = resource_matches[0]
            return WorkflowResult(
                teacher_message=f"我找到了一个可能相关的资料章节：{top_match.resource_name} / {top_match.chapter_title}。确认后我会用它补全板书并继续讲解。",
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=True,
                    reason="资料目录库中找到了可引用的候选章节。",
                ),
                board_decision=BoardDecision(
                    action="await_reference_choice",
                    reason="当前板书内容不足，先等待用户确认是否引用匹配资料。",
                ),
                resource_matches=resource_matches,
                reference_prompt=build_reference_prompt(top_match, request),
                teaching_progress=teaching_progress(lesson.board_document),
            )

        if is_document_empty(lesson.board_document) and is_low_substance_message(request.message):
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message=empty_board_prompt_message(request),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=clarification_status(
                    requirements,
                    can_start=False,
                    reason="当前输入还不足以生成真实板书内容。",
                ),
                board_decision=BoardDecision(
                    action="no_change",
                    reason="空板书上不把低信息量输入渲染成模板内容。",
                ),
                resource_matches=resource_matches,
                teaching_progress=None,
            )

        before = lesson.board_document
        next_document = append_or_replace_document(
            before,
            request_section_html(lesson, request, requirements),
        )
        refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
        changed = document_changed(before, lesson.board_document)
        return WorkflowResult(
            teacher_message=teacher_after_board_write(lesson.learning_requirements or requirements),
            learning_requirement_sheet=lesson.learning_requirements or requirements,
            learning_clarification=clarification_status(
                requirements,
                can_start=True,
                reason="当前板书和资料库都不足以直接讲解，已先建立通用板书入口。",
            ),
            board_decision=BoardDecision(
                action="edit_board" if is_document_empty(before) else "append_section",
                reason="将本轮学习请求写入板书，作为后续讲解和追问的共同上下文。",
            ),
            resource_matches=resource_matches,
            teaching_progress=teaching_progress(lesson.board_document),
            document_changed=changed,
            commit_label="Workflow board update",
            commit_message="Recorded a learning topic from the integrated workflow",
            commit_metadata={"kind": "workflow_topic_board_entry"},
        )


course_workflow = GenericCourseWorkflow()
