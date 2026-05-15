from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardEditPrompt,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ScopeOption,
    SectionTeachingProgressView,
)
from app.services.course_runtime import effective_requirements, refresh_lesson_runtime
from app.services.fallback_generator import reference_document_fallback_html
from app.services.resource_library import extract_reference_context
from app.services.rich_document import (
    append_html_section,
    build_document,
    document_changed,
    html_to_text,
    is_document_empty,
    replace_selection_in_document,
)


_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{1,}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_SPACE_RE = re.compile(r"\s+")
_LOW_SUBSTANCE_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "继续",
    "继续讲",
    "继续下一节",
    "下一节",
}


@dataclass
class WorkflowResult:
    teacher_message: str
    learning_requirement_sheet: LearningRequirementSheet
    learning_clarification: LearningClarificationStatus
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = field(default_factory=list)
    patch_proposal: None = None
    scope_options: list[ScopeOption] = field(default_factory=list)
    resource_matches: list[ResourceMatch] = field(default_factory=list)
    reference_prompt: ResourceReferencePrompt | None = None
    board_edit_prompt: BoardEditPrompt | None = None
    selected_reference: ResourceReferenceContext | None = None
    teaching_progress: SectionTeachingProgressView | None = None
    document_changed: bool = False
    commit_label: str | None = None
    commit_message: str | None = None
    commit_metadata: dict[str, object] = field(default_factory=dict)


