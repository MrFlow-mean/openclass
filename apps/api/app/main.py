from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import AIModelCatalog
from app.routers import chat, documents, realtime, resources, workspace
from app.services.ai_model_catalog import build_model_catalog
from app.services.openai_course_ai import openai_course_ai
from app.services.openai_realtime import google_realtime_teacher, openai_realtime_teacher
from app.services.workspace_state import ensure_data_dirs

ensure_data_dirs()

app = FastAPI(title="AI Board Course System API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspace.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(realtime.router)
app.include_router(resources.router)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "openai": openai_course_ai.status(),
        "realtime": {
            "openai": openai_realtime_teacher.status(),
            "google": google_realtime_teacher.status(),
        },
    }


@app.get("/api/ai-models", response_model=AIModelCatalog)
def get_ai_models() -> AIModelCatalog:
    return build_model_catalog()
