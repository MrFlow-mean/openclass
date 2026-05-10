from __future__ import annotations

import re

from app.models import BoardDecision, BoardDocument
from app.services.chart_generation import augment_document_with_generated_charts
from app.services.ai_workflow import (
    WorkflowState,
    _append_section_topic,
    _board_h2_sections,
    _bound_board_teaching_guide,
    _document_edit_has_content,
    _extract_focus_terms,
    _failed_document_generation_result,
    _is_append_document_request,
    _is_section_followup_learning_need,
    _interactive_teaching_guide,
    _is_full_rewrite_request,
    _is_in_place_expansion_request,
    _merge_selection_edit,
    _reference_payload,
    _resolve_board_teaching_guide,
)
from app.services.course_runtime import build_lesson_for_topic
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import (
    append_html_section,
    build_document,
    document_changed,
    html_to_text,
    replace_selection_in_document,
    text_to_html,
)


_LOW_VALUE_APPEND_MARKERS = (
    "用户当前追问",
    "当前追问",
    "原有主线",
    "新问题接回",
    "承接用户",
    "专门承接",
)

_APPEND_INSTRUCTION_ECHOES = (
    "续写一个新章节",
    "续写新章节",
    "新增一个新章节",
    "新增章节",
    "追加一个新章节",
    "补充一个新章节",
)
_MIN_CHAPTER_APPEND_COMPACT_CHARS = 900
_MIN_EXPLICIT_LARGE_CHAPTER_APPEND_COMPACT_CHARS = 1200


def _compact_for_echo(value: str) -> str:
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’]+", "", value or "")


def _is_chapter_append_request(message: str) -> bool:
    compact = _compact_for_echo(message)
    return _is_append_document_request(message) and any(target in compact for target in ("章节", "新章节", "一节", "几节"))


def _chapter_append_min_compact_chars(request_message: str, document: BoardDocument) -> int:
    compact = _compact_for_echo(request_message)
    document_floor = min(
        _MIN_EXPLICIT_LARGE_CHAPTER_APPEND_COMPACT_CHARS,
        len(_compact_for_echo(document.content_text)) // 3,
    )
    explicit_large_signals = (
        "大章节",
        "完整章节",
        "完整一章",
        "整章",
        "篇幅一样",
        "一样大",
        "同样篇幅",
        "跟一开始生成的板书",
    )
    if any(signal in compact for signal in explicit_large_signals):
        return max(_MIN_EXPLICIT_LARGE_CHAPTER_APPEND_COMPACT_CHARS, document_floor)
    return max(_MIN_CHAPTER_APPEND_COMPACT_CHARS, document_floor)


def _append_edit_needs_fallback(
    *,
    document: BoardDocument,
    request_message: str,
    replacement_html: str,
    replacement_text: str,
) -> bool:
    if not _is_append_document_request(request_message):
        return False

    generated_text = "\n".join(part for part in [html_to_text(replacement_html), replacement_text.strip()] if part)
    compact_generated = _compact_for_echo(generated_text)
    compact_request = _compact_for_echo(request_message)

    if compact_request and compact_request in compact_generated:
        return True
    if any(_compact_for_echo(marker) in compact_generated for marker in _LOW_VALUE_APPEND_MARKERS):
        return True
    if any(_compact_for_echo(marker) in compact_generated for marker in _APPEND_INSTRUCTION_ECHOES):
        return True
    if _is_chapter_append_request(request_message) and len(compact_generated) < _chapter_append_min_compact_chars(
        request_message,
        document,
    ):
        return True
    if _is_chapter_append_request(request_message):
        heading_count = len(re.findall(r"<h[23]\b", replacement_html, flags=re.IGNORECASE))
        if heading_count < 3 and len(compact_generated) < _MIN_EXPLICIT_LARGE_CHAPTER_APPEND_COMPACT_CHARS:
            return True
    if "过拟合" in compact_request and not any(
        term in generated_text for term in ("训练集", "验证集", "正则", "交叉验证", "早停", "复杂度", "数据泄漏", "样本外")
    ):
        return True
    return False


