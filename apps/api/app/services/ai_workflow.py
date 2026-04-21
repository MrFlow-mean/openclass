from __future__ import annotations

import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.models import (
    BoardBlock,
    BoardDecision,
    BoardDocument,
    BlockStyle,
    ChatRequest,
    CoursePackage,
    LearningRequirementSheet,
    Lesson,
    PatchOperation,
    PatchProposal,
    ResourceMatch,
    ScopeOption,
    TeachingGuide,
)
from app.services.course_runtime import (
    build_internal_teaching_guide,
    build_lesson_for_topic,
    effective_requirements,
    normalize_requirements,
)
from app.services.document_ops import apply_patch
from app.services.openai_course_ai import openai_course_ai


class WorkflowState(TypedDict, total=False):
    lesson: Lesson
    course_package: CoursePackage
    request: ChatRequest
    learning_requirement_sheet: LearningRequirementSheet
    needs_clarification: bool
    clarification_questions: list[str]
    pm_reason: str
    board_decision: BoardDecision
    patch_proposal: PatchProposal | None
    teaching_guide: TeachingGuide
    teacher_message: str
    teacher_document: BoardDocument
    scope_options: list[ScopeOption]
    resource_matches: list[ResourceMatch]
    new_lesson_topic: str | None
    generated_lesson: Lesson | None


def _lesson_corpus(lesson: Lesson) -> str:
    return " ".join(
        [
            lesson.title,
            lesson.summary,
            *(lesson.tags or []),
            *[block.title for block in lesson.board_document.blocks],
            *[block.content for block in lesson.board_document.blocks],
        ]
    ).lower()


def _extract_focus_terms(message: str) -> list[str]:
    quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", message)
    if quoted:
        return quoted[:4]
    candidates = re.findall(r"[A-Za-z\u4e00-\u9fff]{2,}", message)
    return candidates[:4]


def classify_scope(message: str, lesson: Lesson) -> str:
    if any(keyword in message for keyword in ["习题", "练习", "例题", "更易懂", "简单讲", "总结", "整理", "改写"]):
        return "in_scope"

    if any(keyword in message for keyword in ["新增章节", "补充一节", "展开讲", "单独一节", "新开一节"]):
        return "scope_escalation"

    if "什么是" in message or "what is" in message.lower():
        lesson_text = _lesson_corpus(lesson)
        terms = _extract_focus_terms(message)
        unknown = [term for term in terms if term.lower() not in lesson_text]
        if unknown:
            return "scope_escalation"

    return "in_scope"


def match_resources(course_package: CoursePackage, message: str) -> list[ResourceMatch]:
    tokens = [token.lower() for token in _extract_focus_terms(message)]
    matches: list[ResourceMatch] = []
    for resource in course_package.resources:
        for chapter in resource.outline:
            overlap = sorted(set(tokens).intersection({keyword.lower() for keyword in chapter.keywords}))
            if overlap:
                matches.append(
                    ResourceMatch(
                        resource_id=resource.id,
                        chapter_id=chapter.id,
                        resource_name=resource.name,
                        chapter_title=chapter.title,
                        reason=f"与当前问题重合的概念词：{', '.join(overlap)}",
                    )
                )
    return matches[:3]


def _selected_or_last_block(lesson: Lesson, block_id: str | None) -> BoardBlock:
    if block_id:
        for block in lesson.board_document.blocks:
            if block.id == block_id:
                return block
    return lesson.board_document.blocks[-1]


def _draft_requirements(lesson: Lesson, request: ChatRequest) -> LearningRequirementSheet:
    requirements = effective_requirements(lesson)
    user_turns = [turn.content for turn in request.conversation if turn.role == "user"]
    requirements.current_questions = [*user_turns[-3:], request.message][-4:]
    if request.selection:
        requirements.current_questions.append(f"用户框选内容：{request.selection.excerpt[:80]}")
    requirements.boundary = "优先围绕当前 lesson 主线；超出范围时先决定是仅讲解、补充章节还是新开 lesson。"
    return normalize_requirements(
        requirements,
        lesson_title=lesson.title,
        document=lesson.board_document,
    )


