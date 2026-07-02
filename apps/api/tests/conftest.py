import pytest

from app.services.openai_course_ai import openai_course_ai


@pytest.fixture(autouse=True)
def disable_default_initial_learning_work_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
