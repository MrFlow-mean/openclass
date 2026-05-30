from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request as urlrequest

import certifi
from fastapi import HTTPException, Request, WebSocket

from app.models import (
    AdminAuditLogView,
    AdminAuditLogResponse,
    AdminOverview,
    AdminStats,
    AuthIdentityView,
    AuthProviderView,
    UserView,
    new_id,
)
from app.services.auth_store import AuthStore
from app.services.email_delivery import delivery_status, send_transactional_email


PBKDF2_ITERATIONS = 210_000
SESSION_TOKEN_BYTES = 32
OAUTH_STATE_BYTES = 24
OAUTH_STATE_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)
EMAIL_VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
_URLLIB_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
AUTH_COOKIE_NAME = "openclass.auth.token"
GUEST_AUTH_COOKIE_NAME = "openclass.guest.auth.token"
PHONE_EMAIL_DOMAIN = "phone.openclass.local"
GUEST_EMAIL_DOMAIN = "guest.openclass.local"
OAUTH_EMAIL_DOMAIN = "oauth.openclass.local"


@dataclass(frozen=True)
class AccountIdentifier:
    kind: str
    subject: str
    email: str | None = None
    phone: str | None = None

    @property
    def storage_email(self) -> str:
        if self.email:
            return self.email
        if self.phone:
            return _synthetic_email_for_phone(self.phone)
        _raise_auth_error(422, "invalid_account", "请输入有效邮箱")

    @property
    def display_name(self) -> str:
        if self.email:
            return self.email.split("@", 1)[0]
        if self.phone:
            return _mask_phone(self.phone)
        return "OpenClass user"


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str
    email: str | None
    display_name: str | None = None
    avatar_url: str | None = None
    email_verified: bool = False


@dataclass(frozen=True)
class OAuthProviderConfig:
    id: str
    label: str
    description: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_env: str
    client_secret_env: str
    userinfo_url: str | None = None
    email_url: str | None = None
    response_mode: str | None = None
    pkce: bool = False
    token_auth_method: str = "body"

    @property
    def client_id(self) -> str | None:
        return _normalize_optional_secret(os.getenv(self.client_id_env))

    @property
    def client_secret(self) -> str | None:
        return _normalize_optional_secret(os.getenv(self.client_secret_env))

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class RegistrationResult:
    email: str
    verification_required: bool = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_optional_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower().startswith("your_") or normalized.lower() in {"changeme", "todo"}:
        return None
    return normalized


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        _raise_auth_error(422, "invalid_email", "请输入有效邮箱")
    return normalized


def _normalize_phone(phone: str) -> str:
    compact = re.sub(r"[\s().-]", "", phone.strip())
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("0086"):
        compact = compact[4:]
    if not re.fullmatch(r"1[3-9]\d{9}", compact):
        _raise_auth_error(422, "invalid_phone", "请输入有效手机号")
    return compact


def _normalize_account_identifier(identifier: str) -> AccountIdentifier:
    raw = identifier.strip()
    if not raw:
        _raise_auth_error(422, "invalid_account", "请输入有效邮箱")
    if "@" in raw:
        email = _normalize_email(raw)
        return AccountIdentifier(kind="email", subject=email, email=email)
    try:
        phone = _normalize_phone(raw)
    except HTTPException:
        _raise_auth_error(422, "invalid_account", "请输入有效邮箱")
    return AccountIdentifier(kind="phone", subject=phone, phone=phone)


def _synthetic_email_for_phone(phone: str) -> str:
    digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]
    return f"phone-{digest}@{PHONE_EMAIL_DOMAIN}"


def _synthetic_email_for_guest(guest_user_id: str) -> str:
    return f"{guest_user_id}@{GUEST_EMAIL_DOMAIN}"


def _mask_phone(phone: str) -> str:
    if len(phone) == 11:
        return f"{phone[:3]}****{phone[-4:]}"
    if len(phone) <= 4:
        return phone
    return f"{phone[:2]}****{phone[-2:]}"


def _admin_emails() -> set[str]:
    raw = os.getenv("OPENCLASS_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _public_origin(request: Request | None = None) -> str:
    configured = _normalize_optional_secret(os.getenv("OPENCLASS_PUBLIC_ORIGIN") or os.getenv("OPENCLASS_WEB_ORIGIN"))
    if configured:
        return configured.rstrip("/")
    if request is not None:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if forwarded_proto and forwarded_host:
            return f"{forwarded_proto.split(',')[0].strip()}://{forwarded_host.split(',')[0].strip()}".rstrip("/")
        return str(request.base_url).rstrip("/")
    return "http://localhost:3000"


def _frontend_origin(request: Request | None = None) -> str:
    configured = _normalize_optional_secret(os.getenv("OPENCLASS_WEB_ORIGIN") or os.getenv("OPENCLASS_PUBLIC_ORIGIN"))
    if configured:
        return configured.rstrip("/")
    if request is not None:
        origin = request.headers.get("origin")
        if origin:
            return origin.rstrip("/")
        referer = request.headers.get("referer")
        if referer:
            parsed = parse.urlparse(referer)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        base = _public_origin(request)
        parsed_base = parse.urlparse(base)
        if parsed_base.hostname in {"localhost", "127.0.0.1"} and parsed_base.port == 8000:
            return f"{parsed_base.scheme}://{parsed_base.hostname}:3000"
        return base
    return "http://localhost:3000"


def _safe_next_path(value: str | None) -> str:
    if not value:
        return "/"
    parsed = parse.urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/"):
        return "/"
    if path in {"/login", "/register"}:
        return "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if len(password) < 8:
        _raise_auth_error(422, "password_too_short", "密码至少需要 8 位")
    password_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), password_salt, PBKDF2_ITERATIONS)
    return password_salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    _, candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, hash_hex)


