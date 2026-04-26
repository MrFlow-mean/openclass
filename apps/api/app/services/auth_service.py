from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, Request

from app.models import AdminOverview, AdminStats, UserView, new_id


PBKDF2_ITERATIONS = 210_000
SESSION_TOKEN_BYTES = 32


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    role: str
    created_at: str
    last_login_at: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise HTTPException(status_code=422, detail="请输入有效邮箱")
    return normalized


def _admin_emails() -> set[str]:
    raw = os.getenv("OPENCLASS_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


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


def _user_view(row: sqlite3.Row | AuthUser) -> UserView:
    return UserView(
        id=row["id"] if isinstance(row, sqlite3.Row) else row.id,
        email=row["email"] if isinstance(row, sqlite3.Row) else row.email,
        role=row["role"] if isinstance(row, sqlite3.Row) else row.role,  # type: ignore[arg-type]
        created_at=row["created_at"] if isinstance(row, sqlite3.Row) else row.created_at,
        last_login_at=row["last_login_at"] if isinstance(row, sqlite3.Row) else row.last_login_at,
    )


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
                    """
                )

    def register(self, email: str, password: str) -> tuple[str, UserView]:
        normalized_email = _normalize_email(email)
        salt, password_hash = _hash_password(password)
        created_at = _now_iso()

        with self._lock:
            with self._connect() as conn:
                with conn:
                    user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
                    role = "admin" if user_count == 0 or normalized_email in _admin_emails() else "user"
                    try:
                        conn.execute(
                            """
                            INSERT INTO users(id, email, password_salt, password_hash, role, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (new_id("user"), normalized_email, salt, password_hash, role, created_at),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise HTTPException(status_code=409, detail="该邮箱已注册") from exc
                    return self._issue_session(conn, normalized_email)

    def login(self, email: str, password: str) -> tuple[str, UserView]:
        normalized_email = _normalize_email(email)
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
                if row is None or not _verify_password(password, row["password_salt"], row["password_hash"]):
                    raise HTTPException(status_code=401, detail="邮箱或密码不正确")
                with conn:
                    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now_iso(), row["id"]))
                    return self._issue_session(conn, normalized_email)

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
                    raise HTTPException(status_code=401, detail="请先登录")
                with conn:
                    conn.execute(
                        "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                        (_now_iso(), token_hash),
                    )
                return _user_view(row)

    def overview(self) -> AdminOverview:
        with self._lock:
            with self._connect() as conn:
                users = [_user_view(row) for row in conn.execute("SELECT * FROM users ORDER BY created_at DESC, email").fetchall()]
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

    def _issue_session(self, conn: sqlite3.Connection, email: str) -> tuple[str, UserView]:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now_iso()
        conn.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (self._token_hash(token), row["id"], now, now),
        )
        refreshed = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return token, _user_view(refreshed)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token_from_request(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="请先登录")
    return token.strip()

