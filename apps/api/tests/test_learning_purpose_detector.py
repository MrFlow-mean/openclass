import json

import pytest

from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.openai_course_ai import OpenAICourseAI


def test_learning_purpose_detection_normalizes_non_guidance_direction() -> None:
    detection = LearningPurposeDetection(
        has_learning_purpose=False,
        needs_guidance=True,
        guidance_direction="skill_practice",
        reason="没有学习目的时不应该保留引导方向。",
    )

    assert detection.to_prompt_payload() == {
        "has_learning_purpose": False,
        "needs_guidance": False,
        "guidance_direction": "none",
        "known_purpose": "",
        "missing_piece": "",
        "reason": "没有学习目的时不应该保留引导方向。",
    }


def test_learning_purpose_detector_prompt_has_two_gradual_guidance_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = OpenAICourseAI()
    captured: dict[str, object] = {}

    def _fake_parse(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        return LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            guidance_direction="knowledge_point",
            known_purpose="想学习一个笼统领域",
            missing_piece="还没有具体知识点",
            reason="用户表达了学习目的，但范围仍然模糊。",
        )

    monkeypatch.setattr(ai, "_parse", _fake_parse)

    result = ai.generate_learning_purpose_detection(
        conversation_summary="assistant: 可以聊聊。",
        board_document_state={
            "status": "empty",
            "is_empty": True,
            "chatbot_context": "当前右侧板书/文档框为空。",
            "content_visibility": "status_only",
        },
        user_message="我想学一个方向，但不知道从哪开始",
    )

    assert result == LearningPurposeDetection(
        has_learning_purpose=True,
        needs_guidance=True,
        guidance_direction="knowledge_point",
        known_purpose="想学习一个笼统领域",
        missing_piece="还没有具体知识点",
        reason="用户表达了学习目的，但范围仍然模糊。",
    )
    assert captured["role"] == "chatbot"
    assert captured["schema"] is LearningPurposeDetection
    assert "guidance_direction=knowledge_point" in captured["system_prompt"]
    assert "guidance_direction=skill_practice" in captured["system_prompt"]
    assert "笼统模糊" in captured["system_prompt"]
    assert "当前水平" in captured["system_prompt"]
    assert "不生成板书" in captured["system_prompt"]
    payload = json.loads(captured["user_prompt"])
    assert payload["user_message"] == "我想学一个方向，但不知道从哪开始"
    assert payload["response_contract"]["guidance_direction"] == "none、knowledge_point 或 skill_practice。"