def _compact(value: str, *, limit: int = 160) -> str:
    text = _SPACE_RE.sub(" ", value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _tokens(text: str) -> set[str]:
    lowered = (text or "").lower()
    tokens = {match.group(0) for match in _WORD_RE.finditer(lowered)}
    for match in _CJK_RE.finditer(lowered):
        run = match.group(0)
        if 1 < len(run) <= 12:
            tokens.add(run)
        for size in (2, 3):
            for index in range(0, max(0, len(run) - size + 1)):
                tokens.add(run[index : index + size])
    return {token for token in tokens if len(token.strip()) > 1}


def _text_score(query: str, candidate: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    candidate_tokens = _tokens(candidate)
    if not candidate_tokens:
        return 0.0
    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0
    query_coverage = len(overlap) / len(query_tokens)
    candidate_coverage = len(overlap) / max(3, len(candidate_tokens))
    compact_query = _compact(query, limit=80).lower()
    compact_candidate = _compact(candidate, limit=500).lower()
    exact_boost = 0.18 if compact_query and compact_query in compact_candidate else 0.0
    return min(1.0, query_coverage * 0.78 + candidate_coverage * 0.22 + exact_boost)


def _dedupe(items: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        compact = _compact(item, limit=220)
        key = compact.lower()
        if not compact or key in seen:
            continue
        seen.add(key)
        result.append(compact)
        if len(result) >= limit:
            break
    return result


def _message_topic(lesson: Lesson, request: ChatRequest) -> str:
    message = _compact(request.message, limit=80)
    if message:
        return message
    return lesson.title


def _is_low_substance_message(message: str) -> bool:
    normalized = _SPACE_RE.sub("", (message or "").strip().lower())
    if not normalized:
        return True
    return normalized in _LOW_SUBSTANCE_MESSAGES


def _update_requirements(lesson: Lesson, request: ChatRequest) -> LearningRequirementSheet:
    base = effective_requirements(lesson)
    topic = _message_topic(lesson, request)
    selected = _compact(request.selection.excerpt, limit=120) if request.selection else ""

    current_questions = _dedupe(
        [
            request.message,
            *base.current_questions,
        ],
        limit=8,
    )
    checklist_seed = [*base.learning_need_checklist]
    if not _is_low_substance_message(topic):
        checklist_seed.append(topic)
    if selected:
        checklist_seed.append(f"结合用户选中的板书片段：{selected}")
    if request.resource_reference_action == "confirm":
        checklist_seed.append("结合已确认的参考资料章节更新板书与讲解")
    if request.interaction_mode == "direct_edit":
        checklist_seed.append("按用户指令直接编辑当前板书")

    return LearningRequirementSheet(
        theme=lesson.title,
        learning_goal=topic if not _is_low_substance_message(topic) else "等待具体学习主题",
        level=base.level or "根据用户背景和当前资料动态调整",
        known_background=base.known_background or "用户背景由后续互动继续补全",
        current_questions=current_questions or [topic],
        learning_need_checklist=_dedupe(checklist_seed, limit=10),
        target_depth=base.target_depth or "能说清主线、关键关系，并完成一次理解检查",
        output_preference=base.output_preference or "根据请求在讲解、讨论和板书写入之间切换",
        boundary=base.boundary or "围绕当前学习请求和已有资料推进",
        board_scope=base.board_scope,
        success_criteria=base.success_criteria or "用户能复述本轮主线，并指出下一步想深入的位置",
        risk_notes=_dedupe(base.risk_notes, limit=6),
    )


def _clarification_status(
    requirements: LearningRequirementSheet,
    *,
    can_start: bool,
    reason: str,
) -> LearningClarificationStatus:
    checklist_score = min(25, len(requirements.learning_need_checklist) * 4)
    question_score = min(20, len(requirements.current_questions) * 4)
    progress = 45 + checklist_score + question_score + (20 if can_start else 0)
    return LearningClarificationStatus(
        progress=min(100, progress),
        label="可以开始" if can_start else "需要补充",
        reason=reason,
        missing_items=[] if can_start else ["请补充你最想解决的问题或目标"],
        can_start=can_start,
        forced_start=can_start,
    )


def _rank_board_excerpts(document: BoardDocument, query: str, *, limit: int = 4) -> list[tuple[str, float]]:
    lines = [_compact(line, limit=260) for line in document.content_text.splitlines()]
    candidates = [line for line in lines if line]
    scored = [(line, _text_score(query, line)) for line in candidates]
    ranked = [(line, score) for line, score in scored if score >= 0.16]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def _resource_haystack(resource: ResourceLibraryItem, chapter_index: int) -> str:
    chapter = resource.outline[chapter_index]
    return "\n".join(
        [
            resource.name,
            chapter.title,
            chapter.summary,
            " ".join(chapter.path),
            " ".join(chapter.keywords),
            chapter.locator_hint or "",
        ]
    )


def match_resource_chapters(
    resources: list[ResourceLibraryItem],
    query: str,
    *,
    limit: int = 3,
) -> list[ResourceMatch]:
    matches: list[ResourceMatch] = []
    for resource in resources:
        for index, chapter in enumerate(resource.outline):
            score = _text_score(query, _resource_haystack(resource, index))
            if score <= 0:
                continue
            matches.append(
                ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    resource_name=resource.name,
                    chapter_title=chapter.title,
                    reason=f"目录标题、摘要或关键词与当前学习请求有重合：{_compact(query, limit=80)}",
                    score=round(score, 3),
                    is_high_overlap=score >= 0.42,
                )
            )
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[:limit]


def _find_resource(resources: list[ResourceLibraryItem], resource_id: str | None) -> ResourceLibraryItem | None:
    if resource_id is None:
        return None
    return next((resource for resource in resources if resource.id == resource_id), None)


def _reference_from_request(
    resources: list[ResourceLibraryItem],
    request: ChatRequest,
) -> ResourceReferenceContext | None:
    if request.resource_reference_action != "confirm":
        return None
    resource = _find_resource(resources, request.resource_reference_resource_id)
    if resource is None or request.resource_reference_chapter_id is None:
        return None
    return extract_reference_context(
        resource,
        request.resource_reference_chapter_id,
        user_query=request.message,
    )


def _reference_prompt(match: ResourceMatch, request: ChatRequest) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=f"我在资料库里找到“{match.chapter_title}”。是否用这一章节来补全当前板书并继续讲解？",
        reason=match.reason,
        score=match.score,
    )


def _request_section_html(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> str:
    topic = html.escape(_message_topic(lesson, request))
    selected = _compact(request.selection.excerpt, limit=260) if request.selection else ""
    parts = [f"<h1>{topic}</h1>"]
    if selected:
        parts.append(f"<blockquote>{html.escape(selected)}</blockquote>")
    return "\n".join(parts)


def _append_or_replace_document(document: BoardDocument, section_html: str) -> BoardDocument:
    if is_document_empty(document):
        return build_document(
            title=document.title,
            content_html=section_html,
            document_id=document.id,
            page_settings=document.page_settings,
        )
    return append_html_section(document, section_html)


def _reference_section_html(lesson: Lesson, reference_context: ResourceReferenceContext) -> str:
    return reference_document_fallback_html(lesson.title, reference_context)


def _teacher_from_board(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    excerpts: list[tuple[str, float]],
) -> str:
    if excerpts:
        lines = [f"我先根据当前板书里最相关的内容来讲：{_compact(excerpts[0][0], limit=180)}"]
        for excerpt, _score in excerpts[1:3]:
            lines.append(f"同时要连上这一点：{_compact(excerpt, limit=160)}")
        lines.append(f"这一轮的检查标准是：{_compact(requirements.success_criteria, limit=180)}")
        return "\n".join(lines)
    if lesson.board_document.content_text.strip():
        first_line = _compact(lesson.board_document.content_text.splitlines()[0], limit=180)
        return f"当前板书已经有内容，但和这次问题的重合度不高。我会先围绕“{_compact(request.message, limit=80)}”补出一段可讲解的板书，再继续讲。已有板书起点是：{first_line}"
    return f"当前板书还没有可讲的内容。我会先围绕“{_compact(request.message, limit=80)}”建立一段可继续扩展的板书。"


def _teacher_after_board_write(
    requirements: LearningRequirementSheet,
    *,
    reference_context: ResourceReferenceContext | None = None,
) -> str:
    if reference_context is not None:
        points = reference_context.teaching_points[:2]
        point_text = "；".join(_compact(point, limit=120) for point in points if point)
        suffix = f" 这次优先抓住：{point_text}" if point_text else ""
        return (
            f"我已把“{_compact(reference_context.chapter_title, limit=80)}”整理进当前板书，"
            f"接下来会按这段资料和你的目标来讲。{suffix}"
        )
    return (
        "我已先把本轮学习主题记录到板书里。"
        f"接下来会围绕“{_compact(requirements.learning_goal, limit=120)}”继续展开。"
    )


def _empty_board_prompt_message(request: ChatRequest) -> str:
    message = _compact(request.message, limit=80)
    if _is_low_substance_message(message):
        return "当前板书还没有可继续讲的内容。你给我一个具体主题、问题或上传资料，我再开始讲解和写板书。"
    return f"我可以从“{message}”开始，但不会把需求清单当成讲义模板写进板书。你可以让我生成讲义、上传资料，或直接说从零开始讲。"


def _teaching_progress(document: BoardDocument) -> SectionTeachingProgressView | None:
    headings = [
        line.strip()
        for line in document.content_text.splitlines()
        if line.strip() and len(line.strip()) <= 80
    ]
    if not headings:
        return None
    return SectionTeachingProgressView(
        section_index=0,
        section_count=len(headings),
        current_section_title=headings[0],
        has_next_section=len(headings) > 1,
        waiting_for_continue=len(headings) > 1,
    )


class GenericCourseWorkflow:
    def invoke(self, state: dict[str, Any]) -> WorkflowResult:
        lesson = state["lesson"]
        request = state["request"]
        resources = list(state.get("resources") or [])

        if not isinstance(lesson, Lesson) or not isinstance(request, ChatRequest):
            raise TypeError("GenericCourseWorkflow requires a Lesson and ChatRequest")

        requirements = _update_requirements(lesson, request)
        query = request.message
        resource_matches = match_resource_chapters(resources, query)
        selected_reference = _reference_from_request(resources, request)

        if request.resource_reference_action == "skip":
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message=_teacher_from_board(lesson, request, requirements, []),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=True, reason="用户选择暂不引用推荐资料，继续用当前板书和需求清单推进。"),
                board_decision=BoardDecision(action="no_change", reason="已跳过资料引用，本轮不改动板书。"),
                resource_matches=resource_matches,
                teaching_progress=_teaching_progress(lesson.board_document),
            )

        if selected_reference is not None:
            before = lesson.board_document
            next_document = _append_or_replace_document(before, _reference_section_html(lesson, selected_reference))
            refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
            changed = document_changed(before, lesson.board_document)
            return WorkflowResult(
                teacher_message=_teacher_after_board_write(lesson.learning_requirements or requirements, reference_context=selected_reference),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=True, reason="已确认参考资料章节，可以据此更新板书并开始讲解。"),
                board_decision=BoardDecision(action="edit_board" if is_document_empty(before) else "append_section", reason="使用用户确认的资料章节补全当前板书。"),
                resource_matches=resource_matches,
                selected_reference=selected_reference,
                teaching_progress=_teaching_progress(lesson.board_document),
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
            replacement = _request_section_html(lesson, request, requirements)
            if request.selection and request.selection.excerpt.strip():
                next_document = replace_selection_in_document(
                    before,
                    selection_text=request.selection.excerpt,
                    replacement_text=html_to_text(replacement),
                    replacement_html=replacement,
                )
            else:
                next_document = _append_or_replace_document(before, replacement)
            refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
            changed = document_changed(before, lesson.board_document)
            return WorkflowResult(
                teacher_message="已按你的指令更新当前板书。你可以继续让我讲解、重写或扩展任意一段。",
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=True, reason="用户明确要求直接编辑板书。"),
                board_decision=BoardDecision(action="edit_board", reason="本轮是直接编辑模式，按用户指令写入当前板书。"),
                resource_matches=resource_matches,
                teaching_progress=_teaching_progress(lesson.board_document),
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
                learning_clarification=_clarification_status(requirements, can_start=True, reason="用户选择暂不扩展板书。"),
                board_decision=BoardDecision(action="no_change", reason="用户跳过了板书扩展确认。"),
                resource_matches=resource_matches,
                teaching_progress=_teaching_progress(lesson.board_document),
            )

        if request.board_edit_action == "confirm":
            before = lesson.board_document
            next_document = _append_or_replace_document(before, _request_section_html(lesson, request, requirements))
            refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
            changed = document_changed(before, lesson.board_document)
            return WorkflowResult(
                teacher_message=_teacher_after_board_write(lesson.learning_requirements or requirements),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=True, reason="用户确认扩展板书。"),
                board_decision=BoardDecision(action="append_section", reason="用户确认后，将学习需求写入当前板书。"),
                resource_matches=resource_matches,
                teaching_progress=_teaching_progress(lesson.board_document),
                document_changed=changed,
                commit_label="Confirmed board expansion",
                commit_message="Expanded board from a confirmed workflow prompt",
                commit_metadata={"kind": "workflow_confirmed_board_expansion"},
            )

        board_excerpts = _rank_board_excerpts(lesson.board_document, query)
        if board_excerpts:
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message=_teacher_from_board(lesson, request, requirements, board_excerpts),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=True, reason="当前板书已有可支撑讲解的相关内容。"),
                board_decision=BoardDecision(action="no_change", reason="当前板书已经包含相关内容，本轮先讲解不改动。"),
                resource_matches=resource_matches,
                teaching_progress=_teaching_progress(lesson.board_document),
            )

        if resource_matches:
            refresh_lesson_runtime(lesson, requirements=requirements)
            top_match = resource_matches[0]
            return WorkflowResult(
                teacher_message=f"我找到了一个可能相关的资料章节：{top_match.resource_name} / {top_match.chapter_title}。确认后我会用它补全板书并继续讲解。",
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=True, reason="资料目录库中找到了可引用的候选章节。"),
                board_decision=BoardDecision(action="await_reference_choice", reason="当前板书内容不足，先等待用户确认是否引用匹配资料。"),
                resource_matches=resource_matches,
                reference_prompt=_reference_prompt(top_match, request),
                teaching_progress=_teaching_progress(lesson.board_document),
            )

        if is_document_empty(lesson.board_document) and _is_low_substance_message(request.message):
            refresh_lesson_runtime(lesson, requirements=requirements)
            return WorkflowResult(
                teacher_message=_empty_board_prompt_message(request),
                learning_requirement_sheet=lesson.learning_requirements or requirements,
                learning_clarification=_clarification_status(requirements, can_start=False, reason="当前输入还不足以生成真实板书内容。"),
                board_decision=BoardDecision(action="no_change", reason="空板书上不把低信息量输入渲染成模板内容。"),
                resource_matches=resource_matches,
                teaching_progress=None,
            )

        before = lesson.board_document
        next_document = _append_or_replace_document(before, _request_section_html(lesson, request, requirements))
        refresh_lesson_runtime(lesson, document=next_document, requirements=requirements)
        changed = document_changed(before, lesson.board_document)
        return WorkflowResult(
            teacher_message=_teacher_after_board_write(lesson.learning_requirements or requirements),
            learning_requirement_sheet=lesson.learning_requirements or requirements,
            learning_clarification=_clarification_status(requirements, can_start=True, reason="当前板书和资料库都不足以直接讲解，已先建立通用板书入口。"),
            board_decision=BoardDecision(action="edit_board" if is_document_empty(before) else "append_section", reason="将本轮学习请求写入板书，作为后续讲解和追问的共同上下文。"),
            resource_matches=resource_matches,
            teaching_progress=_teaching_progress(lesson.board_document),
            document_changed=changed,
            commit_label="Workflow board update",
            commit_message="Recorded a learning topic from the integrated workflow",
            commit_metadata={"kind": "workflow_topic_board_entry"},
        )


course_workflow = GenericCourseWorkflow()