def _append_request_already_applied(
    *,
    document: BoardDocument,
    request_message: str,
    requirements,
) -> bool:
    if not _is_append_document_request(request_message) or not document.content_text.strip():
        return False

    topic = _append_section_topic(request_message, requirements)
    compact_topic = _compact_for_echo(topic)
    if len(compact_topic) < 4:
        return False

    heading_markers = (
        f"补充章节{compact_topic}",
        f"新增章节{compact_topic}",
        f"追加章节{compact_topic}",
        f"新章节{compact_topic}",
    )
    compact_lines = [_compact_for_echo(line) for line in document.content_text.splitlines()]
    if any(line in heading_markers for line in compact_lines):
        return True

    if not _is_chapter_append_request(request_message):
        return False

    tail = _compact_document_tail(document.content_text)
    compact_request = _compact_for_echo(request_message)
    if compact_topic not in tail:
        return False
    if compact_request and compact_request in tail:
        return False
    if any(_compact_for_echo(marker) in tail for marker in _APPEND_INSTRUCTION_ECHOES):
        return False
    return len(tail) >= 500


def _compact_document_tail(value: str) -> str:
    return _compact_for_echo(value[-1800:])


def _in_place_expansion_edit_looks_appended(ai_edit) -> bool:
    generated_text = "\n".join(
        part for part in [html_to_text(ai_edit.replacement_html), ai_edit.replacement_text.strip()] if part
    )
    compact_generated = _compact_for_echo(generated_text)
    if ai_edit.target_action in {"append_section", "create_child_lesson"}:
        return True
    append_headings = ("补充章节", "新增章节", "追加章节", "新章节")
    if any(_compact_for_echo(heading) in compact_generated[:120] for heading in append_headings):
        return True
    return bool(re.search(r"<h2\b[^>]*>\s*(?:补充|新增|追加)?章节", ai_edit.replacement_html, flags=re.IGNORECASE))


