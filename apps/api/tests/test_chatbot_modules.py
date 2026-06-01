from __future__ import annotations


def test_chatbot_modules_import() -> None:
    from app.services import chatbot
    from app.services import chatbot_flow
    from app.services import chatbot_handlers
    from app.services import chatbot_patterns
    from app.services import chatbot_support

    assert callable(chatbot.process_chat_on_lesson)
    assert callable(chatbot.document_ai_edit_request)
    assert callable(chatbot_flow._chat_response)
    assert callable(chatbot_support._resource_resolution_query)
    assert hasattr(chatbot_patterns, "DOCUMENT_WRITE_ACTIONS")
    assert callable(chatbot_handlers._handle_document_lookup_request)
