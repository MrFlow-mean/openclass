from fastapi import HTTPException

from app.models import BoardDocument, ChatRequest


def ensure_request_targets_current_document(request: ChatRequest, document: BoardDocument) -> None:
    if request.document_id is None or request.document_id == document.id:
        return
    raise HTTPException(status_code=403, detail="当前对话只能访问右侧文档")
