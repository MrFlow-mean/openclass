from app.services.board_document_sensor import detect_board_document_state
from app.services.rich_document import build_document


def test_board_document_sensor_reports_empty_and_non_empty_documents() -> None:
    empty_document = build_document(title="空白页")
    html_only_document = build_document(title="HTML 文档", content_html="<p>已有内容</p>")
    text_document = build_document(title="文本板书", content_text="已有板书内容")

    empty_state = detect_board_document_state(empty_document)
    html_state = detect_board_document_state(html_only_document)
    text_state = detect_board_document_state(text_document)

    assert empty_state.to_prompt_payload() == {
        "status": "empty",
        "is_empty": True,
        "chatbot_context": "当前右侧板书/文档框为空。",
        "content_visibility": "status_only",
    }
    assert html_state.status == "non_empty"
    assert html_state.is_empty is False
    assert text_state.to_prompt_payload() == {
        "status": "non_empty",
        "is_empty": False,
        "chatbot_context": "当前右侧板书/文档框不是空的，里面已有内容。",
        "content_visibility": "status_only",
    }
