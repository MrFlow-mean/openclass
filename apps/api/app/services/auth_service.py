from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import re
import secrets
import sqlite3
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request as urlrequest

import certifi
from fastapi import HTTPException, Request, WebSocket

from app.models import AdminOverview, AdminStats, AuthIdentityView, AuthProviderView, UserView, new_id


PBKDF2_ITERATIONS = 210_000
SESSION_TOKEN_BYTES = 32
OAUTH_STATE_BYTES = 24
OAUTH_STATE_TTL = timedelta(minutes=15)
_URLLIB_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
AUTH_COOKIE_NAME = "openclass.auth.token"
PHONE_EMAIL_DOMAIN = "phone.openclass.local"
GUEST_EMAIL_DOMAIN = "guest.openclass.local"


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    phone: str | None
    role: str
    created_at: str
    display_name: str | None = None
    avatar_url: str | None = None
    last_login_at: str | None = None


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
        raise HTTPException(status_code=422, detail="请输入有效邮箱或手机号")

    @property
    def display_name(self) -> str:
        if self.email:
            return self.email.split("@", 1)[0]
        if self.phone:
            return _mask_phone(self.phone)
        return "开放课堂用户"


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str
    email: str | None
    display_name: str | None = None
    avatar_url: str | None = None


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        raise HTTPException(status_code=422, detail="请输入有效邮箱")
    return normalized


def _normalize_phone(phone: str) -> str:
    compact = re.sub(r"[\s().-]", "", phone.strip())
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("0086"):
        compact = compact[4:]
    if not re.fullmatch(r"1[3-9]\d{9}", compact):
        raise HTTPException(status_code=422, detail="请输入有效手机号")
    return compact


def _normalize_account_identifier(identifier: str) -> AccountIdentifier:
    raw = identifier.strip()
    if not raw:
        raise HTTPException(status_code=422, detail="请输入有效邮箱或手机号")
    if "@" in raw:
        email = _normalize_email(raw)
        return AccountIdentifier(kind="email", subject=email, email=email)
    try:
        phone = _normalize_phone(raw)
    except HTTPException as exc:
        raise HTTPException(status_code=422, detail="请输入有效邮箱或手机号") from exc
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


def _workspace_setting_key(owner_user_id: str) -> str:
    return f"active_package_id:{owner_user_id}"


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
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="密码至少需要 8 位")
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


def _user_view(row: sqlite3.Row | AuthUser, identities: list[AuthIdentityView] | None = None) -> UserView:
    return UserView(
        id=row["id"] if isinstance(row, sqlite3.Row) else row.id,
        email=row["email"] if isinstance(row, sqlite3.Row) else row.email,
        phone=(
            row["phone"]
            if isinstance(row, sqlite3.Row) and "phone" in row.keys()
            else (None if isinstance(row, sqlite3.Row) else row.phone)
        ),
        role=row["role"] if isinstance(row, sqlite3.Row) else row.role,  # type: ignore[arg-type]
        display_name=row["display_name"] if isinstance(row, sqlite3.Row) and "display_name" in row.keys() else (None if isinstance(row, sqlite3.Row) else row.display_name),
        avatar_url=row["avatar_url"] if isinstance(row, sqlite3.Row) and "avatar_url" in row.keys() else (None if isinstance(row, sqlite3.Row) else row.avatar_url),
        created_at=row["created_at"] if isinstance(row, sqlite3.Row) else row.created_at,
        last_login_at=row["last_login_at"] if isinstance(row, sqlite3.Row) else row.last_login_at,
        auth_identities=identities or [],
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
        raise HTTPException(status_code=502, detail="第三方登录令牌交换失败") from exc


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
        raise HTTPException(status_code=502, detail="第三方账号资料读取失败") from exc


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
        raise HTTPException(status_code=502, detail="第三方账号资料读取失败") from exc


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        _, payload, _ = token.split(".", 2)
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="第三方身份令牌解析失败") from exc


