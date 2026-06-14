from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models import CodexLoginStartResponse, CodexLoginStatusResponse, CodexProviderStatus, UserView
from app.routers.auth import current_user
from app.services.codex_app_server import (
    CodexAppServerError,
    cancel_codex_login,
    codex_login_status,
    codex_provider_status,
    logout_codex,
    start_codex_device_login,
)

router = APIRouter(prefix="/api/codex")


def _codex_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/status", response_model=CodexProviderStatus)
def status(_: UserView = Depends(current_user), include_rate_limits: bool = False) -> CodexProviderStatus:
    return codex_provider_status(refresh=False, include_rate_limits=include_rate_limits)


@router.post("/login/device", response_model=CodexLoginStartResponse)
def login_device(_: UserView = Depends(current_user)) -> CodexLoginStartResponse:
    try:
        return start_codex_device_login()
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


@router.get("/login/{login_id}", response_model=CodexLoginStatusResponse)
def login_status(login_id: str, _: UserView = Depends(current_user)) -> CodexLoginStatusResponse:
    try:
        return codex_login_status(login_id)
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


@router.post("/login/{login_id}/cancel", response_model=CodexLoginStatusResponse)
def login_cancel(login_id: str, _: UserView = Depends(current_user)) -> CodexLoginStatusResponse:
    try:
        return cancel_codex_login(login_id)
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc


@router.post("/logout")
def logout(_: UserView = Depends(current_user)) -> dict[str, bool]:
    try:
        logout_codex()
    except CodexAppServerError as exc:
        raise _codex_error(exc) from exc
    return {"ok": True}
