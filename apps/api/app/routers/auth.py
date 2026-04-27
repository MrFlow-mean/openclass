from __future__ import annotations

from urllib import parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import AdminOverview, AuthProviderView, AuthRequest, AuthSessionResponse, UserView
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


@router.get("/auth/providers", response_model=list[AuthProviderView])
def auth_providers() -> list[AuthProviderView]:
    return auth_service.providers()


@router.get("/auth/oauth/{provider}/start")
def oauth_start(provider: str, request: Request, next: str = "/") -> RedirectResponse:  # noqa: A002
    return RedirectResponse(auth_service.oauth_authorization_url(provider, next, request), status_code=303)


@router.api_route("/auth/oauth/{provider}/callback", methods=["GET", "POST"])
async def oauth_callback(provider: str, request: Request) -> RedirectResponse:
    payload = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        payload.update({key: str(value) for key, value in form.items()})
    if payload.get("error"):
        target = f"{str(request.base_url).rstrip('/')}/auth/callback?{parse.urlencode({'error': payload.get('error_description') or payload['error']})}"
        return RedirectResponse(target, status_code=303)
    token, user, next_path, frontend_origin = auth_service.complete_oauth_callback(provider, payload, request)
    return RedirectResponse(
        auth_service.oauth_frontend_redirect_url(token, user, next_path, frontend_origin, request),
        status_code=303,
    )


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: UserView = Depends(current_admin)) -> AdminOverview:
    return auth_service.overview()
