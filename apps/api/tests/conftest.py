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
