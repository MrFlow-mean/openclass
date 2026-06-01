from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import RedirectResponse

from app.constants import AUTH_ERROR_ADMIN_REQUIRED
from app.models import (
    AdminAuditLogResponse,
    AdminOverview,
    AdminUserUpdateRequest,
    AuthEmailRequest,
    AuthMessageResponse,
    AuthPasswordResetRequest,
    AuthProviderView,
    AuthRequest,
    AuthSessionResponse,
    RegisterResponse,
    UserView,
)
from app.services.auth_service import (
    AUTH_COOKIE_NAME,
    GUEST_AUTH_COOKIE_NAME,
    SESSION_TTL,
    AuthService,
    bearer_token_from_request,
    bearer_token_from_websocket,
)
from app.services.workspace_state import DATABASE_PATH


router = APIRouter(prefix="/api")
auth_service = AuthService(DATABASE_PATH)


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded_proto.split(",", 1)[0].strip() == "https"


def _set_auth_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        path="/",
    )


def _set_guest_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        GUEST_AUTH_COOKIE_NAME,
        token,
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    secure = _is_secure_request(request)
    for name in (AUTH_COOKIE_NAME, GUEST_AUTH_COOKIE_NAME):
        response.delete_cookie(name, path="/", secure=secure, samesite="lax")


def _error_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"]
    if isinstance(detail, str):
        return detail
    return "Authentication failed"


def current_user(request: Request) -> UserView:
    token = bearer_token_from_request(request)
    return auth_service.get_user_by_token(token)


def optional_current_user(request: Request) -> UserView | None:
    try:
        token = bearer_token_from_request(request)
        return auth_service.get_user_by_token(token)
    except HTTPException:
        return None


def current_websocket_user(websocket: WebSocket) -> UserView:
    token = bearer_token_from_websocket(websocket)
    return auth_service.get_user_by_token(token)


def current_admin(user: UserView = Depends(current_user)) -> UserView:
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"code": AUTH_ERROR_ADMIN_REQUIRED, "message": "需要管理员权限"},
        )
    return user


@router.post("/auth/register", response_model=RegisterResponse)
def register(payload: AuthRequest, request: Request) -> RegisterResponse:
    result = auth_service.register(
        payload.account_identifier(),
        payload.password,
        request=request,
        next_path=payload.next_path,
        guest_token=payload.guest_token or request.cookies.get(GUEST_AUTH_COOKIE_NAME),
    )
    return RegisterResponse(email=result.email, verification_required=True)


@router.post("/auth/login", response_model=AuthSessionResponse)
def login(payload: AuthRequest, request: Request, response: Response) -> AuthSessionResponse:
    token, user = auth_service.login(
        payload.account_identifier(),
        payload.password,
        guest_token=payload.guest_token or request.cookies.get(GUEST_AUTH_COOKIE_NAME),
        user_agent=request.headers.get("user-agent"),
    )
    _clear_auth_cookies(response, request)
    _set_auth_cookie(response, request, token)
    return AuthSessionResponse(token=token, user=user)


@router.post("/auth/logout", response_model=AuthMessageResponse)
def logout(request: Request, response: Response) -> AuthMessageResponse:
    try:
        auth_service.logout(bearer_token_from_request(request))
    finally:
        _clear_auth_cookies(response, request)
    return AuthMessageResponse(message="已退出登录")


@router.post("/auth/logout-all", response_model=AuthMessageResponse)
def logout_all(request: Request, response: Response, user: UserView = Depends(current_user)) -> AuthMessageResponse:
    auth_service.logout_all(user.id)
    _clear_auth_cookies(response, request)
    return AuthMessageResponse(message="已退出全部会话")


@router.post("/auth/guest", response_model=AuthSessionResponse)
def guest(request: Request, response: Response) -> AuthSessionResponse:
    token, user = auth_service.start_guest_session()
    _clear_auth_cookies(response, request)
    _set_guest_cookie(response, request, token)
    return AuthSessionResponse(token=token, user=user)


@router.get("/auth/me", response_model=UserView)
def me(user: UserView = Depends(current_user)) -> UserView:
    return user


@router.post("/auth/email/resend", response_model=AuthMessageResponse)
def resend_verification(payload: AuthEmailRequest, request: Request) -> AuthMessageResponse:
    auth_service.resend_verification(
        payload.email,
        request=request,
        next_path=payload.next_path,
        guest_token=request.cookies.get(GUEST_AUTH_COOKIE_NAME),
    )
    return AuthMessageResponse(message="如果该邮箱需要验证，我们已经发送新的验证邮件")


