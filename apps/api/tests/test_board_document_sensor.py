from app.services.board_document_sensor import read_board_document_sensor
from app.services.rich_document import build_document


def test_board_document_sensor_reports_empty_document_without_body_text() -> None:
    document = build_document(title="空白板书", content_text="")

    reading = read_board_document_sensor(document)

    assert reading.model_context() == {
        "status": "empty",
        "has_content": False,
        "reason": "当前右侧板书文档没有可见内容。",
    }


def test_board_document_sensor_reports_non_empty_document_without_body_text() -> None:
    document = build_document(title="已有板书", content_text="这段内容不能传给 Chatbot。")

    reading = read_board_document_sensor(document)

    assert reading.model_context() == {
        "status": "non_empty",
        "has_content": True,
        "reason": "当前右侧板书文档已有可见内容。",
    }
    assert "这段内容" not in str(reading.model_context())
