from __future__ import annotations

from app.models import (
    BoardDocument,
    ChatRequest,
    LearningRequirementSheet,
    Lesson,
    ResourceReferenceContext,
    SectionTeachingProgressView,
)
from app.services.workflow_roles.materials import text_score
from app.services.workflow_roles.shared import compact, is_low_substance_message


def rank_board_excerpts(document: BoardDocument, query: str, *, limit: int = 4) -> list[tuple[str, float]]:
    lines = [compact(line, limit=260) for line in document.content_text.splitlines()]
    candidates = [line for line in lines if line]
    scored = [(line, text_score(query, line)) for line in candidates]
    ranked = [(line, score) for line, score in scored if score >= 0.16]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def teacher_from_board(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    excerpts: list[tuple[str, float]],
) -> str:
    if excerpts:
        lines = [f"我先根据当前板书里最相关的内容来讲：{compact(excerpts[0][0], limit=180)}"]
        for excerpt, _score in excerpts[1:3]:
            lines.append(f"同时要连上这一点：{compact(excerpt, limit=160)}")
        lines.append(f"这一轮的检查标准是：{compact(requirements.success_criteria, limit=180)}")
        return "\n".join(lines)
    if lesson.board_document.content_text.strip():
        first_line = compact(lesson.board_document.content_text.splitlines()[0], limit=180)
        return f"当前板书已经有内容，但和这次问题的重合度不高。我会先围绕“{compact(request.message, limit=80)}”补出一段可讲解的板书，再继续讲。已有板书起点是：{first_line}"
    return f"当前板书还没有可讲的内容。我会先围绕“{compact(request.message, limit=80)}”建立一段可继续扩展的板书。"


def teacher_after_board_write(
    requirements: LearningRequirementSheet,
    *,
    reference_context: ResourceReferenceContext | None = None,
) -> str:
    if reference_context is not None:
        points = reference_context.teaching_points[:2]
        point_text = "；".join(compact(point, limit=120) for point in points if point)
        suffix = f" 这次优先抓住：{point_text}" if point_text else ""
        return (
            f"我已把“{compact(reference_context.chapter_title, limit=80)}”整理进当前板书，"
            f"接下来会按这段资料和你的目标来讲。{suffix}"
        )
    return (
        "我已先把本轮学习主题记录到板书里。"
        f"接下来会围绕“{compact(requirements.learning_goal, limit=120)}”继续展开。"
    )


def empty_board_prompt_message(request: ChatRequest) -> str:
    message = compact(request.message, limit=80)
    if is_low_substance_message(message):
        return "当前板书还没有可继续讲的内容。你给我一个具体主题、问题或上传资料，我再开始讲解和写板书。"
    return f"我可以从“{message}”开始，但不会把需求清单当成讲义模板写进板书。你可以让我生成讲义、上传资料，或直接说从零开始讲。"


def teaching_progress(document: BoardDocument) -> SectionTeachingProgressView | None:
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

