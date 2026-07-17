from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.models import AIModelSelection, SelectionRef, UserView
from app.routers.auth import current_user
from app.services.ai_execution_adapter import CodexAIExecutionAdapter
from app.services.ai_model_catalog import default_text_selection
from app.services.codex_app_server import CodexAppServerError
from app.services.geometry_scene import GeometryScene, generate_geometry_scene
from app.services.workspace_state import find_lesson_package, load_workspace_for_user


router = APIRouter(prefix="/api")


class GeometryGenerationRequest(BaseModel):
    selection: SelectionRef
    instructions: str = Field(default="", max_length=2000)
    text_model: AIModelSelection | None = None


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _validated_board_excerpt(lesson_id: str, selection: SelectionRef, document_text: str) -> str:
    excerpt = selection.excerpt.strip()
    if selection.kind != "board":
        raise HTTPException(status_code=422, detail="只能引用当前板书内容生成图形")
    if selection.lesson_id and selection.lesson_id != lesson_id:
        raise HTTPException(status_code=409, detail="引用内容不属于当前页面")
    if not excerpt:
        raise HTTPException(status_code=422, detail="请先在板书中引用公式或题目")
    if len(excerpt) > 6000:
        raise HTTPException(status_code=422, detail="引用内容过长，请缩小选区后重试")
    normalized_excerpt = _normalized_text(excerpt)
    if normalized_excerpt not in _normalized_text(document_text):
        raise HTTPException(status_code=409, detail="引用内容已变化，请回到板书重新引用")
    return excerpt


@router.post("/lessons/{lesson_id}/geometry/generate", response_model=GeometryScene)
def create_geometry_scene(
    lesson_id: str,
    payload: GeometryGenerationRequest,
    user: UserView = Depends(current_user),
) -> GeometryScene:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    if payload.selection.document_id and payload.selection.document_id != lesson.board_document.id:
        raise HTTPException(status_code=409, detail="引用内容不属于当前板书版本")
    excerpt = _validated_board_excerpt(
        lesson_id,
        payload.selection,
        lesson.board_document.content_text,
    )
    selected_model = payload.text_model or default_text_selection()
    if selected_model.provider != "openai_codex":
        raise HTTPException(status_code=422, detail="当前图形生成只支持已连接的 Codex 文本模型")
    try:
        return generate_geometry_scene(
            adapter=CodexAIExecutionAdapter(
                owner_user_id=user.id,
                model=selected_model.model,
            ),
            source_excerpt=excerpt,
            instructions=payload.instructions,
        )
    except CodexAppServerError as exc:
        raise HTTPException(status_code=503, detail="图形生成模型暂不可用，请检查模型连接") from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="模型没有生成有效的图形结构，请调整引用内容后重试") from exc
