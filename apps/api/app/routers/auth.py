from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.models import AdminOverview, AuthRequest, AuthSessionResponse, UserView
from app.services.auth_service import AuthService, bearer_token_from_request
from app.services.workspace_state import DATABASE_PATH


router = APIRouter(prefix="/api")
auth_service = AuthService(DATABASE_PATH)


def current_user(request: Request) -> UserView:
    token = bearer_token_from_request(request)
    return auth_service.get_user_by_token(token)


def current_admin(user: UserView = Depends(current_user)) -> UserView:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.post("/auth/register", response_model=AuthSessionResponse)
def register(payload: AuthRequest) -> AuthSessionResponse:
    token, user = auth_service.register(payload.email, payload.password)
    return AuthSessionResponse(token=token, user=user)


@router.post("/auth/login", response_model=AuthSessionResponse)
def login(payload: AuthRequest) -> AuthSessionResponse:
    token, user = auth_service.login(payload.email, payload.password)
    return AuthSessionResponse(token=token, user=user)


@router.get("/auth/me", response_model=UserView)
def me(user: UserView = Depends(current_user)) -> UserView:
    return user


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: UserView = Depends(current_admin)) -> AdminOverview:
    return auth_service.overview()
