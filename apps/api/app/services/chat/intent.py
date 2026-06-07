from __future__ import annotations

import re

from app.models import BoardTaskAction, ChatRequest, LearningRequirementSheet
from app.services.learning_requirement_manager import is_generation_control_request


MAX_CONTEXT_CHARS = 1800

EXPLAIN_REQUEST_PATTERN = re.compile(
    r"(讲解|讲述|解释|说明|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"概括|总结|总览|整体把握|大意|框架|梳理(?:框架|结构)?|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
STRONG_EXPLAIN_REQUEST_PATTERN = re.compile(
    r"(讲解|讲述|解释|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"概括|总结|总览|整体把握|大意|框架|梳理(?:框架|结构)?|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
APPEND_REQUEST_PATTERN = re.compile(
    r"(续写|继续写|接着写|往后写|后续|新增|追加|新加|新章节|新小节|下一节|下一章|下一部分|末尾|"
    r"(?:帮我|为我|请|可以|能不能|你可以)?.{0,8}(?:写|编写|生成|设计|创建|做)"
    r"(?:一|几|[0-9０-９一二三四五六七八九十两]|个|段|篇|份|条|点|些|一下))"
)
EXPAND_REQUEST_PATTERN = re.compile(r"(扩写|扩展|补充|增加|添加)")
SIMPLIFY_REQUEST_PATTERN = re.compile(
    r"(简化|简单(?:一点|点|些)?|更简单|通俗|更容易懂|更好懂|好理解|容易理解|降低难度|浅显|"
    r"缩短|改短|短(?:一点|点|些)|精简|压缩|太长|篇幅|"
    r"控制.{0,8}(?:以内|以下)|[0-9０-９一二三四五六七八九十两]+.{0,8}(?:以内|以下))"
)
REWRITE_REQUEST_PATTERN = re.compile(
    r"(改写|重写|修改|编辑|润色|优化|"
    r"改(?:得|的)?(?:简单|通俗|容易|好懂|清楚|更清楚|更难|难一点|有难度|更有区分度)|"
    r"(?:提高|增加|提升).{0,6}难度|换(?:个|一种)说法)"
)
TARGET_LOCATION_HINT_PATTERN = re.compile(
    r"(选中|这一段|这段|这部分|这里|前面|上面|下面|"
    r"第.{0,8}[章节部分段空题项条句行]|定义|概念|例子|示例|结论|总结|表格|为什么)"
)
RESOURCE_REFERENCE_HINT_PATTERN = re.compile(
    r"(资料|材料|文档|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word|章节|小节|第.{0,8}[章节部分])",
    re.IGNORECASE,
)
EXPLICIT_RESOURCE_REFERENCE_PATTERN = re.compile(
    r"(资料|材料|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word)",
    re.IGNORECASE,
)
LEARNING_START_REQUEST_PATTERN = re.compile(r"(我要学|我想学|想学习|学习一下|开始学|帮我学|学一学)")
FOLLOWUP_EXECUTION_PATTERN = re.compile(r"^(写啊|写|开始|执行|可以|好的|好|就这样|按这个来|照这个来|继续)$")
DOCUMENT_GENERATION_ACTIONS = r"(生成|写|撰写|创建|整理|制作|设计|输出|产出|编写)"
DOCUMENT_ARTIFACT_NOUNS = (
    r"(文档|讲义|板书|版书|课文|文章|作文|报告|对话|练习|题目|试题|测验|课程|"
    r"教案|教程|学习计划|提纲|大纲|案例|表格|清单|材料|页面|章节|小节)"
)
DOCUMENT_ARTIFACT_REQUEST_PATTERN = re.compile(
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,48}}{DOCUMENT_ARTIFACT_NOUNS}"
    r"|"
    rf"{DOCUMENT_ARTIFACT_NOUNS}.{{0,24}}{DOCUMENT_GENERATION_ACTIONS}"
    r"|"
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,12}}(?:一|几|若干|多)?(?:篇|份|个|套|道|组|页|段|部分)[^吧吗呢啊。！？!?；;\n]{{2,80}}"
)

EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {*EDIT_ACTIONS, "append_section"}


def _compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _requests_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and EXPLAIN_REQUEST_PATTERN.search(compact))


def _requests_append_section(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and APPEND_REQUEST_PATTERN.search(compact))


def _is_followup_execution_request(text: str) -> bool:
    compact = _compact_text(text, limit=80)
    return bool(compact and FOLLOWUP_EXECUTION_PATTERN.search(compact))


def _requirements_imply_append(requirements: LearningRequirementSheet) -> bool:
    if requirements.action_type == "append_section":
        return True
    action_text = " ".join(
        part
        for part in [
            requirements.action_instruction,
            requirements.learning_goal,
            *requirements.learning_need_checklist,
        ]
        if part
    )
    return _requests_append_section(action_text)


def _has_explicit_resource_reference(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and EXPLICIT_RESOURCE_REFERENCE_PATTERN.search(compact))


def _should_force_explain_task(message: str) -> bool:
    if not EXPLAIN_REQUEST_PATTERN.search(message):
        return False
    has_write_intent = _requests_append_section(message) or bool(EXPAND_REQUEST_PATTERN.search(message))
    if has_write_intent and not STRONG_EXPLAIN_REQUEST_PATTERN.search(message):
        return False
    return True


def _infer_board_task_action(request: ChatRequest, *, has_selection: bool, document_empty: bool) -> BoardTaskAction | None:
    if request.board_generation_action == "start":
        return "generate_board"
    message = _compact_text(request.message, limit=280)
    if request.interaction_mode == "direct_edit":
        if _requests_append_section(message):
            return "append_section"
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
        return "rewrite_target"
    if not has_selection and _has_explicit_resource_reference(message):
        return None
    if not document_empty and _should_force_explain_task(message):
        return "explain_target"
    if _requests_append_section(message) and not document_empty:
        return "append_section"
    if not document_empty and SIMPLIFY_REQUEST_PATTERN.search(message):
        return "simplify_target"
    if not document_empty and EXPAND_REQUEST_PATTERN.search(message):
        return "expand_target"
    if REWRITE_REQUEST_PATTERN.search(message):
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
        return "rewrite_target"
    if has_selection and not document_empty:
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
    if _should_force_explain_task(message) and (has_selection or TARGET_LOCATION_HINT_PATTERN.search(message)):
        return "explain_target"
    if not has_selection and RESOURCE_REFERENCE_HINT_PATTERN.search(message):
        return None
    if has_selection and not document_empty:
        return "explain_target"
    return None


def _prefer_requirement_action(
    inferred: BoardTaskAction | None,
    requirement_action: BoardTaskAction | None,
    *,
    request_message: str,
    requirements: LearningRequirementSheet,
) -> BoardTaskAction | None:
    if inferred is None and _is_followup_execution_request(request_message) and _requirements_imply_append(requirements):
        return "append_section"
    if requirement_action == "append_section":
        return requirement_action
    if requirement_action in EDIT_ACTIONS:
        return requirement_action
    if requirement_action == "explain_target" and inferred is None:
        return requirement_action
    return inferred


def _requests_document_artifact_generation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    if not compact:
        return False
    return bool(DOCUMENT_ARTIFACT_REQUEST_PATTERN.search(compact))


def _requests_resource_backed_answer(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and RESOURCE_REFERENCE_HINT_PATTERN.search(compact))


def _requests_learning_start(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and LEARNING_START_REQUEST_PATTERN.search(compact))


def _should_prompt_resource_reference(text: str) -> bool:
    return (
        _requests_resource_backed_answer(text)
        or _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )
