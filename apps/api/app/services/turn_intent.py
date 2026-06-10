from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import BoardTaskRequestedAction


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

SEQUENTIAL_EXPLANATION_REQUEST_PATTERN = re.compile(
    r"(都讲|全都讲|全部讲|都解释|全部解释|逐个|一个个|挨个|依次|按顺序|从头到尾|"
    r"(?:讲解|解释|讲|说明).{0,12}(?:所有|全部|每个|每一(?:个|道|题|节|小节|部分|段)?|每道|每题|各个)|"
    r"(?:所有|全部|每个|每一(?:个|道|题|节|小节|部分|段)?|每道|每题|各个).{0,12}(?:都)?(?:讲|讲解|解释|说明))"
)
COLLECTION_EXPLANATION_TARGET_PATTERN = re.compile(
    r"(练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目|"
    r"exercise|exercises|question|questions|problem|problems|quiz|quizzes|task|tasks)",
    re.IGNORECASE,
)
SINGLE_EXPLANATION_TARGET_PATTERN = re.compile(
    r"(第\s*[0-9０-９一二三四五六七八九十两]+.{0,8}(?:章|节|小节|部分|段|句|行|题|项|条|步)|"
    r"(?:练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目)"
    r"\s*[0-9０-９一二三四五六七八九十两]+|"
    r"倒数|选中|这里|这(?:一|个)?(?:段|句|行|题|项|条|步|部分)|某(?:段|句|行|题|项|条|步))",
    re.IGNORECASE,
)
OVERVIEW_EXPLANATION_REQUEST_PATTERN = re.compile(r"(概括|总结|总览|整体把握|大意|框架|梳理(?:框架|结构)?)")
WHOLE_DOCUMENT_SCOPE_PATTERN = re.compile(r"(全文|整篇|整份|整个(?:文档|板书)|全篇|全部内容|整体)")

