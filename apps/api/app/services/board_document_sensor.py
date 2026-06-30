from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import BoardDocument
from app.services.rich_document import is_document_empty


BoardDocumentSensorStatus = Literal["empty", "non_empty"]


@dataclass(frozen=True)
class BoardDocumentSensorReading:
    status: BoardDocumentSensorStatus
    has_content: bool
    reason: str

    def model_context(self) -> dict[str, object]:
        return {
            "status": self.status,
            "has_content": self.has_content,
            "reason": self.reason,
        }


def read_board_document_sensor(document: BoardDocument) -> BoardDocumentSensorReading:
    if is_document_empty(document):
        return BoardDocumentSensorReading(
            status="empty",
            has_content=False,
            reason="当前右侧板书文档没有可见内容。",
        )
    return BoardDocumentSensorReading(
        status="non_empty",
        has_content=True,
        reason="当前右侧板书文档已有可见内容。",
    )
