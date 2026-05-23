from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.models import (
    BoardNeedMapping,
    BoardSectionTeachingPlan,
    BoardTeachingGuide,
    BoardTeachingProgress,
    BoardTeachingSelectedItem,
    Lesson,
    SectionTeachingProgressView,
)
from app.services.openai_course_ai import openai_course_ai


MAX_TEACHING_CONTEXT_CHARS = 2200
MAX_SECTION_EXCERPT_CHARS = 900


@dataclass(frozen=True)
class BoardTeachingResult:
    chatbot_message: str
    progress_view: SectionTeachingProgressView


def teach_first_section(
    *,
    lesson: Lesson,
    resource_summary: str,
    conversation_summary: str,
) -> BoardTeachingResult:
    return _teach_section(
        lesson=lesson,
        section_index=0,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
    )


def teach_next_section(
    *,
    lesson: Lesson,
    resource_summary: str,
    conversation_summary: str,
) -> BoardTeachingResult:
    sections = _section_titles(lesson)
    current = lesson.board_teaching_progress.current_section_index if lesson.board_teaching_progress else -1
    next_index = min(current + 1, max(len(sections) - 1, 0))
    return _teach_section(
        lesson=lesson,
        section_index=next_index,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
    )


def build_board_teaching_guide(lesson: Lesson) -> BoardTeachingGuide:
    sections = _document_sections(lesson)
    section_plans = [
        _build_section_plan(index=index, heading=heading, excerpt=excerpt)
        for index, (heading, excerpt) in enumerate(sections)
    ]
    teaching_flow = [plan.heading for plan in section_plans]
    selected_items = [
        BoardTeachingSelectedItem(
            excerpt=plan.board_excerpt,
            source_heading=plan.heading,
            reason="作为当前板书的主要讲解小节。",
            mapped_needs=list(lesson.learning_requirements.learning_need_checklist[:3])
            if lesson.learning_requirements
            else [],
            teaching_role="section",
            order_index=plan.order_index,
        )
        for plan in section_plans
        if plan.board_excerpt
    ][:8]
    need_mappings = [
        BoardNeedMapping(
            need=need,
            matched_excerpt=section_plans[0].board_excerpt if section_plans else lesson.board_document.content_text[:200],
            source_heading=section_plans[0].heading if section_plans else lesson.board_document.title,
            rationale="将学习需求映射到当前板书主线，便于后续按小节讲解。",
        )
        for need in (lesson.learning_requirements.learning_need_checklist[:5] if lesson.learning_requirements else [])
    ]
    return BoardTeachingGuide(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=_snapshot_hash(lesson),
        board_title=lesson.board_document.title or lesson.title,
        selected_items=selected_items,
        need_mappings=need_mappings,
        teaching_flow=teaching_flow,
        generation_rationale="根据当前板书结构自动生成分节讲解计划。",
        chatbot_brief=_compact_plan_text(section_plans),
        lecture_handout=_lecture_handout(section_plans),
        section_plans=section_plans,
    )


def _teach_section(
    *,
    lesson: Lesson,
    section_index: int,
    resource_summary: str,
    conversation_summary: str,
) -> BoardTeachingResult:
    sections = _section_titles(lesson)
    safe_index = min(max(section_index, 0), len(sections) - 1)
    section_title = sections[safe_index]
    has_next = safe_index < len(sections) - 1
    user_message = (
        f"请讲解当前板书的第 {safe_index + 1} 节：{section_title}。"
        f"当前是否还有后续章节：{'是' if has_next else '否'}。"
        "请根据这个状态自然决定结尾怎么收束。"
    )
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=lesson.learning_requirements.learning_goal if lesson.learning_requirements else lesson.summary,
        board_summary=_section_context(lesson, section_title),
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=user_message,
        selection_excerpt=None,
        interaction_mode="ask",
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()

    completed = set(lesson.board_teaching_progress.completed_section_indexes if lesson.board_teaching_progress else [])
    completed.add(safe_index)
    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=_snapshot_hash(lesson),
        current_section_index=safe_index,
        completed_section_indexes=sorted(completed),
        waiting_for_continue=has_next,
    )
    progress_view = SectionTeachingProgressView(
        section_index=safe_index,
        section_count=len(sections),
        current_section_title=section_title,
        has_next_section=has_next,
        waiting_for_continue=has_next,
    )
    return BoardTeachingResult(chatbot_message=chatbot_message, progress_view=progress_view)


def _section_titles(lesson: Lesson) -> list[str]:
    if lesson.board_teaching_guide and lesson.board_teaching_guide.section_plans:
        titles = [plan.heading for plan in lesson.board_teaching_guide.section_plans]
        return _dedupe_titles(titles)[:8]

    sections = _document_sections(lesson)
    if sections:
        return _dedupe_titles([heading for heading, _excerpt in sections])[:8]

    titles: list[str] = []
    if lesson.teaching_guide:
        titles.extend(mapping.supports_goal.strip() for mapping in lesson.teaching_guide.mappings)
    if not titles:
        titles.extend(line.strip("# ").strip() for line in lesson.board_document.content_text.splitlines())
    return _dedupe_titles(titles)[:8] or [lesson.board_document.title or lesson.title]


def _dedupe_titles(titles: list[str]) -> list[str]:
    normalized: list[str] = []
    for title in titles:
        compact = re.sub(r"\s+", " ", title).strip().strip("#").strip()
        if compact and compact not in normalized:
            normalized.append(compact)
    return normalized


