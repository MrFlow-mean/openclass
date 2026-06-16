import pytest

from app.services.openai_course_ai import BoardDocumentQualityReview, openai_course_ai


@pytest.fixture(autouse=True)
def allow_default_board_document_quality_review(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_document_quality_review",
        lambda **kwargs: BoardDocumentQualityReview(
            status="pass",
            checked_dimensions=["title_terms", "definitions", "examples", "exercises", "answers", "scope", "structure"],
        ),
    )


@pytest.fixture(autouse=True)
def disable_default_initial_learning_work_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