def _heuristic_clarification(lesson: Lesson, request: ChatRequest) -> tuple[bool, str, list[str]]:
    message = request.message.strip()
    if request.selection:
        return False, "用户已经通过选区指向了具体内容。", []

    if any(keyword in message for keyword in ["定义", "公式", "例题", "练习", "总结", "讲解", "新增", "改写", "板书"]):
        return False, "用户已经给出明确的学习动作或目标。", []

    compact = re.sub(r"[\s，。！？?!.、\"“”']", "", message)
    generic_phrases = {
        "不懂",
        "没懂",
        "这里没懂",
        "这个没懂",
        "这个什么意思",
        "讲讲",
        "解释下",
        "帮我学",
        "怎么学",
        "怎么理解",
    }
    if len(compact) <= 8 or compact in generic_phrases:
        return (
            True,
            "当前问题还没有明确到可以直接决定板书策略。",
            [
                "你现在最想解决的是哪一个具体知识点或题目？",
                "你是希望我直接讲懂，还是同时改一版更适合你的板书？",
                "你希望讲到什么深度：先入门、能做题，还是深入理解？",
            ],
        )

    focus_terms = _extract_focus_terms(message)
    lesson_text = _lesson_corpus(lesson)
    if focus_terms and any(term.lower() in lesson_text for term in focus_terms):
        return False, "当前问题和 lesson 主线仍然有关。", []

    return False, "问题已经具备基本上下文，可以继续推进。", []


def _build_scope_options(matches: list[ResourceMatch]) -> list[ScopeOption]:
    return [
        ScopeOption(
            action="patch_current_lesson",
            label="当前课内简述",
            description="不改当前板书结构，只围绕现有板书先把问题讲清楚。",
        ),
        ScopeOption(
            action="append_section",
            label="新增章节",
            description="在当前 lesson 里补充一个新章节，把这个问题纳入主线。",
        ),
        ScopeOption(
            action="create_new_lesson",
            label="新开详细 lesson",
            description="把这个问题单独开成一节新课，避免覆盖当前主线。",
            resource_chapter_id=matches[0].chapter_id if matches else None,
        ),
    ]


def _fallback_board_decision(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    matches: list[ResourceMatch],
) -> BoardDecision:
    message = request.message
    scope_mode = classify_scope(message, lesson)

    if request.scope_action == "create_new_lesson":
        return BoardDecision(action="create_new_lesson", reason="用户明确要求把问题拆成一节新课。")

    if request.scope_action == "append_section":
        return BoardDecision(action="append_section", reason="用户选择在当前 lesson 中新增章节。")

    if request.scope_action == "patch_current_lesson":
        return BoardDecision(action="no_change", reason="用户选择先在当前课内简述，不直接改板书。")

    if scope_mode == "scope_escalation":
        if matches:
            return BoardDecision(
                action="await_scope_choice",
                reason=f"问题超出当前板书范围，并且资料库里已有相关入口：{matches[0].resource_name} / {matches[0].chapter_title}。",
            )
        return BoardDecision(action="await_scope_choice", reason="问题已经超出当前 lesson，需要先选择推进方式。")

    if any(keyword in message for keyword in ["新增章节", "补充一节", "展开讲", "扩展"]):
        return BoardDecision(action="append_section", reason="用户希望把相关内容纳入当前 lesson 的新章节。")

    if any(keyword in message for keyword in ["更易懂", "通俗", "改写", "整理", "练习", "习题", "例题", "总结", "补一段板书"]):
        return BoardDecision(action="edit_board", reason="当前需求更适合先调整板书，再围绕更新后的结构讲解。")

    if any(keyword in message for keyword in ["解释", "讲一下", "讲讲", "为什么", "什么意思", "怎么理解"]):
        return BoardDecision(action="no_change", reason="当前更像围绕现有板书的讲解请求，不必先改板书。")

    if requirements.output_preference:
        return BoardDecision(action="no_change", reason="现有板书已经能支撑这次讲解，先不改板书。")

    return BoardDecision(action="edit_board", reason="默认先生成一份局部板书补丁，便于后续讲解。")


