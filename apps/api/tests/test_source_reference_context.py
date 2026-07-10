from app.models import ChatRequest, SelectionRef
from app.services.source_reference_context import board_target_selection, source_aware_user_message


def _source_selection() -> SelectionRef:
    return SelectionRef(
        kind="source",
        excerpt="《资料》 · 2.1 核心章节 · p. 12",
        heading_path=["第二部分", "2.1 核心章节"],
        source_ingestion_id="source_reference_test",
        source_title="资料",
        source_chapter_id="sourcechapter_reference_test",
        source_chapter_number="2.1",
        source_chapter_title="核心章节",
        source_page_range="p. 12",
    )


def test_source_reference_builds_retrieval_context_without_changing_visible_message() -> None:
    request = ChatRequest(message="请讲解这一章。", selection=_source_selection())

    prompt_message = source_aware_user_message(request)
    retrieval_message = source_aware_user_message(request, include_locator=True)

    assert request.message == "请讲解这一章。"
    assert "《资料》" in prompt_message
    assert "2.1 核心章节" in prompt_message
    assert "source_chapter_id=" not in prompt_message
    assert "source_chapter_id=sourcechapter_reference_test" in retrieval_message


def test_source_reference_is_evidence_context_not_board_target() -> None:
    source_selection = _source_selection()
    board_selection = SelectionRef(kind="board", excerpt="板书中的目标段落")

    assert board_target_selection(source_selection) is None
    assert board_target_selection(board_selection) == board_selection