@router.get("/auth/email/verify")
def verify_email(token: str, request: Request) -> RedirectResponse:
    try:
        session_token, _, next_path, frontend_origin = auth_service.verify_email(
            token,
            user_agent=request.headers.get("user-agent"),
        )
        response = RedirectResponse(
            auth_service.verification_frontend_redirect_url(next_path, frontend_origin, request),
            status_code=303,
        )
        _clear_auth_cookies(response, request)
        _set_auth_cookie(response, request, session_token)
        return response
    except HTTPException as exc:
        response = RedirectResponse(
            auth_service.verification_frontend_redirect_url("/", "", request, error=_error_message(exc)),
            status_code=303,
        )
        _clear_auth_cookies(response, request)
        return response


@router.post("/auth/password/forgot", response_model=AuthMessageResponse)
def forgot_password(payload: AuthEmailRequest, request: Request) -> AuthMessageResponse:
    auth_service.request_password_reset(payload.email, request=request)
    return AuthMessageResponse(message="如果该邮箱存在，我们已经发送密码重置邮件")


@router.post("/auth/password/reset", response_model=AuthMessageResponse)
def reset_password(payload: AuthPasswordResetRequest) -> AuthMessageResponse:
    auth_service.reset_password(payload.token, payload.password)
    return AuthMessageResponse(message="密码已重置，请重新登录")


@router.get("/auth/providers", response_model=list[AuthProviderView])
def auth_providers() -> list[AuthProviderView]:
    return auth_service.providers()


@router.get("/auth/oauth/{provider}/start")
def oauth_start(
    provider: str,
    request: Request,
    next: str = "/",  # noqa: A002
    guest_token: str | None = None,
) -> RedirectResponse:
    return RedirectResponse(
        auth_service.oauth_authorization_url(
            provider,
            next,
            request,
            guest_token=guest_token or request.cookies.get(GUEST_AUTH_COOKIE_NAME),
        ),
        status_code=303,
    )


@router.api_route("/auth/oauth/{provider}/callback", methods=["GET", "POST"])
async def oauth_callback(provider: str, request: Request) -> RedirectResponse:
    payload = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        payload.update({key: str(value) for key, value in form.items()})
    if payload.get("error"):
        target = auth_service.oauth_frontend_redirect_url(
            "/",
            "",
            request,
            error=str(payload.get("error_description") or payload["error"]),
        )
        return RedirectResponse(target, status_code=303)
    try:
        token, _, next_path, frontend_origin = auth_service.complete_oauth_callback(
            provider,
            payload,
            request,
            user_agent=request.headers.get("user-agent"),
        )
        response = RedirectResponse(
            auth_service.oauth_frontend_redirect_url(next_path, frontend_origin, request),
            status_code=303,
        )
        _clear_auth_cookies(response, request)
        _set_auth_cookie(response, request, token)
        return response
    except HTTPException as exc:
        target = auth_service.oauth_frontend_redirect_url("/", "", request, error=_error_message(exc))
        response = RedirectResponse(target, status_code=303)
        _clear_auth_cookies(response, request)
        return response


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: UserView = Depends(current_admin)) -> AdminOverview:
    return auth_service.overview()


@router.patch("/admin/users/{user_id}", response_model=UserView)
def admin_update_user(
    user_id: str,
    payload: AdminUserUpdateRequest,
    admin: UserView = Depends(current_admin),
) -> UserView:
    return auth_service.update_admin_user(actor=admin, target_user_id=user_id, role=payload.role, status=payload.status)


@router.post("/admin/users/{user_id}/sessions/revoke", response_model=AuthMessageResponse)
def admin_revoke_user_sessions(user_id: str, admin: UserView = Depends(current_admin)) -> AuthMessageResponse:
    auth_service.revoke_admin_user_sessions(actor=admin, target_user_id=user_id)
    return AuthMessageResponse(message="用户会话已撤销")


@router.get("/admin/audit-logs", response_model=AdminAuditLogResponse)
def admin_audit_logs(_: UserView = Depends(current_admin), limit: int = 100) -> AdminAuditLogResponse:
    return auth_service.audit_logs(limit=max(1, min(limit, 200)))