def _provider_label(provider: str) -> str:
    return {
        "email": "邮箱",
        "phone": "手机号",
        "google": "Google",
        "apple": "Apple",
        "github": "GitHub",
        "wechat": "微信",
        "microsoft": "Microsoft",
        "x": "X",
    }.get(provider, provider)


def _oauth_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def _oauth_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _raise_auth_error(status_code: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _identity_view(row: sqlite3.Row) -> AuthIdentityView:
    return AuthIdentityView(
        provider=row["provider"],
        provider_label=_provider_label(row["provider"]),
        email=row["email"],
        display_name=row["display_name"],
        avatar_url=row["avatar_url"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def _user_view(row: sqlite3.Row, identities: list[AuthIdentityView] | None = None) -> UserView:
    return UserView(
        id=row["id"],
        email=row["email"],
        phone=_row_value(row, "phone"),
        role=row["role"],  # type: ignore[arg-type]
        status=_row_value(row, "status", "active"),
        display_name=_row_value(row, "display_name"),
        avatar_url=_row_value(row, "avatar_url"),
        created_at=row["created_at"],
        updated_at=_row_value(row, "updated_at"),
        last_login_at=row["last_login_at"],
        email_verified_at=_row_value(row, "email_verified_at"),
        session_count=_row_value(row, "session_count"),
        package_count=_row_value(row, "package_count"),
        auth_identities=identities or [],
    )


def _audit_log_view(row: sqlite3.Row) -> AdminAuditLogView:
    try:
        metadata = json.loads(row["metadata_json"])
    except (TypeError, ValueError):
        metadata = {}
    return AdminAuditLogView(
        id=row["id"],
        actor_user_id=row["actor_user_id"],
        target_user_id=row["target_user_id"],
        action=row["action"],
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=row["created_at"],
        actor_email=row["actor_email"],
        target_email=row["target_email"],
    )


def _oauth_providers() -> dict[str, OAuthProviderConfig]:
    return {
        "google": OAuthProviderConfig(
            id="google",
            label="Google",
            description="使用 Google 账号登录开放课堂。",
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            scopes=("openid", "email", "profile"),
            client_id_env="OPENCLASS_OAUTH_GOOGLE_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_GOOGLE_CLIENT_SECRET",
        ),
        "apple": OAuthProviderConfig(
            id="apple",
            label="Apple",
            description="使用 Apple ID 登录开放课堂。",
            auth_url="https://appleid.apple.com/auth/authorize",
            token_url="https://appleid.apple.com/auth/token",
            scopes=("name", "email"),
            client_id_env="OPENCLASS_OAUTH_APPLE_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_APPLE_CLIENT_SECRET",
            response_mode="form_post",
        ),
        "github": OAuthProviderConfig(
            id="github",
            label="GitHub",
            description="使用 GitHub 账号登录开放课堂。",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            userinfo_url="https://api.github.com/user",
            email_url="https://api.github.com/user/emails",
            scopes=("read:user", "user:email"),
            client_id_env="OPENCLASS_OAUTH_GITHUB_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_GITHUB_CLIENT_SECRET",
        ),
        "wechat": OAuthProviderConfig(
            id="wechat",
            label="微信",
            description="使用微信扫码登录开放课堂。",
            auth_url="https://open.weixin.qq.com/connect/qrconnect",
            token_url="https://api.weixin.qq.com/sns/oauth2/access_token",
            userinfo_url="https://api.weixin.qq.com/sns/userinfo",
            scopes=("snsapi_login",),
            client_id_env="OPENCLASS_OAUTH_WECHAT_APP_ID",
            client_secret_env="OPENCLASS_OAUTH_WECHAT_APP_SECRET",
        ),
        "microsoft": OAuthProviderConfig(
            id="microsoft",
            label="Microsoft",
            description="使用 Microsoft 账号登录开放课堂。",
            auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            userinfo_url="https://graph.microsoft.com/oidc/userinfo",
            scopes=("openid", "email", "profile"),
            client_id_env="OPENCLASS_OAUTH_MICROSOFT_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_MICROSOFT_CLIENT_SECRET",
        ),
        "x": OAuthProviderConfig(
            id="x",
            label="X",
            description="使用 X 账号登录开放课堂。",
            auth_url="https://x.com/i/oauth2/authorize",
            token_url="https://api.x.com/2/oauth2/token",
            userinfo_url="https://api.x.com/2/users/me?user.fields=profile_image_url",
            scopes=("users.read",),
            client_id_env="OPENCLASS_OAUTH_X_CLIENT_ID",
            client_secret_env="OPENCLASS_OAUTH_X_CLIENT_SECRET",
            pkce=True,
            token_auth_method="basic",
        ),
    }


def _public_provider_ids() -> list[str]:
    raw = (os.getenv("OPENCLASS_AUTH_PUBLIC_PROVIDERS") or "").strip()
    if not raw:
        return ["google", "wechat", "github"]
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _post_form(url: str, data: dict[str, str], *, basic_auth: tuple[str, str] | None = None) -> dict[str, Any]:
    encoded = parse.urlencode(data).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "OpenClass OAuth",
    }
    if basic_auth is not None:
        username, password = basic_auth
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    req = urlrequest.Request(
        url,
        data=encoded,
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=12, context=_URLLIB_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        _raise_auth_error(502, "oauth_token_exchange_failed", "第三方登录令牌交换失败")
        raise exc


def _get_json(url: str, access_token: str) -> dict[str, Any] | list[Any]:
    req = urlrequest.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "OpenClass OAuth",
        },
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=12, context=_URLLIB_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        _raise_auth_error(502, "oauth_profile_failed", "第三方账号资料读取失败")
        raise exc


def _get_json_with_params(url: str, params: dict[str, str]) -> dict[str, Any] | list[Any]:
    target = f"{url}?{parse.urlencode(params)}"
    req = urlrequest.Request(
        target,
        headers={
            "Accept": "application/json",
            "User-Agent": "OpenClass OAuth",
        },
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=12, context=_URLLIB_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        _raise_auth_error(502, "oauth_profile_failed", "第三方账号资料读取失败")
        raise exc


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        _, payload, _ = token.split(".", 2)
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except Exception as exc:
        _raise_auth_error(502, "oauth_id_token_failed", "第三方身份令牌解析失败")
        raise exc


class AuthService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.store = AuthStore(path)

    def register(
        self,
        identifier: str,
        password: str,
        *,
        request: Request | None = None,
        next_path: str | None = None,
        guest_token: str | None = None,
    ) -> RegistrationResult:
        account = _normalize_account_identifier(identifier)
        if not account.email:
            _raise_auth_error(422, "email_required", "请使用邮箱注册")
        if not delivery_status().configured:
            _raise_auth_error(503, "mail_delivery_unconfigured", "邮件服务尚未配置，暂时无法注册")
        salt, password_hash = _hash_password(password)
        created_at = _now_iso()
        verification_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        user_id = new_id("user")
        frontend_origin = _frontend_origin(request)
        normalized_next = _safe_next_path(next_path)
        guest_user_id = self._guest_user_id_for_token(guest_token)

        with self.store.transaction() as conn:
            user_count = self.store.user_count(conn)
            role = "admin" if user_count == 0 or account.email in _admin_emails() else "user"
            try:
                self.store.create_password_user(
                    conn,
                    user_id=user_id,
                    email=account.storage_email,
                    phone=None,
                    password_salt=salt,
                    password_hash=password_hash,
                    role=role,
                    display_name=account.display_name,
                    created_at=created_at,
                    email_verified_at=None,
                )
                self.store.create_identity(
                    conn,
                    provider="email",
                    provider_subject=account.email,
                    user_id=user_id,
                    email=account.email,
                    display_name=account.display_name,
                    created_at=created_at,
                    last_login_at=None,
                )
                self.store.create_email_verification(
                    conn,
                    token_hash=self._token_hash(verification_token),
                    user_id=user_id,
                    email=account.email,
                    next_path=normalized_next,
                    frontend_origin=frontend_origin,
                    guest_user_id=guest_user_id,
                    created_at=created_at,
                    expires_at=_future_iso(EMAIL_VERIFICATION_TTL),
                )
            except sqlite3.IntegrityError as exc:
                _raise_auth_error(409, "email_already_registered", "该邮箱已注册")
                raise exc

        self._send_verification_email(account.email, verification_token, request)
        return RegistrationResult(email=account.email)

    def resend_verification(
        self,
        email: str,
        *,
        request: Request | None = None,
        next_path: str | None = None,
        guest_token: str | None = None,
    ) -> None:
        normalized_email = _normalize_email(email)
        if not delivery_status().configured:
            _raise_auth_error(503, "mail_delivery_unconfigured", "邮件服务尚未配置，暂时无法发送验证邮件")
        verification_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        should_send = False
        guest_user_id = self._guest_user_id_for_token(guest_token)

        with self.store.transaction() as conn:
            user = self.store.find_user_by_email(conn, normalized_email)
            if user is not None and user["status"] == "active" and user["email_verified_at"] is None:
                self.store.create_email_verification(
                    conn,
                    token_hash=self._token_hash(verification_token),
                    user_id=user["id"],
                    email=normalized_email,
                    next_path=_safe_next_path(next_path),
                    frontend_origin=_frontend_origin(request),
                    guest_user_id=guest_user_id,
                    created_at=now,
                    expires_at=_future_iso(EMAIL_VERIFICATION_TTL),
                )
                should_send = True
        if should_send:
            self._send_verification_email(normalized_email, verification_token, request)

    def verify_email(self, token: str, *, user_agent: str | None = None) -> tuple[str, UserView, str, str]:
        token_hash = self._token_hash(token)
        now = _now_iso()
        with self.store.transaction() as conn:
            verification = self.store.consume_email_verification(conn, token_hash=token_hash, now=now)
            if verification is None:
                _raise_auth_error(400, "email_verification_invalid", "邮箱验证链接已失效，请重新发送验证邮件")
            user = self.store.find_user_by_id(conn, verification["user_id"])
            if user is None:
                _raise_auth_error(404, "user_not_found", "用户不存在")
            if user["status"] != "active":
                _raise_auth_error(403, "account_disabled", "该账号已被禁用")
            self.store.mark_email_verified(conn, user_id=user["id"], now=now)
            self._claim_guest_workspace_by_id(conn, guest_user_id=verification["guest_user_id"], user_id=user["id"])
            session_token, session_user = self._issue_session_for_user_id(conn, user["id"], user_agent=user_agent)
            return session_token, session_user, _safe_next_path(verification["next_path"]), verification["frontend_origin"] or _frontend_origin(None)

    def login(
        self,
        identifier: str,
        password: str,
        *,
        guest_token: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, UserView]:
        account = _normalize_account_identifier(identifier)
        with self.store.transaction() as conn:
            row = (
                self.store.find_user_by_phone(conn, account.phone or "")
                if account.kind == "phone"
                else self.store.find_user_by_email(conn, account.email or "")
            )
            if row is None or not _verify_password(password, row["password_salt"], row["password_hash"]):
                _raise_auth_error(401, "invalid_credentials", "账号或密码不正确")
            if row["status"] != "active":
                _raise_auth_error(403, "account_disabled", "该账号已被禁用")
            if account.kind == "email" and row["email_verified_at"] is None:
                _raise_auth_error(403, "email_not_verified", "请先验证邮箱后再登录")
            now = _now_iso()
            self.store.touch_password_login(conn, user_id=row["id"], provider=account.kind, now=now)
            self._claim_guest_workspace(conn, guest_token=guest_token, user_id=row["id"])
            return self._issue_session_for_user_id(conn, row["id"], user_agent=user_agent)

    def login_with_oauth(
        self,
        profile: OAuthProfile,
        *,
        guest_user_id: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, UserView]:
        if not profile.provider or not profile.subject:
            _raise_auth_error(422, "oauth_profile_incomplete", "第三方账号资料不完整")
        provider = profile.provider.strip().lower()
        subject = profile.subject.strip()
        verified_email = self._verified_email_for_oauth_profile(profile)
        storage_email = verified_email or self._synthetic_email_for_oauth_profile(profile)
        now = _now_iso()

        with self.store.transaction() as conn:
            identity = self.store.find_user_by_oauth_identity(conn, provider=provider, provider_subject=subject)
            if identity is not None:
                if identity["status"] != "active":
                    _raise_auth_error(403, "account_disabled", "该账号已被禁用")
                user_id = identity["id"]
            else:
                existing = self.store.find_user_by_email(conn, verified_email) if verified_email else None
                if existing is not None:
                    if existing["status"] != "active":
                        _raise_auth_error(403, "account_disabled", "该账号已被禁用")
                    user_id = existing["id"]
                else:
                    user_id = new_id("user")
                    password_salt, password_hash = _hash_password(secrets.token_urlsafe(32))
                    user_count = self.store.user_count(conn)
                    role = "admin" if user_count == 0 or storage_email in _admin_emails() else "user"
                    self.store.create_oauth_user(
                        conn,
                        user_id=user_id,
                        email=storage_email,
                        password_salt=password_salt,
                        password_hash=password_hash,
                        role=role,
                        display_name=profile.display_name or storage_email.split("@", 1)[0],
                        avatar_url=profile.avatar_url,
                        email_verified_at=now if verified_email else None,
                        now=now,
                    )
                self.store.create_identity(
                    conn,
                    provider=provider,
                    provider_subject=subject,
                    user_id=user_id,
                    email=profile.email,
                    display_name=profile.display_name,
                    avatar_url=profile.avatar_url,
                    created_at=now,
                    last_login_at=now,
                )

            self.store.touch_oauth_identity(
                conn,
                provider=provider,
                provider_subject=subject,
                email=profile.email,
                display_name=profile.display_name,
                avatar_url=profile.avatar_url,
                now=now,
            )
            self.store.touch_oauth_user_profile(
                conn,
                user_id=user_id,
                display_name=profile.display_name,
                avatar_url=profile.avatar_url,
                email_verified_at=now if verified_email else None,
                now=now,
            )
            self._claim_guest_workspace_by_id(conn, guest_user_id=guest_user_id, user_id=user_id)
            return self._issue_session_for_user_id(conn, user_id, user_agent=user_agent)

    def get_user_by_token(self, token: str) -> UserView:
        token_hash = self._token_hash(token)
        now = _now_iso()
        with self.store.transaction() as conn:
            row = self.store.find_user_by_session_token(conn, token_hash, now)
            if row is None:
                guest = self.store.find_guest_session_by_token(conn, token_hash)
                if guest is None:
                    _raise_auth_error(401, "unauthenticated", "请先登录")
                self.store.touch_guest_session(conn, token_hash, now)
                return self._guest_user_view(guest["guest_user_id"], guest["created_at"], guest["last_seen_at"])
            self.store.touch_session(conn, token_hash, now)
            return _user_view(row, self._identities_for_user(conn, row["id"]))

    def start_guest_session(self) -> tuple[str, UserView]:
        guest_user_id = new_id("guest")
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        with self.store.transaction() as conn:
            self.store.create_guest_session(
                conn,
                token_hash=self._token_hash(token),
                guest_user_id=guest_user_id,
                now=now,
            )
        return token, self._guest_user_view(guest_user_id, now, now)

    def logout(self, token: str) -> None:
        with self.store.transaction() as conn:
            self.store.revoke_session(conn, token_hash=self._token_hash(token), now=_now_iso())

    def logout_all(self, user_id: str) -> None:
        with self.store.transaction() as conn:
            self.store.revoke_user_sessions(conn, user_id=user_id, now=_now_iso())

    def request_password_reset(self, email: str, *, request: Request | None = None) -> None:
        normalized_email = _normalize_email(email)
        if not delivery_status().configured:
            _raise_auth_error(503, "mail_delivery_unconfigured", "邮件服务尚未配置，暂时无法发送重置邮件")
        reset_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        should_send = False
        with self.store.transaction() as conn:
            user = self.store.find_user_by_email(conn, normalized_email)
            if user is not None and user["status"] == "active" and user["email_verified_at"] is not None:
                self.store.create_password_reset(
                    conn,
                    token_hash=self._token_hash(reset_token),
                    user_id=user["id"],
                    created_at=now,
                    expires_at=_future_iso(PASSWORD_RESET_TTL),
                )
                should_send = True
        if should_send:
            self._send_password_reset_email(normalized_email, reset_token, request)

    def reset_password(self, token: str, password: str) -> None:
        token_hash = self._token_hash(token)
        salt, password_hash = _hash_password(password)
        now = _now_iso()
        with self.store.transaction() as conn:
            reset = self.store.consume_password_reset(conn, token_hash=token_hash, now=now)
            if reset is None:
                _raise_auth_error(400, "password_reset_invalid", "密码重置链接已失效，请重新发送")
            user = self.store.find_user_by_id(conn, reset["user_id"])
            if user is None or user["status"] != "active":
                _raise_auth_error(400, "password_reset_invalid", "密码重置链接已失效，请重新发送")
            self.store.update_password(
                conn,
                user_id=user["id"],
                password_salt=salt,
                password_hash=password_hash,
                now=now,
            )
            self.store.revoke_user_sessions(conn, user_id=user["id"], now=now)

    def overview(self) -> AdminOverview:
        now = _now_iso()
        with self.store.connection() as conn:
            users = [
                _user_view(row, self._identities_for_user(conn, row["id"]))
                for row in self.store.list_users(conn, now=now)
            ]
            stats_row = self.store.admin_stats(conn, now=now)
        status = delivery_status()
        return AdminOverview(
            stats=AdminStats(
                users=stats_row["users"],
                admins=stats_row["admins"],
                packages=stats_row["packages"],
                lessons=stats_row["lessons"],
                resources=stats_row["resources"],
                disabled_users=stats_row["disabled_users"],
                unverified_users=stats_row["unverified_users"],
                active_sessions=stats_row["active_sessions"],
            ),
            users=users,
            mail_delivery_configured=status.configured,
            mail_delivery_mode=status.mode,
        )

    def update_admin_user(
        self,
        *,
        actor: UserView,
        target_user_id: str,
        role: str | None,
        status: str | None,
    ) -> UserView:
        if actor.id == target_user_id and (role == "user" or status == "disabled"):
            _raise_auth_error(400, "admin_self_lockout", "不能禁用或降级当前管理员账号")
        now = _now_iso()
        with self.store.transaction() as conn:
            target = self.store.find_user_by_id(conn, target_user_id)
            if target is None:
                _raise_auth_error(404, "user_not_found", "用户不存在")
            self.store.update_user_admin_fields(conn, user_id=target_user_id, role=role, status=status, now=now)
            if status == "disabled":
                self.store.revoke_user_sessions(conn, user_id=target_user_id, now=now)
            self.store.create_audit_log(
                conn,
                log_id=new_id("audit"),
                actor_user_id=actor.id,
                target_user_id=target_user_id,
                action="user.update",
                metadata={
                    "role_before": target["role"],
                    "role_after": role or target["role"],
                    "status_before": target["status"],
                    "status_after": status or target["status"],
                },
                created_at=now,
            )
            updated = self.store.find_user_by_id(conn, target_user_id)
            return _user_view(updated, self._identities_for_user(conn, target_user_id))

    def revoke_admin_user_sessions(self, *, actor: UserView, target_user_id: str) -> None:
        now = _now_iso()
        with self.store.transaction() as conn:
            target = self.store.find_user_by_id(conn, target_user_id)
            if target is None:
                _raise_auth_error(404, "user_not_found", "用户不存在")
            self.store.revoke_user_sessions(conn, user_id=target_user_id, now=now)
            self.store.create_audit_log(
                conn,
                log_id=new_id("audit"),
                actor_user_id=actor.id,
                target_user_id=target_user_id,
                action="user.sessions.revoke",
                metadata={},
                created_at=now,
            )

    def audit_logs(self, *, limit: int = 100) -> AdminAuditLogResponse:
        with self.store.connection() as conn:
            return AdminAuditLogResponse(logs=[_audit_log_view(row) for row in self.store.list_audit_logs(conn, limit=limit)])

    def providers(self) -> list[AuthProviderView]:
        options = [
            AuthProviderView(
                id="email",
                label="邮箱",
                description="使用邮箱和密码注册或登录。",
                configured=delivery_status().configured,
                kind="password",
            )
        ]
        all_providers = _oauth_providers()
        for provider_id in _public_provider_ids():
            provider = all_providers.get(provider_id)
            if provider is None:
                continue
            options.append(
                AuthProviderView(
                    id=provider.id,
                    label=provider.label,
                    description=provider.description,
                    configured=provider.configured,
                    kind="oauth",
                )
            )
        return options

    def oauth_authorization_url(
        self,
        provider_id: str,
        next_path: str,
        request: Request,
        *,
        guest_token: str | None = None,
    ) -> str:
        provider = self._configured_provider(provider_id)
        state = secrets.token_urlsafe(OAUTH_STATE_BYTES)
        redirect_uri = self._oauth_redirect_uri(provider.id, request)
        guest_user_id = self._guest_user_id_for_token(guest_token)
        code_verifier = _oauth_code_verifier() if provider.pkce else None
        if provider.id == "wechat":
            params = {
                "appid": provider.client_id or "",
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(provider.scopes),
                "state": state,
            }
        else:
            params = {
                "client_id": provider.client_id or "",
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(provider.scopes),
                "state": state,
            }
        if provider.id == "google":
            params["prompt"] = "select_account"
        if provider.pkce and code_verifier:
            params["code_challenge"] = _oauth_code_challenge(code_verifier)
            params["code_challenge_method"] = "S256"
        if provider.response_mode:
            params["response_mode"] = provider.response_mode

        with self.store.transaction() as conn:
            self.store.create_oauth_state(
                conn,
                state=state,
                provider=provider.id,
                next_path=_safe_next_path(next_path),
                frontend_origin=_frontend_origin(request),
                guest_user_id=guest_user_id,
                code_verifier=code_verifier,
                created_at=_now_iso(),
            )
        suffix = "#wechat_redirect" if provider.id == "wechat" else ""
        return f"{provider.auth_url}?{parse.urlencode(params)}{suffix}"

    def complete_oauth_callback(
        self,
        provider_id: str,
        payload: dict[str, str],
        request: Request,
        *,
        user_agent: str | None = None,
    ) -> tuple[str, UserView, str, str]:
        provider = self._configured_provider(provider_id)
        state = payload.get("state", "")
        code = payload.get("code", "")
        if not state or not code:
            _raise_auth_error(400, "oauth_callback_missing_code", "第三方登录回调缺少授权码")
        next_path, frontend_origin, guest_user_id, code_verifier = self._consume_oauth_state(provider.id, state)
        token_payload = self._exchange_oauth_code(provider, code, request, code_verifier=code_verifier)
        profile = self._profile_from_oauth(provider, token_payload)
        token, user = self.login_with_oauth(profile, guest_user_id=guest_user_id, user_agent=user_agent)
        return token, user, next_path, frontend_origin

    def oauth_frontend_redirect_url(self, next_path: str, frontend_origin: str, request: Request, *, error: str | None = None) -> str:
        target = parse.urljoin((frontend_origin or _frontend_origin(request)).rstrip("/"), "/auth/callback")
        params = {"next": _safe_next_path(next_path)}
        if error:
            params["error"] = error
        return f"{target}?{parse.urlencode(params)}"

    def verification_frontend_redirect_url(self, next_path: str, frontend_origin: str, request: Request, *, error: str | None = None) -> str:
        target = parse.urljoin((frontend_origin or _frontend_origin(request)).rstrip("/"), "/auth/callback")
        params = {"next": _safe_next_path(next_path), "verified": "1"}
        if error:
            params["error"] = error
        return f"{target}?{parse.urlencode(params)}"

    def _exchange_oauth_code(
        self,
        provider: OAuthProviderConfig,
        code: str,
        request: Request,
        *,
        code_verifier: str | None = None,
    ) -> dict[str, Any]:
        if provider.id == "wechat":
            payload = _get_json_with_params(
                provider.token_url,
                {
                    "appid": provider.client_id or "",
                    "secret": provider.client_secret or "",
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if not isinstance(payload, dict):
                _raise_auth_error(502, "wechat_token_malformed", "微信登录令牌格式异常")
            if payload.get("errcode"):
                _raise_auth_error(502, "wechat_token_failed", str(payload.get("errmsg") or "微信登录令牌交换失败"))
            return payload
        token_data = {
            "client_id": provider.client_id or "",
            "client_secret": provider.client_secret or "",
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self._oauth_redirect_uri(provider.id, request),
        }
        basic_auth = None
        if provider.pkce:
            if not code_verifier:
                _raise_auth_error(400, "oauth_state_invalid", "第三方登录状态已失效，请重新发起登录")
            token_data["code_verifier"] = code_verifier
        if provider.token_auth_method == "basic":
            basic_auth = (provider.client_id or "", provider.client_secret or "")
            token_data.pop("client_secret", None)
        return _post_form(
            provider.token_url,
            token_data,
            basic_auth=basic_auth,
        )

    def _issue_session_for_user_id(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        *,
        user_agent: str | None,
    ) -> tuple[str, UserView]:
        row = self.store.find_user_by_id(conn, user_id)
        if row is None:
            _raise_auth_error(404, "user_not_found", "用户不存在")
        if row["status"] != "active":
            _raise_auth_error(403, "account_disabled", "该账号已被禁用")
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        self.store.create_session(
            conn,
            token_hash=self._token_hash(token),
            user_id=row["id"],
            now=now,
            expires_at=_future_iso(SESSION_TTL),
            user_agent=(user_agent or "")[:500] or None,
        )
        refreshed = self.store.find_user_by_id(conn, row["id"])
        return token, _user_view(refreshed, self._identities_for_user(conn, row["id"]))

    def _guest_user_view(self, guest_user_id: str, created_at: str, last_seen_at: str | None = None) -> UserView:
        return UserView(
            id=guest_user_id,
            email=_synthetic_email_for_guest(guest_user_id),
            role="guest",
            status="active",
            display_name="游客",
            created_at=created_at,
            updated_at=created_at,
            last_login_at=last_seen_at,
            auth_identities=[],
        )

    def _guest_user_id_for_token(self, guest_token: str | None) -> str | None:
        if not guest_token:
            return None
        token_hash = self._token_hash(guest_token)
        with self.store.connection() as conn:
            return self.store.guest_user_id_for_token_hash(conn, token_hash)

    def _claim_guest_workspace(
        self,
        conn: sqlite3.Connection,
        *,
        guest_token: str | None,
        user_id: str,
    ) -> None:
        if not guest_token:
            return
        token_hash = self._token_hash(guest_token)
        guest_user_id = self.store.guest_user_id_for_token_hash(conn, token_hash)
        if guest_user_id is None:
            return
        self._claim_guest_workspace_by_id(conn, guest_user_id=guest_user_id, user_id=user_id)

    def _claim_guest_workspace_by_id(
        self,
        conn: sqlite3.Connection,
        *,
        guest_user_id: str | None,
        user_id: str,
    ) -> None:
        if not guest_user_id or guest_user_id == user_id:
            return
        self.store.claim_guest_workspace(conn, guest_user_id=guest_user_id, user_id=user_id)

    def _identities_for_user(self, conn: sqlite3.Connection, user_id: str) -> list[AuthIdentityView]:
        rows = self.store.identities_for_user(conn, user_id)
        return [_identity_view(row) for row in rows]

    def _verified_email_for_oauth_profile(self, profile: OAuthProfile) -> str | None:
        if not profile.email or not profile.email_verified:
            return None
        try:
            return _normalize_email(profile.email)
        except HTTPException:
            return None

    def _synthetic_email_for_oauth_profile(self, profile: OAuthProfile) -> str:
        safe_subject = "".join(char if char.isalnum() else "-" for char in profile.subject.lower()).strip("-")
        return f"{profile.provider}-{safe_subject or secrets.token_hex(6)}@{OAUTH_EMAIL_DOMAIN}"

    def _configured_provider(self, provider_id: str) -> OAuthProviderConfig:
        provider = _oauth_providers().get(provider_id.strip().lower())
        if provider is None:
            _raise_auth_error(404, "oauth_provider_unsupported", "暂不支持该第三方登录方式")
        if not provider.configured:
            _raise_auth_error(503, "oauth_provider_unconfigured", f"{provider.label} 登录尚未配置 OAuth Client")
        return provider

    def _oauth_redirect_uri(self, provider_id: str, request: Request) -> str:
        return parse.urljoin(_public_origin(request), f"/api/auth/oauth/{provider_id}/callback")

    def _consume_oauth_state(self, provider_id: str, state: str) -> tuple[str, str, str | None, str | None]:
        with self.store.transaction() as conn:
            row = self.store.consume_oauth_state(conn, provider=provider_id, state=state)
            if row is None:
                _raise_auth_error(400, "oauth_state_invalid", "第三方登录状态已失效，请重新发起登录")
            created_at = _parse_iso(row["created_at"])
            if created_at is None or datetime.now(timezone.utc) - created_at > OAUTH_STATE_TTL:
                _raise_auth_error(400, "oauth_state_expired", "第三方登录状态已过期，请重新发起登录")
            return (
                _safe_next_path(row["next_path"]),
                row["frontend_origin"] or _frontend_origin(None),
                row["guest_user_id"],
                row["code_verifier"],
            )

    def _profile_from_oauth(self, provider: OAuthProviderConfig, token_payload: dict[str, Any]) -> OAuthProfile:
        access_token = str(token_payload.get("access_token") or "")
        id_token = str(token_payload.get("id_token") or "")
        if provider.id == "apple":
            claims = _decode_jwt_payload(id_token)
            return OAuthProfile(
                provider=provider.id,
                subject=str(claims.get("sub") or ""),
                email=str(claims.get("email") or "") or None,
                display_name=None,
                avatar_url=None,
                email_verified=_is_truthy(claims.get("email_verified")),
            )
        if not access_token:
            _raise_auth_error(502, "oauth_access_token_missing", "第三方登录没有返回访问令牌")
        if provider.id == "wechat":
            openid = str(token_payload.get("openid") or "")
            if not openid:
                _raise_auth_error(502, "wechat_openid_missing", "微信登录没有返回 OpenID")
            raw_profile = _get_json_with_params(
                provider.userinfo_url or "",
                {
                    "access_token": access_token,
                    "openid": openid,
                    "lang": "zh_CN",
                },
            )
            if not isinstance(raw_profile, dict):
                _raise_auth_error(502, "wechat_profile_malformed", "微信账号资料格式异常")
            if raw_profile.get("errcode"):
                _raise_auth_error(502, "wechat_profile_failed", str(raw_profile.get("errmsg") or "微信账号资料读取失败"))
            subject = str(raw_profile.get("unionid") or raw_profile.get("openid") or openid)
            return OAuthProfile(
                provider=provider.id,
                subject=subject,
                email=None,
                display_name=str(raw_profile.get("nickname") or "") or None,
                avatar_url=str(raw_profile.get("headimgurl") or "") or None,
                email_verified=False,
            )
        if provider.id == "github":
            raw_profile = _get_json(provider.userinfo_url or "", access_token)
            if not isinstance(raw_profile, dict):
                _raise_auth_error(502, "github_profile_malformed", "GitHub 账号资料格式异常")
            email = None
            email_verified = False
            if provider.email_url:
                raw_emails = _get_json(provider.email_url, access_token)
                if isinstance(raw_emails, list):
                    for item in raw_emails:
                        if isinstance(item, dict) and item.get("primary") and item.get("verified") and item.get("email"):
                            email = item["email"]
                            email_verified = True
                            break
            if not email and raw_profile.get("email"):
                email = raw_profile.get("email")
            return OAuthProfile(
                provider=provider.id,
                subject=str(raw_profile.get("id") or ""),
                email=str(email or "") or None,
                display_name=str(raw_profile.get("name") or raw_profile.get("login") or "") or None,
                avatar_url=str(raw_profile.get("avatar_url") or "") or None,
                email_verified=email_verified,
            )
        if provider.id == "x":
            raw_profile = _get_json(provider.userinfo_url or "", access_token)
            if not isinstance(raw_profile, dict):
                _raise_auth_error(502, "x_profile_malformed", "X 账号资料格式异常")
            data = raw_profile.get("data")
            if not isinstance(data, dict):
                _raise_auth_error(502, "x_profile_malformed", "X 账号资料格式异常")
            return OAuthProfile(
                provider=provider.id,
                subject=str(data.get("id") or ""),
                email=str(data.get("email") or "") or None,
                display_name=str(data.get("name") or data.get("username") or "") or None,
                avatar_url=str(data.get("profile_image_url") or "") or None,
                email_verified=False,
            )
        raw_profile = _get_json(provider.userinfo_url or "", access_token)
        if not isinstance(raw_profile, dict):
            _raise_auth_error(502, "oauth_profile_malformed", "第三方账号资料格式异常")
        return OAuthProfile(
            provider=provider.id,
            subject=str(raw_profile.get("sub") or raw_profile.get("id") or ""),
            email=str(raw_profile.get("email") or raw_profile.get("userPrincipalName") or "") or None,
            display_name=str(raw_profile.get("name") or raw_profile.get("preferred_username") or "") or None,
            avatar_url=str(raw_profile.get("picture") or "") or None,
            email_verified=_is_truthy(raw_profile.get("email_verified", True)),
        )

    def _send_verification_email(self, email: str, token: str, request: Request | None) -> None:
        link = f"{_public_origin(request)}/api/auth/email/verify?{parse.urlencode({'token': token})}"
        send_transactional_email(
            to_email=email,
            subject="Verify your OpenClass email",
            text_body=(
                "Welcome to OpenClass.\n\n"
                "Verify your email to finish creating your account:\n"
                f"{link}\n\n"
                "This link expires in 24 hours. If you did not request this, you can ignore this email."
            ),
        )

    def _send_password_reset_email(self, email: str, token: str, request: Request | None) -> None:
        link = f"{_frontend_origin(request)}/login?{parse.urlencode({'reset_token': token})}"
        send_transactional_email(
            to_email=email,
            subject="Reset your OpenClass password",
            text_body=(
                "Use this link to reset your OpenClass password:\n"
                f"{link}\n\n"
                "This link expires in 1 hour. If you did not request this, you can ignore this email."
            ),
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return _token_hash(token)


def _bearer_token_from_parts(
    authorization: str,
    *,
    cookie_token: str | None = None,
    query_token: str | None = None,
) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    if query_token:
        return query_token.strip()
    if cookie_token:
        return cookie_token.strip()
    _raise_auth_error(401, "unauthenticated", "请先登录")


def bearer_token_from_request(request: Request) -> str:
    return _bearer_token_from_parts(
        request.headers.get("Authorization", ""),
        cookie_token=request.cookies.get(AUTH_COOKIE_NAME) or request.cookies.get(GUEST_AUTH_COOKIE_NAME),
        query_token=request.query_params.get("access_token"),
    )


def bearer_token_from_websocket(websocket: WebSocket) -> str:
    return _bearer_token_from_parts(
        websocket.headers.get("Authorization", ""),
        cookie_token=websocket.cookies.get(AUTH_COOKIE_NAME) or websocket.cookies.get(GUEST_AUTH_COOKIE_NAME),
        query_token=websocket.query_params.get("access_token"),
    )