def run_board_executor(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    selected_reference = state.get("selected_reference")

    if decision.action in {"clarify_request", "await_scope_choice", "await_reference_choice"}:
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": None,
        }

    if decision.action == "no_change":
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": _resolve_board_teaching_guide(
                lesson=lesson,
                request=request,
                requirements=requirements,
                document=lesson.board_document,
                prefer_existing=selected_reference is None and not _is_section_followup_learning_need(lesson, request),
                selected_reference=selected_reference,
            ),
        }

    if decision.action == "create_new_lesson":
        topic = _extract_focus_terms(request.message)[0] if _extract_focus_terms(request.message) else request.message
        generated_lesson = build_lesson_for_topic(
            topic,
            requirements=requirements,
            reference_context=selected_reference,
        )
        generated_lesson.board_document = augment_document_with_generated_charts(
            generated_lesson.board_document,
            request_message=request.message,
        )
        generated_lesson.teaching_guide = _interactive_teaching_guide(
            lesson_id=generated_lesson.id,
            lesson_title=generated_lesson.title,
            document=generated_lesson.board_document,
            requirements=requirements,
        )
        if generated_lesson.history_graph.commits:
            generated_lesson.history_graph.commits[-1].snapshot = generated_lesson.board_document
        board_teaching_guide = _resolve_board_teaching_guide(
            lesson=generated_lesson,
            request=request,
            requirements=requirements,
            document=generated_lesson.board_document,
            prefer_existing=True,
            selected_reference=selected_reference,
        )
        generated_lesson.board_teaching_guide = board_teaching_guide
        if generated_lesson.history_graph.commits:
            generated_lesson.history_graph.commits[-1].metadata["board_teaching_guide"] = board_teaching_guide.model_dump(mode="json")
        return {
            "teaching_guide": generated_lesson.teaching_guide,
            "teacher_document": generated_lesson.board_document,
            "document_updated": True,
            "generated_lesson": generated_lesson,
            "teacher_talk_track": None,
            "board_teaching_guide": board_teaching_guide,
            "teaching_start_section_index": 0,
        }

    if decision.action == "append_section" and _append_request_already_applied(
        document=lesson.board_document,
        request_message=request.message,
        requirements=requirements,
    ):
        board_teaching_guide = _resolve_board_teaching_guide(
            lesson=lesson,
            request=request,
            requirements=requirements,
            document=lesson.board_document,
            prefer_existing=False,
            selected_reference=selected_reference,
        )
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "board_decision": BoardDecision(action="no_change", reason="同一追加章节已经在当前讲义中，避免重复写入。"),
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": board_teaching_guide,
        }

    existing_section_count = len(_board_h2_sections(lesson.board_document)) if decision.action == "append_section" else 0
    effective_scope_action = request.scope_action or ("append_section" if decision.action == "append_section" else None)
    ai_edit = openai_course_ai.generate_document_edit(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        current_branch=lesson.history_graph.current_branch,
        request_message=request.message,
        selection=request.selection.model_dump(mode="json") if request.selection else None,
        interaction_mode=request.interaction_mode,
        scope_action=effective_scope_action,
        requirements=requirements,
        document=lesson.board_document,
        selected_reference=_reference_payload(selected_reference, include_full_text=True),
    )

    if ai_edit is not None and not _document_edit_has_content(ai_edit):
        ai_edit = None
    if ai_edit is not None and decision.action == "append_section" and _append_edit_needs_fallback(
        document=lesson.board_document,
        request_message=request.message,
        replacement_html=ai_edit.replacement_html,
        replacement_text=ai_edit.replacement_text,
    ):
        ai_edit = None
    if (
        ai_edit is not None
        and decision.action == "edit_board"
        and _is_in_place_expansion_request(request.message)
        and _in_place_expansion_edit_looks_appended(ai_edit)
    ):
        ai_edit = None

    if ai_edit is not None:
        replacement_doc = build_document(
            title=ai_edit.suggested_title or lesson.board_document.title,
            content_html=ai_edit.replacement_html,
            content_text=ai_edit.replacement_text or None,
            document_id=lesson.board_document.id,
        )
        if (
            request.selection
            and request.interaction_mode == "direct_edit"
            and not _is_full_rewrite_request(request.message)
        ):
            generated_text = replacement_doc.content_text or html_to_text(ai_edit.replacement_html)
            replacement_text = _merge_selection_edit(
                selection_text=request.selection.excerpt,
                generated_text=generated_text,
                request_message=request.message,
            )
            replacement_html = replacement_doc.content_html
            if replacement_text.strip() != generated_text.strip():
                replacement_html = text_to_html(replacement_text)
            next_document = replace_selection_in_document(
                lesson.board_document,
                selection_text=request.selection.excerpt,
                replacement_text=replacement_text,
                replacement_html=replacement_html,
            )
        elif (
            decision.action == "append_section"
            or (
                ai_edit.target_action in {"append_section", "create_child_lesson"}
                and not _is_in_place_expansion_request(request.message)
            )
            or (not ai_edit.replace_whole and _is_append_document_request(request.message))
        ):
            next_document = append_html_section(lesson.board_document, replacement_doc.content_html)
        else:
            next_document = replacement_doc
        teacher_talk_track = ai_edit.teacher_talk_track.strip() or None
        board_teaching_guide = _bound_board_teaching_guide(
            guidance=ai_edit.board_teaching_guide,
            document=next_document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )
    else:
        return _failed_document_generation_result(
            lesson=lesson,
            request=request,
            decision=decision,
            requirements=requirements,
            selected_reference=selected_reference,
        )

    before_chart_document = next_document
    next_document = augment_document_with_generated_charts(
        next_document,
        request_message=request.message,
    )
    if document_changed(before_chart_document, next_document):
        board_teaching_guide = _bound_board_teaching_guide(
            guidance=board_teaching_guide,
            document=next_document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )

    guide = _interactive_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=next_document,
        requirements=requirements,
    )
    has_document_changed = document_changed(lesson.board_document, next_document)
    teaching_start_section_index = 0
    if has_document_changed and decision.action == "append_section" and board_teaching_guide.section_plans:
        teaching_start_section_index = min(existing_section_count, len(board_teaching_guide.section_plans) - 1)

    return {
        "teaching_guide": guide,
        "teacher_document": next_document,
        "document_updated": has_document_changed,
        "generated_lesson": None,
        "teacher_talk_track": teacher_talk_track,
        "board_teaching_guide": board_teaching_guide,
        "teaching_start_section_index": teaching_start_section_index,
    }
