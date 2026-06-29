from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import BoardDocument
from app.services.rich_document import is_document_empty


BoardDocumentPresence = Literal["empty", "non_empty"]


@dataclass(frozen=True)
class BoardDocumentSensorState:
    status: BoardDocumentPresence
    is_empty: bool
    chatbot_context: str

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "is_empty": self.is_empty,
            "chatbot_context": self.chatbot_context,
            "content_visibility": "status_only",
        }


def detect_board_document_state(document: BoardDocument) -> BoardDocumentSensorState:
    document_is_empty = is_document_empty(document)
    if document_is_empty:
        return BoardDocumentSensorState(
            status="empty",
            is_empty=True,
            chatbot_context="当前右侧板书/文档框为空。",
        )
    return BoardDocumentSensorState(
        status="non_empty",
        is_empty=False,
        chatbot_context="当前右侧板书/文档框不是空的，里面已有内容。",
    )
