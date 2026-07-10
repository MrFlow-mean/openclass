import pytest

from app.services.openai_course_ai import ChatbotReply, openai_course_ai


@pytest.fixture(autouse=True)
def disable_default_initial_learning_work_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_intake_reply",
        lambda **kwargs: ChatbotReply(chatbot_message=kwargs.get("requirement_reply_draft", "")),
    )