BOARD_TASK_WRITE_PATTERN = re.compile(r"(写|编写|生成|设计|创建|新增|追加|补充|扩写|添加|加一段|加一节|加入)")
BOARD_TASK_EDIT_PATTERN = re.compile(
    r"(改|修改|改写|重写|编辑|润色|优化|简化|扩展|缩短|改短|调整|精简|压缩|太长|篇幅|"
    r"控制.{0,8}(?:以内|以下)|[0-9０-９一二三四五六七八九十两]+.{0,8}(?:以内|以下))"
)
BOARD_TASK_EXPLAIN_PATTERN = re.compile(
    r"(讲解|讲述|解释|说明|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
BOARD_TASK_STRONG_EXPLAIN_PATTERN = re.compile(
    r"(讲解|讲述|解释|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
BOARD_TASK_CHAT_PATTERN = re.compile(r"(练习|互动|你问我答|问答|角色|轮流|按.{0,12}规则|对话|测验|检查我)")
ORDINAL_HINT_PATTERN = re.compile(r"(第\s*[0-9０-９一二三四五六七八九十两]+|[0-9０-９一二三四五六七八九十两]+\s*[.．、:：)）])")
TARGET_BEFORE_ACTION_PATTERN = re.compile(
    r"(?:在|把|对|给)?(?P<hint>[^，。！？!?；;\n\r]{1,80}?)"
    r"(?:里|中|下|后面|前面|旁边|部分|这一段|这段)"
    r"[^，。！？!?；;\n\r]{0,24}?"
    r"(?:写|编写|生成|设计|创建|新增|追加|补充|扩写|添加|改|修改|改写|重写|编辑|润色|优化|简化|精简|压缩|缩短|改短|讲解|解释|说明)"
)


@dataclass(frozen=True)
class IntentSignals:
    wants_write: bool
    wants_edit: bool
    wants_explain: bool
    wants_append: bool
    wants_expand: bool
    wants_simplify: bool
    wants_rewrite: bool
    wants_resource: bool
    wants_sequence: bool
    wants_collection: bool
    wants_whole_document: bool
    has_single_target: bool
    has_target_hint: bool
    raw_matches: dict[str, list[str]]
    wants_chat: bool = False


def compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    return pattern.search(text) if text else None


def _matched(pattern: re.Pattern[str], text: str) -> list[str]:
    match = _match(pattern, text)
    return [match.group(0)] if match else []


def wants_append(message: str) -> bool:
    compact = compact_text(message, limit=280)
    return bool(_match(APPEND_REQUEST_PATTERN, compact))


def wants_explain(message: str) -> bool:
    compact = compact_text(message, limit=280)
    return bool(_match(EXPLAIN_REQUEST_PATTERN, compact))


def wants_resource_reference(message: str) -> bool:
    compact = compact_text(message, limit=280)
    return bool(_match(RESOURCE_REFERENCE_HINT_PATTERN, compact))


def has_explicit_resource_reference(message: str) -> bool:
    compact = compact_text(message, limit=280)
    return bool(_match(EXPLICIT_RESOURCE_REFERENCE_PATTERN, compact))


def wants_sequential_explanation(message: str) -> bool:
    compact = compact_text(message, limit=120)
    return bool(_match(SEQUENTIAL_EXPLANATION_REQUEST_PATTERN, compact))


def has_collection_target(message: str) -> bool:
    compact = compact_text(message, limit=360)
    return bool(_match(COLLECTION_EXPLANATION_TARGET_PATTERN, compact))


def has_single_target(message: str) -> bool:
    compact = compact_text(message, limit=360)
    return bool(_match(SINGLE_EXPLANATION_TARGET_PATTERN, compact))


def wants_collection_explanation(message: str) -> bool:
    compact = compact_text(message, limit=360)
    return bool(
        compact
        and wants_explain(compact)
        and has_collection_target(compact)
        and not has_single_target(compact)
    )


def wants_whole_document_scope(message: str) -> bool:
    compact = compact_text(message, limit=300)
    return bool(_match(WHOLE_DOCUMENT_SCOPE_PATTERN, compact))


def wants_document_artifact_generation(message: str) -> bool:
    compact = compact_text(message, limit=280)
    return bool(_match(DOCUMENT_ARTIFACT_REQUEST_PATTERN, compact))


def wants_learning_start(message: str) -> bool:
    compact = compact_text(message, limit=280)
    return bool(_match(LEARNING_START_REQUEST_PATTERN, compact))


def is_followup_execution_request(message: str) -> bool:
    compact = compact_text(message, limit=80)
    return bool(_match(FOLLOWUP_EXECUTION_PATTERN, compact))


def should_force_explain_task(message: str) -> bool:
    if not _match(EXPLAIN_REQUEST_PATTERN, message):
        return False
    has_write_intent = wants_append(message) or bool(_match(EXPAND_REQUEST_PATTERN, message))
    if has_write_intent and not _match(STRONG_EXPLAIN_REQUEST_PATTERN, message):
        return False
    return True


def has_structured_location_hint(message: str) -> bool:
    return bool(_match(ORDINAL_HINT_PATTERN, compact_text(message, limit=280)))


def extract_target_hint(message: str) -> str:
    match = _match(TARGET_BEFORE_ACTION_PATTERN, message)
    if not match:
        return ""
    hint = compact_text(match.group("hint"), limit=160)
    hint = re.sub(r"^(请|帮我|你能不能|能不能|可以|可以为我|为我)\s*", "", hint)
    hint = hint.strip(" ：:，,。！？!?；;\"'“”‘’")
    return "" if is_only_generic_reference(hint) else hint


def is_only_generic_reference(message: str) -> bool:
    compact = re.sub(r"[\s，,。！？!?；;：:]+", "", message or "")
    return compact in {"这", "这个", "这里", "这段", "这一段", "这部分", "这个内容", "这个地方"}


def infer_board_task_requested_action(message: str) -> BoardTaskRequestedAction | None:
    text = compact_text(message, limit=280)
    has_write = bool(_match(BOARD_TASK_WRITE_PATTERN, text))
    if _match(BOARD_TASK_EXPLAIN_PATTERN, text) and (not has_write or _match(BOARD_TASK_STRONG_EXPLAIN_PATTERN, text)):
        return "explain"
    if _match(BOARD_TASK_EDIT_PATTERN, text):
        return "edit"
    if has_write:
        return "write"
    if _match(BOARD_TASK_CHAT_PATTERN, text):
        return "chat"
    return None


def extract_intent_signals(message: str) -> IntentSignals:
    text = compact_text(message, limit=360)
    raw_matches = {
        "write": _matched(BOARD_TASK_WRITE_PATTERN, text),
        "edit": _matched(BOARD_TASK_EDIT_PATTERN, text),
        "explain": _matched(EXPLAIN_REQUEST_PATTERN, text),
        "append": _matched(APPEND_REQUEST_PATTERN, text),
        "expand": _matched(EXPAND_REQUEST_PATTERN, text),
        "simplify": _matched(SIMPLIFY_REQUEST_PATTERN, text),
        "rewrite": _matched(REWRITE_REQUEST_PATTERN, text),
        "resource": _matched(RESOURCE_REFERENCE_HINT_PATTERN, text),
        "sequence": _matched(SEQUENTIAL_EXPLANATION_REQUEST_PATTERN, text),
        "collection": _matched(COLLECTION_EXPLANATION_TARGET_PATTERN, text),
        "whole_document": _matched(WHOLE_DOCUMENT_SCOPE_PATTERN, text),
        "single_target": _matched(SINGLE_EXPLANATION_TARGET_PATTERN, text),
        "target_hint": _matched(TARGET_LOCATION_HINT_PATTERN, text),
        "chat": _matched(BOARD_TASK_CHAT_PATTERN, text),
    }
    return IntentSignals(
        wants_write=bool(raw_matches["write"]),
        wants_edit=bool(raw_matches["edit"]),
        wants_explain=bool(raw_matches["explain"]),
        wants_append=bool(raw_matches["append"]),
        wants_expand=bool(raw_matches["expand"]),
        wants_simplify=bool(raw_matches["simplify"]),
        wants_rewrite=bool(raw_matches["rewrite"]),
        wants_resource=bool(raw_matches["resource"]),
        wants_sequence=bool(raw_matches["sequence"]),
        wants_collection=bool(raw_matches["collection"]),
        wants_whole_document=bool(raw_matches["whole_document"]),
        has_single_target=bool(raw_matches["single_target"]),
        has_target_hint=bool(raw_matches["target_hint"]),
        raw_matches=raw_matches,
        wants_chat=bool(raw_matches["chat"]),
    )