def _fallback_patch_operations(
    *,
    action: str,
    lesson: Lesson,
    request: ChatRequest,
    selected_block: BoardBlock,
) -> tuple[list[PatchOperation], str, str]:
    operations: list[PatchOperation] = []
    rationale = "基于当前 lesson 生成局部补丁。"
    target_action = "patch_current_lesson"

    if action == "append_section":
        terms = "、".join(_extract_focus_terms(request.message)[:2]) or "扩展主题"
        operations.extend(
            [
                PatchOperation(
                    op="insert_block",
                    after_block_id=lesson.board_document.blocks[-1].id,
                    block=BoardBlock(
                        type="heading",
                        title=f"{terms}：补充展开",
                        content="这一节专门解释新问题与当前课程主线的关系。",
                        style=BlockStyle(font_size="lg", emphasis="accent"),
                    ),
                ),
                PatchOperation(
                    op="insert_block",
                    after_block_id=None,
                    block=BoardBlock(
                        type="paragraph",
                        title="扩展说明",
                        content="先给出最小可用定义，再说明它为什么会在此处出现，以及和当前主线如何连接。",
                    ),
                ),
            ]
        )
        return operations, "当前问题已经超出原有板书，但适合作为同一课的新增章节。", "append_section"

    if "习题" in request.message or "练习" in request.message:
        operations.append(
            PatchOperation(
                op="insert_block",
                after_block_id=selected_block.id,
                block=BoardBlock(
                    type="exercise",
                    title="AI 新增练习",
                    content=(
                        "1. 用自己的话复述这个概念。\n"
                        "2. 完成一道基础题，并说明每一步为什么成立。\n"
                        "3. 再试一道稍微变形的练习。"
                    ),
                    style=BlockStyle(emphasis="callout"),
                ),
            )
        )
        return operations, "用户要求出练习题，因此在当前 lesson 里追加一个练习块。", "append_section"

    if any(keyword in request.message for keyword in ["更易懂", "通俗", "简单讲", "改写", "整理"]):
        simplified = (
            f"先把它讲得更直白一些：{selected_block.content}\n\n"
            "可以把它理解成：先抓住定义，再看它在题目里如何被调用。"
        )
        operations.append(
            PatchOperation(
                op="update_block_content",
                block_id=selected_block.id,
                title=selected_block.title,
                content=simplified,
            )
        )
        return operations, "用户想把当前内容讲得更易懂，因此只修改命中的板书块。", target_action

    if any(keyword in request.message for keyword in ["总结", "整理"]):
        operations.append(
            PatchOperation(
                op="insert_block",
                after_block_id=selected_block.id,
                block=BoardBlock(
                    type="note",
                    title="AI 总结",
                    content="先回收本节最关键的定义、使用条件和最容易出错的地方。",
                    style=BlockStyle(emphasis="callout"),
                ),
            )
        )
        return operations, "用户希望快速收束重点，因此插入一个总结块。", target_action

    operations.append(
        PatchOperation(
            op="insert_block",
            after_block_id=selected_block.id,
            block=BoardBlock(
                type="note",
                title="AI 补充说明",
                content=f"围绕“{request.message}”补一段板书说明，并保持与当前 lesson 主线一致。",
                style=BlockStyle(emphasis="callout"),
            ),
        )
    )
    return operations, rationale, target_action


def _relevant_blocks(document: BoardDocument, request: ChatRequest) -> list[BoardBlock]:
    if request.selection and request.selection.block_id:
        for block in document.blocks:
            if block.id == request.selection.block_id:
                return [block]

    terms = {term.lower() for term in _extract_focus_terms(request.message)}
    if not terms:
        return document.blocks[: min(3, len(document.blocks))]

    scored: list[tuple[int, BoardBlock]] = []
    for block in document.blocks:
        corpus = f"{block.title} {block.content}".lower()
        score = sum(1 for term in terms if term in corpus)
        if block.type == "formula":
            score += 1
        if score:
            scored.append((score, block))

    if scored:
        return [block for _, block in sorted(scored, key=lambda item: item[0], reverse=True)[:3]]

    return document.blocks[: min(3, len(document.blocks))]


def _block_snippet(block: BoardBlock, mapping: TeachingGuide | None) -> str:
    if mapping:
        matched = next((item for item in mapping.mappings if item.block_id == block.id), None)
        if matched and matched.focus_points:
            detail = matched.focus_points[-1]
            return detail[:100]
    first_line = block.content.splitlines()[0].strip()
    return first_line[:100]