class AuthService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _initialize(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        phone TEXT,
                        password_salt TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('user', 'admin')),
                        created_at TEXT NOT NULL,
                        last_login_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                        ON auth_sessions(user_id);

                    CREATE TABLE IF NOT EXISTS auth_identities (
                        provider TEXT NOT NULL,
                        provider_subject TEXT NOT NULL,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        email TEXT,
                        display_name TEXT,
                        avatar_url TEXT,
                        created_at TEXT NOT NULL,
                        last_login_at TEXT,
                        PRIMARY KEY (provider, provider_subject)
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_identities_user
                        ON auth_identities(user_id);

                    CREATE TABLE IF NOT EXISTS auth_oauth_states (
                        state TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        next_path TEXT NOT NULL,
                        frontend_origin TEXT,
                        guest_user_id TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS auth_guest_sessions (
                        token_hash TEXT PRIMARY KEY,
                        guest_user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_guest_sessions_guest
                        ON auth_guest_sessions(guest_user_id);
                    """
                )
                self._ensure_user_column(conn, "display_name", "TEXT")
                self._ensure_user_column(conn, "avatar_url", "TEXT")
                self._ensure_user_column(conn, "phone", "TEXT")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone) WHERE phone IS NOT NULL"
                )
                self._ensure_table_column(conn, "auth_oauth_states", "frontend_origin", "TEXT")
                self._ensure_table_column(conn, "auth_oauth_states", "guest_user_id", "TEXT")
                self._ensure_table_column(conn, "auth_oauth_states", "code_verifier", "TEXT")
                with conn:
                    self._ensure_email_identities(conn)

    def _ensure_user_column(self, conn: sqlite3.Connection, name: str, definition: str) -> None:
        self._ensure_table_column(conn, "users", name, definition)

    def _ensure_table_column(self, conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _ensure_email_identities(self, conn: sqlite3.Connection) -> None:
        now = _now_iso()
        rows = conn.execute(
            """
            SELECT users.*
            FROM users
            LEFT JOIN auth_identities
                ON auth_identities.user_id = users.id
                AND auth_identities.provider = 'email'
            WHERE auth_identities.user_id IS NULL
                AND users.phone IS NULL
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO auth_identities(
                    provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at
                )
                VALUES ('email', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["email"],
                    row["id"],
                    row["email"],
                    row["display_name"] or row["email"].split("@", 1)[0],
                    row["avatar_url"],
                    row["created_at"] or now,
                    row["last_login_at"],
                ),
            )

    def register(self, identifier: str, password: str, *, guest_token: str | None = None) -> tuple[str, UserView]:
        account = _normalize_account_identifier(identifier)
        salt, password_hash = _hash_password(password)
        created_at = _now_iso()

        with self._lock:
            with self._connect() as conn:
                with conn:
                    user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
                    role = "admin" if user_count == 0 or (account.email and account.email in _admin_emails()) else "user"
                    try:
                        user_id = new_id("user")
                        conn.execute(
                            """
                            INSERT INTO users(id, email, phone, password_salt, password_hash, role, display_name, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (user_id, account.storage_email, account.phone, salt, password_hash, role, account.display_name, created_at),
                        )
                        conn.execute(
                            """
                            INSERT INTO auth_identities(
                                provider, provider_subject, user_id, email, display_name, created_at, last_login_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                account.kind,
                                account.subject,
                                user_id,
                                account.email,
                                account.display_name,
                                created_at,
                                created_at,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        message = "该手机号已注册" if account.kind == "phone" else "该邮箱已注册"
                        raise HTTPException(status_code=409, detail=message) from exc
                    self._claim_guest_workspace(conn, guest_token=guest_token, user_id=user_id)
                    return self._issue_session_for_user_id(conn, user_id)

    def login(self, identifier: str, password: str, *, guest_token: str | None = None) -> tuple[str, UserView]:
        account = _normalize_account_identifier(identifier)
        with self._lock:
            with self._connect() as conn:
                if account.kind == "phone":
                    row = conn.execute("SELECT * FROM users WHERE phone = ?", (account.phone,)).fetchone()
                else:
                    row = conn.execute("SELECT * FROM users WHERE email = ?", (account.email,)).fetchone()
                if row is None or not _verify_password(password, row["password_salt"], row["password_hash"]):
                    raise HTTPException(status_code=401, detail="账号或密码不正确")
                with conn:
                    now = _now_iso()
                    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
                    conn.execute(
                        """
                        UPDATE auth_identities
                        SET last_login_at = ?
                        WHERE user_id = ? AND provider = ?
                        """,
                        (now, row["id"], account.kind),
                    )
                    self._claim_guest_workspace(conn, guest_token=guest_token, user_id=row["id"])
                    return self._issue_session_for_user_id(conn, row["id"])

    def login_with_oauth(self, profile: OAuthProfile, *, guest_user_id: str | None = None) -> tuple[str, UserView]:
        if not profile.provider or not profile.subject:
            raise HTTPException(status_code=422, detail="第三方账号资料不完整")
        provider = profile.provider.strip().lower()
        subject = profile.subject.strip()
        normalized_email = self._email_for_oauth_profile(profile)
        now = _now_iso()

        with self._lock:
            with self._connect() as conn:
                with conn:
                    identity = conn.execute(
                        """
                        SELECT users.*
                        FROM auth_identities
                        JOIN users ON users.id = auth_identities.user_id
                        WHERE auth_identities.provider = ? AND auth_identities.provider_subject = ?
                        """,
                        (provider, subject),
                    ).fetchone()
                    if identity is not None:
                        user_id = identity["id"]
                    else:
                        existing = conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
                        if existing is not None:
                            user_id = existing["id"]
                        else:
                            user_id = new_id("user")
                            password_salt, password_hash = _hash_password(secrets.token_urlsafe(32))
                            user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
                            role = "admin" if user_count == 0 or normalized_email in _admin_emails() else "user"
                            conn.execute(
                                """
                                INSERT INTO users(
                                    id, email, password_salt, password_hash, role, display_name, avatar_url, created_at, last_login_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    user_id,
                                    normalized_email,
                                    password_salt,
                                    password_hash,
                                    role,
                                    profile.display_name or normalized_email.split("@", 1)[0],
                                    profile.avatar_url,
                                    now,
                                    now,
                                ),
                            )
                        conn.execute(
                            """
                            INSERT INTO auth_identities(
                                provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                provider,
                                subject,
                                user_id,
                                profile.email,
                                profile.display_name,
                                profile.avatar_url,
                                now,
                                now,
                            ),
                        )

                    conn.execute(
                        """
                        UPDATE auth_identities
                        SET email = ?, display_name = ?, avatar_url = ?, last_login_at = ?
                        WHERE provider = ? AND provider_subject = ?
                        """,
                        (profile.email, profile.display_name, profile.avatar_url, now, provider, subject),
                    )
                    conn.execute(
                        """
                        UPDATE users
                        SET
                            last_login_at = ?,
                            display_name = COALESCE(NULLIF(?, ''), display_name),
                            avatar_url = COALESCE(NULLIF(?, ''), avatar_url)
                        WHERE id = ?
                        """,
                        (now, profile.display_name or "", profile.avatar_url or "", user_id),
                    )
                    self._claim_guest_workspace_by_id(conn, guest_user_id=guest_user_id, user_id=user_id)
                    return self._issue_session_for_user_id(conn, user_id)

    def get_user_by_token(self, token: str) -> UserView:
        token_hash = self._token_hash(token)
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT users.*
                    FROM auth_sessions
                    JOIN users ON users.id = auth_sessions.user_id
                    WHERE auth_sessions.token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
                if row is None:
                    guest = conn.execute(
                        "SELECT * FROM auth_guest_sessions WHERE token_hash = ?",
                        (token_hash,),
                    ).fetchone()
                    if guest is None:
                        raise HTTPException(status_code=401, detail="请先登录")
                    with conn:
                        conn.execute(
                            "UPDATE auth_guest_sessions SET last_seen_at = ? WHERE token_hash = ?",
                            (_now_iso(), token_hash),
                        )
                    return self._guest_user_view(guest["guest_user_id"], guest["created_at"], guest["last_seen_at"])
                with conn:
                    conn.execute(
                        "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                        (_now_iso(), token_hash),
                    )
                return _user_view(row, self._identities_for_user(conn, row["id"]))

    def start_guest_session(self) -> tuple[str, UserView]:
        guest_user_id = new_id("guest")
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        with self._lock:
            with self._connect() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO auth_guest_sessions(token_hash, guest_user_id, created_at, last_seen_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (self._token_hash(token), guest_user_id, now, now),
                    )
        return token, self._guest_user_view(guest_user_id, now, now)

    def overview(self) -> AdminOverview:
        with self._lock:
            with self._connect() as conn:
                users = [
                    _user_view(row, self._identities_for_user(conn, row["id"]))
                    for row in conn.execute("SELECT * FROM users ORDER BY created_at DESC, email").fetchall()
                ]
                stats_row = conn.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM users) AS users,
                        (SELECT count(*) FROM users WHERE role = 'admin') AS admins,
                        (SELECT count(*) FROM course_packages) AS packages,
                        (SELECT count(*) FROM lessons) AS lessons,
                        (SELECT count(*) FROM resources) AS resources
                    """
                ).fetchone()
        return AdminOverview(
            stats=AdminStats(
                users=stats_row["users"],
                admins=stats_row["admins"],
                packages=stats_row["packages"],
                lessons=stats_row["lessons"],
                resources=stats_row["resources"],
            ),
            users=users,
        )

    def providers(self) -> list[AuthProviderView]:
        options = [
            AuthProviderView(
                id="email",
                label="邮箱/手机号",
                description="使用邮箱或手机号和密码注册或登录。",
                configured=True,
                kind="password",
            )
        ]
        for provider in _oauth_providers().values():
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

        with self._lock:
            with self._connect() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO auth_oauth_states(
                            state, provider, next_path, frontend_origin, guest_user_id, code_verifier, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            state,
                            provider.id,
                            _safe_next_path(next_path),
                            _frontend_origin(request),
                            guest_user_id,
                            code_verifier,
                            _now_iso(),
                        ),
                    )
        suffix = "#wechat_redirect" if provider.id == "wechat" else ""
        return f"{provider.auth_url}?{parse.urlencode(params)}{suffix}"

    def complete_oauth_callback(self, provider_id: str, payload: dict[str, str], request: Request) -> tuple[str, UserView, str, str]:
        provider = self._configured_provider(provider_id)
        state = payload.get("state", "")
        code = payload.get("code", "")
        if not state or not code:
            raise HTTPException(status_code=400, detail="第三方登录回调缺少授权码")
        next_path, frontend_origin, guest_user_id, code_verifier = self._consume_oauth_state(provider.id, state)
        token_payload = self._exchange_oauth_code(provider, code, request, code_verifier=code_verifier)
        profile = self._profile_from_oauth(provider, token_payload)
        token, user = self.login_with_oauth(profile, guest_user_id=guest_user_id)
        return token, user, next_path, frontend_origin

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
                raise HTTPException(status_code=502, detail="微信登录令牌格式异常")
            if payload.get("errcode"):
                raise HTTPException(status_code=502, detail=str(payload.get("errmsg") or "微信登录令牌交换失败"))
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
                raise HTTPException(status_code=400, detail="第三方登录状态已失效，请重新发起登录")
            token_data["code_verifier"] = code_verifier
        if provider.token_auth_method == "basic":
            basic_auth = (provider.client_id or "", provider.client_secret or "")
            token_data.pop("client_secret", None)
        return _post_form(
            provider.token_url,
            token_data,
            basic_auth=basic_auth,
        )

    def oauth_frontend_redirect_url(self, token: str, user: UserView, next_path: str, frontend_origin: str, request: Request) -> str:
        target = parse.urljoin((frontend_origin or _frontend_origin(request)).rstrip("/"), "/auth/callback")
        query = parse.urlencode(
            {
                "token": token,
                "user_id": user.id,
                "next": _safe_next_path(next_path),
            }
        )
        return f"{target}?{query}"

    def _issue_session(self, conn: sqlite3.Connection, email: str) -> tuple[str, UserView]:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        return self._issue_session_for_user_id(conn, row["id"])

    def _issue_session_for_user_id(self, conn: sqlite3.Connection, user_id: str) -> tuple[str, UserView]:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        conn.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (self._token_hash(token), row["id"], now, now),
        )
        refreshed = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return token, _user_view(refreshed, self._identities_for_user(conn, row["id"]))

    def _guest_user_view(self, guest_user_id: str, created_at: str, last_seen_at: str | None = None) -> UserView:
        return UserView(
            id=guest_user_id,
            email=_synthetic_email_for_guest(guest_user_id),
            role="guest",
            display_name="游客",
            created_at=created_at,
            last_login_at=last_seen_at,
            auth_identities=[],
        )

    def _guest_user_id_for_token(self, guest_token: str | None) -> str | None:
        if not guest_token:
            return None
        token_hash = self._token_hash(guest_token)
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT guest_user_id FROM auth_guest_sessions WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
        return row["guest_user_id"] if row is not None else None

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
        row = conn.execute(
            "SELECT guest_user_id FROM auth_guest_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return
        self._claim_guest_workspace_by_id(conn, guest_user_id=row["guest_user_id"], user_id=user_id)

    def _claim_guest_workspace_by_id(
        self,
        conn: sqlite3.Connection,
        *,
        guest_user_id: str | None,
        user_id: str,
    ) -> None:
        if not guest_user_id or guest_user_id == user_id:
            return
        conn.execute(
            "UPDATE course_packages SET owner_user_id = ? WHERE owner_user_id = ?",
            (user_id, guest_user_id),
        )
        guest_setting_key = _workspace_setting_key(guest_user_id)
        user_setting_key = _workspace_setting_key(user_id)
        guest_active_row = conn.execute(
            "SELECT value FROM workspace_settings WHERE key = ?",
            (guest_setting_key,),
        ).fetchone()
        if guest_active_row is not None:
            conn.execute(
                "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                (user_setting_key, guest_active_row["value"]),
            )
            conn.execute("DELETE FROM workspace_settings WHERE key = ?", (guest_setting_key,))
        conn.execute("DELETE FROM auth_guest_sessions WHERE guest_user_id = ?", (guest_user_id,))

    def _identities_for_user(self, conn: sqlite3.Connection, user_id: str) -> list[AuthIdentityView]:
        rows = conn.execute(
            """
            SELECT *
            FROM auth_identities
            WHERE user_id = ?
            ORDER BY
                CASE provider
                    WHEN 'email' THEN 0
                    WHEN 'phone' THEN 1
                    WHEN 'wechat' THEN 2
                    WHEN 'google' THEN 3
                    WHEN 'apple' THEN 4
                    WHEN 'github' THEN 5
                    WHEN 'microsoft' THEN 6
                    WHEN 'x' THEN 7
                    ELSE 9
                END,
                created_at
            """,
            (user_id,),
        ).fetchall()
        return [_identity_view(row) for row in rows]

    def _email_for_oauth_profile(self, profile: OAuthProfile) -> str:
        if profile.email:
            try:
                return _normalize_email(profile.email)
            except HTTPException:
                pass
        safe_subject = "".join(char if char.isalnum() else "-" for char in profile.subject.lower()).strip("-")
        return f"{profile.provider}-{safe_subject or secrets.token_hex(6)}@oauth.openclass.local"

    def _configured_provider(self, provider_id: str) -> OAuthProviderConfig:
        provider = _oauth_providers().get(provider_id.strip().lower())
        if provider is None:
            raise HTTPException(status_code=404, detail="暂不支持该第三方登录方式")
        if not provider.configured:
            raise HTTPException(status_code=503, detail=f"{provider.label} 登录尚未配置 OAuth Client")
        return provider

    def _oauth_redirect_uri(self, provider_id: str, request: Request) -> str:
        return parse.urljoin(_public_origin(request), f"/api/auth/oauth/{provider_id}/callback")

    def _consume_oauth_state(self, provider_id: str, state: str) -> tuple[str, str, str | None, str | None]:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    row = conn.execute(
                        "SELECT * FROM auth_oauth_states WHERE state = ? AND provider = ?",
                        (state, provider_id),
                    ).fetchone()
                    if row is None:
                        raise HTTPException(status_code=400, detail="第三方登录状态已失效，请重新发起登录")
                    conn.execute("DELETE FROM auth_oauth_states WHERE state = ?", (state,))
                    created_at = _parse_iso(row["created_at"])
                    if created_at is None or datetime.now(timezone.utc) - created_at > OAUTH_STATE_TTL:
                        raise HTTPException(status_code=400, detail="第三方登录状态已过期，请重新发起登录")
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
            )
        if not access_token:
            raise HTTPException(status_code=502, detail="第三方登录没有返回访问令牌")
        if provider.id == "wechat":
            openid = str(token_payload.get("openid") or "")
            if not openid:
                raise HTTPException(status_code=502, detail="微信登录没有返回 OpenID")
            raw_profile = _get_json_with_params(
                provider.userinfo_url or "",
                {
                    "access_token": access_token,
                    "openid": openid,
                    "lang": "zh_CN",
                },
            )
            if not isinstance(raw_profile, dict):
                raise HTTPException(status_code=502, detail="微信账号资料格式异常")
            if raw_profile.get("errcode"):
                raise HTTPException(status_code=502, detail=str(raw_profile.get("errmsg") or "微信账号资料读取失败"))
            subject = str(raw_profile.get("unionid") or raw_profile.get("openid") or openid)
            return OAuthProfile(
                provider=provider.id,
                subject=subject,
                email=None,
                display_name=str(raw_profile.get("nickname") or "") or None,
                avatar_url=str(raw_profile.get("headimgurl") or "") or None,
            )
        if provider.id == "github":
            raw_profile = _get_json(provider.userinfo_url or "", access_token)
            if not isinstance(raw_profile, dict):
                raise HTTPException(status_code=502, detail="GitHub 账号资料格式异常")
            email = raw_profile.get("email")
            if not email and provider.email_url:
                raw_emails = _get_json(provider.email_url, access_token)
                if isinstance(raw_emails, list):
                    for item in raw_emails:
                        if isinstance(item, dict) and item.get("primary") and item.get("verified") and item.get("email"):
                            email = item["email"]
                            break
            return OAuthProfile(
                provider=provider.id,
                subject=str(raw_profile.get("id") or ""),
                email=str(email or "") or None,
                display_name=str(raw_profile.get("name") or raw_profile.get("login") or "") or None,
                avatar_url=str(raw_profile.get("avatar_url") or "") or None,
            )
        if provider.id == "x":
            raw_profile = _get_json(provider.userinfo_url or "", access_token)
            if not isinstance(raw_profile, dict):
                raise HTTPException(status_code=502, detail="X 账号资料格式异常")
            data = raw_profile.get("data")
            if not isinstance(data, dict):
                raise HTTPException(status_code=502, detail="X 账号资料格式异常")
            return OAuthProfile(
                provider=provider.id,
                subject=str(data.get("id") or ""),
                email=str(data.get("email") or "") or None,
                display_name=str(data.get("name") or data.get("username") or "") or None,
                avatar_url=str(data.get("profile_image_url") or "") or None,
            )
        raw_profile = _get_json(provider.userinfo_url or "", access_token)
        if not isinstance(raw_profile, dict):
            raise HTTPException(status_code=502, detail="第三方账号资料格式异常")
        return OAuthProfile(
            provider=provider.id,
            subject=str(raw_profile.get("sub") or raw_profile.get("id") or ""),
            email=str(raw_profile.get("email") or raw_profile.get("userPrincipalName") or "") or None,
            display_name=str(raw_profile.get("name") or raw_profile.get("preferred_username") or "") or None,
            avatar_url=str(raw_profile.get("picture") or "") or None,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    raise HTTPException(status_code=401, detail="请先登录")


def bearer_token_from_request(request: Request) -> str:
    return _bearer_token_from_parts(
        request.headers.get("Authorization", ""),
        cookie_token=request.cookies.get(AUTH_COOKIE_NAME),
        query_token=request.query_params.get("access_token"),
    )


def bearer_token_from_websocket(websocket: WebSocket) -> str:
    return _bearer_token_from_parts(
        websocket.headers.get("Authorization", ""),
        cookie_token=websocket.cookies.get(AUTH_COOKIE_NAME),
        query_token=websocket.query_params.get("access_token"),
    )
