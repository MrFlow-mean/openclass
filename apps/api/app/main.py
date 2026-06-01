from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import AIModelCatalog
from app.routers import auth, chat, documents, realtime, workspace
from app.services.ai_model_catalog import build_model_catalog, realtime_runtime_enabled
from app.services.openai_course_ai import openai_course_ai
from app.services.workspace_state import ensure_data_dirs

ensure_data_dirs()

app = FastAPI(title="AI Board Course System API", version="0.2.0")

cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
for origin in (os.getenv("OPENCLASS_PUBLIC_ORIGIN"), os.getenv("OPENCLASS_WEB_ORIGIN")):
    if origin and origin.rstrip("/") not in cors_origins:
        cors_origins.append(origin.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspace.router)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(realtime.router)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "openai": openai_course_ai.status(),
        "workflow": {"status": "chat_active"},
        "realtime": {"status": "enabled" if realtime_runtime_enabled() else "disabled"},
    }


@app.get("/api/ai-models", response_model=AIModelCatalog)
def get_ai_models() -> AIModelCatalog:
    return build_model_catalog()