def _fallback_teacher_message(state: WorkflowState) -> str:
    request = state["request"]
    decision = state["board_decision"]
    requirements = state["learning_requirement_sheet"]
    proposal = state.get("patch_proposal")
    document = state.get("teacher_document") or state["lesson"].board_document
    guide = state["teaching_guide"]
    clarification_questions = state.get("clarification_questions", [])
    generated_lesson = state.get("generated_lesson")
    lesson_title = generated_lesson.title if generated_lesson else state["lesson"].title

    if decision.action == "clarify_request":
        numbered = "\n".join(
            f"{index}. {question}" for index, question in enumerate(clarification_questions or ["你最想先解决哪个具体问题？"], start=1)
        )
        return (
            "我先不急着改板书，想先把你的学习需求确认清楚，这样后面的板书和讲解才会更贴合你。\n"
            f"{numbered}"
        )

    if decision.action == "await_scope_choice":
        resource_line = ""
        matches = state.get("resource_matches", [])
        if matches:
            first = matches[0]
            resource_line = f"\n我在资料库里还找到了相关入口：{first.resource_name} / {first.chapter_title}。"
        return (
            f"这个问题已经超出《{lesson_title}》当前板书范围。"
            "你可以先决定是只在当前课里简述、在本课新增章节，还是直接新开一节详细 lesson。"
            f"{resource_line}"
        )

    intro_map = {
        "no_change": f"这次先不改《{lesson_title}》的板书，我直接沿着当前结构给你讲。",
        "edit_board": f"我已经先把《{lesson_title}》里和这次需求最相关的部分调顺了，下面按更新后的结构给你讲。",
        "append_section": f"我已经把这个问题补成《{lesson_title}》里的一个新章节，下面按新的板书结构给你讲。",
        "create_new_lesson": f"我已经把这个更大的问题拆成新课《{lesson_title}》，我们先抓住它的主线。",
    }

    relevant_blocks = _relevant_blocks(document, request)
    explanation_lines = [intro_map.get(decision.action, f"我先围绕《{lesson_title}》当前板书给你讲。")]
    for index, block in enumerate(relevant_blocks[:3], start=1):
        explanation_lines.append(f"{index}. {block.title}：{_block_snippet(block, guide)}")

    check_question = next(
        (mapping.check_questions[0] for mapping in guide.mappings if mapping.check_questions),
        requirements.success_criteria,
    )
    if proposal is not None:
        explanation_lines.append("右侧已经准备好本次板书改动预览，你可以一边看 diff，一边跟着新的结构理解。")
    explanation_lines.append(f"你可以先试着回答这个检查问题：{check_question}")
    return "\n".join(explanation_lines)