def _section_context(lesson: Lesson, section_title: str) -> str:
    plan = _find_section_plan(lesson, section_title)
    if plan:
        plan_text = "\n".join(
            part
            for part in [
                f"当前讲解章节：{plan.heading}",
                f"板书原文：{plan.board_excerpt}",
                f"核心点：{'；'.join(plan.core_points)}" if plan.core_points else "",
                f"讲解步骤：{'；'.join(plan.teaching_steps)}" if plan.teaching_steps else "",
                f"例子或类比：{plan.example_or_analogy}" if plan.example_or_analogy else "",
                f"易错点：{'；'.join(plan.common_pitfalls)}" if plan.common_pitfalls else "",
                f"检查问题：{plan.check_question}" if plan.check_question else "",
            ]
            if part
        )
        return _limit_context(plan_text)

    compact_document = re.sub(r"\s+", " ", _section_excerpt_from_document(lesson, section_title)).strip()
    return f"当前讲解章节：{section_title}\n当前板书摘要：{_limit_context(compact_document or lesson.board_document.title)}"


def _snapshot_hash(lesson: Lesson) -> str:
    payload = f"{lesson.board_document.id}\n{lesson.board_document.content_text}\n{lesson.board_document.content_html}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _document_sections(lesson: Lesson) -> list[tuple[str, str]]:
    text = lesson.board_document.content_text or ""
    if not text.strip():
        return [(lesson.board_document.title or lesson.title, "")]

    heading_matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, flags=re.MULTILINE))
    h2_matches = [match for match in heading_matches if len(match.group(1)) == 2]
    selected_matches = h2_matches or heading_matches
    if not selected_matches:
        return [(lesson.board_document.title or lesson.title, _limit_excerpt(text))]

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(selected_matches):
        heading = match.group(2).strip()
        start = match.end()
        end = selected_matches[index + 1].start() if index + 1 < len(selected_matches) else len(text)
        excerpt = text[start:end].strip()
        if not excerpt and index == 0 and match.start() > 0:
            excerpt = text[: match.start()].strip()
        sections.append((heading, _limit_excerpt(excerpt or heading)))
    return sections[:8]


def _build_section_plan(*, index: int, heading: str, excerpt: str) -> BoardSectionTeachingPlan:
    points = _extract_core_points(excerpt, heading)
    first_point = points[0] if points else heading
    check_target = heading or first_point
    return BoardSectionTeachingPlan(
        order_index=index,
        heading=heading,
        board_excerpt=excerpt,
        core_points=points,
        teaching_steps=[
            f"先用一句话解释本节要解决的核心问题：{first_point}",
            "再按照板书中的定义、条件、步骤或关系逐层展开。",
            "接着用板书里的例子、类比或应用场景帮助学习者落地理解。",
            "最后指出一个容易混淆的点，并用检查问题确认是否听懂。",
        ],
        teaching_method="按板书小节从概念到关系、从例子到检查问题逐步讲解。",
        example_or_analogy=_first_example_line(excerpt),
        common_pitfalls=[
            "只记住结论，但没有说清它成立的条件或使用边界。",
            "跳过中间推理，导致后续例子和检查问题无法对应到板书原文。",
        ],
        check_question=f"你能用自己的话说明“{check_target}”这一节的核心意思，并指出它和下一步学习有什么关系吗？",
        transition_to_next="如果这一节能复述出来，再进入下一节；如果不能，先补一个例子或反例。",
    )


def _extract_core_points(excerpt: str, heading: str) -> list[str]:
    points: list[str] = []
    for raw_line in excerpt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        line = re.sub(r"^\d+[.、]\s*", "", line)
        line = line.strip()
        if line and line != heading:
            points.append(line)
        if len(points) >= 5:
            break
    return points or ([heading] if heading else [])


def _first_example_line(excerpt: str) -> str:
    for raw_line in excerpt.splitlines():
        line = raw_line.strip(" -*\t")
        if not line:
            continue
        if any(marker in line for marker in ("例", "比如", "例如", "如：", "应用", "场景")):
            return line
    return "可结合板书中最具体的一句话或一个符号关系举例说明。"


def _find_section_plan(lesson: Lesson, section_title: str) -> BoardSectionTeachingPlan | None:
    if not lesson.board_teaching_guide:
        return None
    normalized_title = _normalize_title(section_title)
    for plan in lesson.board_teaching_guide.section_plans:
        if _normalize_title(plan.heading) == normalized_title:
            return plan
    return None


def _section_excerpt_from_document(lesson: Lesson, section_title: str) -> str:
    normalized_title = _normalize_title(section_title)
    for heading, excerpt in _document_sections(lesson):
        if _normalize_title(heading) == normalized_title:
            return excerpt
    return lesson.board_document.content_text or lesson.board_document.title


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip("#").strip().lower()


def _limit_excerpt(value: str) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", value or "").strip()
    if len(compact) <= MAX_SECTION_EXCERPT_CHARS:
        return compact
    return f"{compact[: MAX_SECTION_EXCERPT_CHARS - 1]}..."


def _limit_context(value: str) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", value or "").strip()
    if len(compact) <= MAX_TEACHING_CONTEXT_CHARS:
        return compact
    return f"{compact[: MAX_TEACHING_CONTEXT_CHARS - 1]}..."


def _lecture_handout(section_plans: list[BoardSectionTeachingPlan]) -> str:
    chunks: list[str] = []
    for plan in section_plans:
        chunks.append(
            "\n".join(
                part
                for part in [
                    f"## {plan.heading}",
                    f"核心点：{'；'.join(plan.core_points)}" if plan.core_points else "",
                    f"讲解步骤：{'；'.join(plan.teaching_steps)}",
                    f"检查问题：{plan.check_question}",
                ]
                if part
            )
        )
    return "\n\n".join(chunks)


def _compact_plan_text(section_plans: list[BoardSectionTeachingPlan]) -> str:
    titles = " -> ".join(plan.heading for plan in section_plans)
    return f"按板书小节依次讲解：{titles}" if titles else ""
