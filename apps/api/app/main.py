from __future__ import annotations

import os
from urllib import parse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.models import AIModelCatalog
from app.routers import auth, chat, collaboration, documents, realtime, resources, workspace
from app.services.ai_model_catalog import build_model_catalog, realtime_runtime_enabled
from app.services.email_delivery import delivery_status
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


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = parse.urlparse(referer)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return None


@app.middleware("http")
async def validate_unsafe_api_origin(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api"):
        origin = _request_origin(request)
        if origin and origin not in cors_origins:
            return JSONResponse(
                {"detail": {"code": "origin_not_allowed", "message": "请求来源不被允许"}},
                status_code=403,
            )
    return await call_next(request)

app.include_router(workspace.router)
app.include_router(auth.router)
app.include_router(collaboration.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(realtime.router)
app.include_router(resources.router)


@app.get("/health")
def health() -> dict[str, object]:
    mail = delivery_status()
    return {
        "status": "ok",
        "openai": openai_course_ai.status(),
        "workflow": {"status": "chat_active"},
        "realtime": {"status": "enabled" if realtime_runtime_enabled() else "disabled"},
        "mail": {"status": "configured" if mail.configured else "unconfigured", "mode": mail.mode},
    }


@app.get("/api/ai-models", response_model=AIModelCatalog)
def get_ai_models() -> AIModelCatalog:
    return build_model_catalog()
