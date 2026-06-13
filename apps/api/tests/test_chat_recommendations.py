import json

from app.models import (
    ChatRequest,
    ConversationTurn,
    LearningClarificationStatus,
    LibraryChapter,
    ResourceChapterShard,
    ResourceLibraryItem,
)
from app.services.chat.recommendations import requirement_recommendation_context
from app.services.chat_turn_orchestrator import _chatbot_recommendation_context
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai


def _resource() -> ResourceLibraryItem:
    return ResourceLibraryItem(
        id="resource_learning_paths",
        name="上传资料",
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=1234,
        outline=[
            LibraryChapter(title="整体概览", summary="先建立资料主线。"),
            LibraryChapter(title="关键方法", summary="适合继续深入。"),
            LibraryChapter(title="额外章节", summary="不应进入推荐上下文。"),
        ],
        chapter_shards=[
            ResourceChapterShard(
                chapter_id="chapter_extra",
                title="正文片段",
                summary="不应在 outline 已足够时补入。",
                text_hash="hash_extra",
            )
        ],
        text_content="资料全文不应该进入推荐上下文" * 80,
    )


def test_requirement_recommendation_context_uses_short_sources_only() -> None:
    lesson = create_empty_lesson("学习工作台")
    assert lesson.learning_requirements is not None
    lesson.learning_requirements.level = "了解一点基础"
    lesson.history_graph.commits[-1].metadata["learning_clarification"] = {
        "key_facts": [
            {
                "label": "面向场景",
                "value": "希望用于实际任务",
                "evidence": "用户之前说过",
                "category": "scenario",
            }
        ]
    }

    context = requirement_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        resources=[_resource()],
        conversation=[ConversationTurn(role="user", content="我想更偏应用一点")],
        user_message="我也不知道具体学什么",
    )

    assert "了解一点基础" in context
    assert "希望用于实际任务" in context
    assert "整体概览" in context
    assert "关键方法" in context
    assert "额外章节" not in context
    assert "资料全文不应该进入推荐上下文" not in context


def test_requirement_recommendation_context_does_not_invent_without_sources() -> None:
    lesson = create_empty_lesson("学习工作台")
    assert lesson.learning_requirements is not None

    context = requirement_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        resources=[],
        conversation=[],
        user_message="不知道",
    )

    assert context == ""


def test_requirement_recommendation_context_can_use_current_user_signal() -> None:
    lesson = create_empty_lesson("学习工作台")
    assert lesson.learning_requirements is not None

    context = requirement_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        resources=[],
        conversation=[],
        user_message="我想更偏实际应用一点",
    )

    assert "当前用户表达：我想更偏实际应用一点" in context


def test_chatbot_recommendation_context_only_for_empty_board_clarification() -> None:
    lesson = create_empty_lesson("学习工作台")
    assert lesson.learning_requirements is not None
    learning_clarification = LearningClarificationStatus(
        progress=35,
        label="需求仍不清楚",
        reason="还需要继续澄清学习入口。",
    )
    resource = _resource()

    context = _chatbot_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        learning_clarification=learning_clarification,
        resources=[resource],
        conversation=[],
        request=ChatRequest(message="不知道学什么"),
        action_type=None,
        selected_reference=None,
        reference_prompt=None,
    )
    assert "整体概览" in context

    direct_edit_context = _chatbot_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        learning_clarification=learning_clarification,
        resources=[resource],
        conversation=[],
        request=ChatRequest(message="改一下", interaction_mode="direct_edit"),
        action_type=None,
        selected_reference=None,
        reference_prompt=None,
    )
    assert direct_edit_context == ""

    lesson.board_document.content_text = "已有板书内容"
    existing_board_context = _chatbot_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        learning_clarification=learning_clarification,
        resources=[resource],
        conversation=[],
        request=ChatRequest(message="不知道学什么"),
        action_type=None,
        selected_reference=None,
        reference_prompt=None,
    )
    assert existing_board_context == ""

    lesson.board_document.content_text = ""
    learning_clarification.ready_for_board = True
    ready_context = _chatbot_recommendation_context(
        lesson=lesson,
        requirements=lesson.learning_requirements,
        learning_clarification=learning_clarification,
        resources=[resource],
        conversation=[],
        request=ChatRequest(message="可以开始生成了"),
        action_type=None,
        selected_reference=None,
        reference_prompt=None,
    )
    assert ready_context == ""


def test_chatbot_prompt_carries_recommendation_contract(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_parse(role, system_prompt, user_prompt, schema, *, log_user_prompt=None):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return ChatbotReply(chatbot_message="我会先问一个问题，并给两个入口。")

    monkeypatch.setattr(openai_course_ai, "_parse", fake_parse)

    reply = openai_course_ai.generate_chatbot_reply(
        lesson_title="学习工作台",
        learning_goal="先澄清需求",
        board_summary="",
        resource_summary="上传资料：整体概览",
        conversation_summary="",
        user_message="不知道学什么",
        recommendation_context="可参考资料入口：上传资料 / 整体概览",
    )

    assert reply is not None
    assert captured["role"] == "chatbot"
    assert "最多 2 个" in captured["system_prompt"]
    assert "不得脑补用户没有透露" in captured["system_prompt"]
    payload = json.loads(captured["user_prompt"])
    assert payload["recommendation_context"] == "可参考资料入口：上传资料 / 整体概览"
