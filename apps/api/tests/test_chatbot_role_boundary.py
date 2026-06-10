from __future__ import annotations

from pathlib import Path

from app.services import chatbot, chat_service


def test_chatbot_module_does_not_export_orchestration_entrypoints() -> None:
    assert not hasattr(chatbot, "process_chat_on_lesson")
    assert not hasattr(chatbot, "document_ai_edit_request")


def test_chatbot_module_has_no_orchestration_imports() -> None:
    source = Path(chatbot.__file__).read_text()
    forbidden_tokens = [
        "workspace_state",
        "commit_operations",
        "edit_existing_document",
        "run_initial_board_generation",
        "handle_existing_board_task_flow",
        "LearningRequirementHistoryRecorder",
        "BoardTaskHistoryRecorder",
        "resolve_resource_reference",
        "resolve_board_focus",
        "chat_turn_orchestrator",
    ]
    for token in forbidden_tokens:
        assert token not in source


def test_chat_service_uses_turn_orchestrator() -> None:
    source = Path(chat_service.__file__).read_text()
    assert "app.services.chat_turn_orchestrator" in source
    assert "app.services.chatbot import process_chat_on_lesson" not in source
    assert "app.services.chatbot import document_ai_edit_request" not in source