def pm_node(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    draft_requirements = _draft_requirements(lesson, request)
    assessment = openai_course_ai.assess_learning_requirements(
        lesson_title=lesson.title,
        lesson_summary=lesson.summary,
        lesson_tags=lesson.tags,
        block_titles=[block.title for block in lesson.board_document.blocks],
        user_message=request.message,
        selection_excerpt=request.selection.excerpt if request.selection else None,
        conversation=[turn.model_dump(mode="json") for turn in request.conversation],
    )

    if assessment is not None:
        requirements = normalize_requirements(
            assessment.learning_requirement_sheet,
            lesson_title=lesson.title,
            document=lesson.board_document,
        )
        return {
            "learning_requirement_sheet": requirements,
            "needs_clarification": not assessment.ready,
            "clarification_questions": assessment.clarification_questions[:3],
            "pm_reason": assessment.reason,
        }

    needs_clarification, reason, questions = _heuristic_clarification(lesson, request)
    return {
        "learning_requirement_sheet": draft_requirements,
        "needs_clarification": needs_clarification,
        "clarification_questions": questions[:3],
        "pm_reason": reason,
    }


def board_manager_node(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    matches = match_resources(state["course_package"], request.message)

    if state.get("needs_clarification"):
        return {
            "board_decision": BoardDecision(
                action="clarify_request",
                reason=state.get("pm_reason", "当前需求仍需要继续澄清。"),
            ),
            "scope_options": [],
            "resource_matches": matches,
        }

    ai_decision = openai_course_ai.generate_board_decision(
        lesson_title=lesson.title,
        request_message=request.message,
        selection=request.selection.model_dump(mode="json") if request.selection else None,
        scope_action=request.scope_action,
        requirements=requirements,
        document=lesson.board_document,
        resource_matches=[match.model_dump(mode="json") for match in matches],
    )
    decision = ai_decision or _fallback_board_decision(lesson, request, requirements, matches)
    scope_options = _build_scope_options(matches) if decision.action == "await_scope_choice" else []
    return {
        "board_decision": decision,
        "scope_options": scope_options,
        "resource_matches": matches,
    }


def board_executor_node(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    matches = state.get("resource_matches", [])

    if decision.action in {"clarify_request", "await_scope_choice", "no_change"}:
        guide = build_internal_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "patch_proposal": None,
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "generated_lesson": None,
            "new_lesson_topic": lesson.title,
        }

    if decision.action == "create_new_lesson":
        topic = _extract_focus_terms(request.message)[0] if _extract_focus_terms(request.message) else request.message
        generated_lesson = build_lesson_for_topic(topic, requirements=requirements)
        return {
            "patch_proposal": None,
            "teaching_guide": generated_lesson.teaching_guide,
            "teacher_document": generated_lesson.board_document,
            "generated_lesson": generated_lesson,
            "new_lesson_topic": generated_lesson.title,
        }

    selected_block = _selected_or_last_block(lesson, request.selection.block_id if request.selection else None)
    ai_patch = openai_course_ai.generate_patch_proposal(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        current_branch=lesson.history_graph.current_branch,
        request_message=request.message,
        selection=request.selection.model_dump(mode="json") if request.selection else None,
        scope_action=request.scope_action,
        requirements=requirements,
        document=lesson.board_document,
        resource_matches=[match.model_dump(mode="json") for match in matches],
    )

    if ai_patch is not None:
        preview_doc, diff = apply_patch(lesson.board_document, ai_patch.operations)
        guide = build_internal_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=preview_doc,
            requirements=requirements,
        )
        return {
            "patch_proposal": PatchProposal(
                rationale=ai_patch.rationale,
                commit_label=ai_patch.commit_label,
                operations=ai_patch.operations,
                diff_preview=diff,
                target_action=ai_patch.target_action,
                suggested_title=ai_patch.suggested_title,
            ),
            "teaching_guide": guide,
            "teacher_document": preview_doc,
            "generated_lesson": None,
            "new_lesson_topic": lesson.title,
        }

    operations, rationale, target_action = _fallback_patch_operations(
        action=decision.action,
        lesson=lesson,
        request=request,
        selected_block=selected_block,
    )
    preview_doc, diff = apply_patch(lesson.board_document, operations)
    guide = build_internal_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=preview_doc,
        requirements=requirements,
    )
    return {
        "patch_proposal": PatchProposal(
            rationale=rationale,
            commit_label="AI board patch",
            operations=operations,
            diff_preview=diff,
            target_action=target_action,
        ),
        "teaching_guide": guide,
        "teacher_document": preview_doc,
        "generated_lesson": None,
        "new_lesson_topic": lesson.title,
    }


def teacher_node(state: WorkflowState) -> WorkflowState:
    request = state["request"]
    proposal = state.get("patch_proposal")
    requirements = state["learning_requirement_sheet"]
    matches = state.get("resource_matches", [])
    scope_options = state.get("scope_options", [])
    decision = state["board_decision"]
    teacher_document = state.get("teacher_document") or state["lesson"].board_document

    ai_message = openai_course_ai.generate_teacher_message(
        lesson_title=(state.get("generated_lesson") or state["lesson"]).title,
        request_message=request.message,
        requirements=requirements,
        document=teacher_document,
        guide=state["teaching_guide"],
        board_decision=decision,
        patch_proposal=proposal,
        scope_options=scope_options,
        resource_matches=[match.model_dump(mode="json") for match in matches],
        clarification_questions=state.get("clarification_questions", []),
    )
    if ai_message:
        return {"teacher_message": ai_message}
    return {"teacher_message": _fallback_teacher_message(state)}


def build_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("pm", pm_node)
    graph.add_node("board_manager", board_manager_node)
    graph.add_node("board_executor", board_executor_node)
    graph.add_node("teacher", teacher_node)
    graph.add_edge(START, "pm")
    graph.add_edge("pm", "board_manager")
    graph.add_edge("board_manager", "board_executor")
    graph.add_edge("board_executor", "teacher")
    graph.add_edge("teacher", END)
    return graph.compile()


course_workflow = build_workflow()
