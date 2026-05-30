from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SYNTHETIC_EMAIL_SUFFIXES = (
    "@phone.openclass.local",
    "@oauth.openclass.local",
    "@guest.openclass.local",
)


def _workspace_setting_key(owner_user_id: str) -> str:
    return f"active_package_id:{owner_user_id}"


def _is_synthetic_email(email: str | None) -> bool:
    normalized = (email or "").lower()
    return any(normalized.endswith(suffix) for suffix in SYNTHETIC_EMAIL_SUFFIXES)


class AuthStore:
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

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn:
            with conn:
                yield conn

    def _initialize(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    phone TEXT,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'admin')),
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
                    display_name TEXT,
                    avatar_url TEXT,
                    email_verified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT,
                    user_agent TEXT
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
                    code_verifier TEXT,
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

                CREATE TABLE IF NOT EXISTS auth_email_verifications (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    next_path TEXT NOT NULL,
                    frontend_origin TEXT,
                    guest_user_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_email_verifications_user
                    ON auth_email_verifications(user_id);

                CREATE TABLE IF NOT EXISTS auth_password_resets (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_password_resets_user
                    ON auth_password_resets(user_id);

                CREATE TABLE IF NOT EXISTS admin_audit_logs (
                    id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    target_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_created
                    ON admin_audit_logs(created_at DESC);
                """
            )
            self._ensure_user_column(conn, "display_name", "TEXT")
            self._ensure_user_column(conn, "avatar_url", "TEXT")
            self._ensure_user_column(conn, "phone", "TEXT")
            self._ensure_user_column(conn, "status", "TEXT")
            self._ensure_user_column(conn, "email_verified_at", "TEXT")
            self._ensure_user_column(conn, "updated_at", "TEXT")
            self._ensure_table_column(conn, "auth_sessions", "expires_at", "TEXT")
            self._ensure_table_column(conn, "auth_sessions", "revoked_at", "TEXT")
            self._ensure_table_column(conn, "auth_sessions", "user_agent", "TEXT")
            self._ensure_table_column(conn, "auth_oauth_states", "frontend_origin", "TEXT")
            self._ensure_table_column(conn, "auth_oauth_states", "guest_user_id", "TEXT")
            self._ensure_table_column(conn, "auth_oauth_states", "code_verifier", "TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone) WHERE phone IS NOT NULL")
            conn.execute("UPDATE users SET status = 'active' WHERE status IS NULL OR status = ''")
            conn.execute("UPDATE users SET updated_at = created_at WHERE updated_at IS NULL")
            self._migrate_legacy_email_verification(conn)
            self._migrate_legacy_session_expiry(conn)
            self.ensure_email_identities(conn)

    def _ensure_user_column(self, conn: sqlite3.Connection, name: str, definition: str) -> None:
        self._ensure_table_column(conn, "users", name, definition)

    def _ensure_table_column(self, conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_legacy_email_verification(self, conn: sqlite3.Connection) -> None:
        key = "auth_legacy_email_verification_migrated"
        row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return
        rows = conn.execute("SELECT id, email, created_at FROM users WHERE email_verified_at IS NULL").fetchall()
        for user in rows:
            if _is_synthetic_email(user["email"]):
                continue
            conn.execute(
                "UPDATE users SET email_verified_at = ?, updated_at = COALESCE(updated_at, ?) WHERE id = ?",
                (user["created_at"], user["created_at"], user["id"]),
            )
        conn.execute("INSERT INTO schema_meta(key, value) VALUES (?, ?)", (key, "1"))

    def _migrate_legacy_session_expiry(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE auth_sessions
            SET expires_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '+30 days')
            WHERE expires_at IS NULL
            """
        )

    def ensure_email_identities(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT users.*
            FROM users
            LEFT JOIN auth_identities
                ON auth_identities.user_id = users.id
                AND auth_identities.provider = 'email'
            WHERE auth_identities.user_id IS NULL
                AND users.phone IS NULL
                AND users.email NOT LIKE '%@oauth.openclass.local'
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
                    row["created_at"],
                    row["last_login_at"],
                ),
            )

    def user_count(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT count(*) FROM users").fetchone()[0])

    def find_user_by_email(self, conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def find_user_by_phone(self, conn: sqlite3.Connection, phone: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()

    def find_user_by_id(self, conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def create_password_user(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        email: str,
        phone: str | None,
        password_salt: str,
        password_hash: str,
        role: str,
        display_name: str,
        created_at: str,
        email_verified_at: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO users(
                id, email, phone, password_salt, password_hash, role, status,
                display_name, email_verified_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (user_id, email, phone, password_salt, password_hash, role, display_name, email_verified_at, created_at, created_at),
        )

    def create_oauth_user(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        email: str,
        password_salt: str,
        password_hash: str,
        role: str,
        display_name: str,
        avatar_url: str | None,
        email_verified_at: str | None,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO users(
                id, email, password_salt, password_hash, role, status,
                display_name, avatar_url, email_verified_at, created_at, updated_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, password_salt, password_hash, role, display_name, avatar_url, email_verified_at, now, now, now),
        )

    def create_identity(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        provider_subject: str,
        user_id: str,
        email: str | None,
        display_name: str | None,
        avatar_url: str | None = None,
        created_at: str,
        last_login_at: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_identities(
                provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, provider_subject, user_id, email, display_name, avatar_url, created_at, last_login_at),
        )

    def touch_password_login(self, conn: sqlite3.Connection, *, user_id: str, provider: str, now: str) -> None:
        conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, user_id))
        conn.execute(
            """
            UPDATE auth_identities
            SET last_login_at = ?
            WHERE user_id = ? AND provider = ?
            """,
            (now, user_id, provider),
        )

    def find_user_by_oauth_identity(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        provider_subject: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT users.*
            FROM auth_identities
            JOIN users ON users.id = auth_identities.user_id
            WHERE auth_identities.provider = ? AND auth_identities.provider_subject = ?
            """,
            (provider, provider_subject),
        ).fetchone()

    def touch_oauth_identity(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        provider_subject: str,
        email: str | None,
        display_name: str | None,
        avatar_url: str | None,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE auth_identities
            SET email = ?, display_name = ?, avatar_url = ?, last_login_at = ?
            WHERE provider = ? AND provider_subject = ?
            """,
            (email, display_name, avatar_url, now, provider, provider_subject),
        )

    def touch_oauth_user_profile(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        display_name: str | None,
        avatar_url: str | None,
        email_verified_at: str | None,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE users
            SET
                last_login_at = ?,
                updated_at = ?,
                email_verified_at = COALESCE(email_verified_at, ?),
                display_name = COALESCE(NULLIF(?, ''), display_name),
                avatar_url = COALESCE(NULLIF(?, ''), avatar_url)
            WHERE id = ?
            """,
            (now, now, email_verified_at, display_name or "", avatar_url or "", user_id),
        )

    def mark_email_verified(self, conn: sqlite3.Connection, *, user_id: str, now: str) -> None:
        conn.execute(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?",
            (now, now, user_id),
        )

    def update_password(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        password_salt: str,
        password_hash: str,
        now: str,
    ) -> None:
        conn.execute(
            "UPDATE users SET password_salt = ?, password_hash = ?, updated_at = ? WHERE id = ?",
            (password_salt, password_hash, now, user_id),
        )

    def find_user_by_session_token(self, conn: sqlite3.Connection, token_hash: str, now: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT users.*
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ?
                AND auth_sessions.revoked_at IS NULL
                AND (auth_sessions.expires_at IS NULL OR auth_sessions.expires_at > ?)
                AND users.status = 'active'
            """,
            (token_hash, now),
        ).fetchone()

    def find_guest_session_by_token(self, conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM auth_guest_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()

    def touch_session(self, conn: sqlite3.Connection, token_hash: str, now: str) -> None:
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))

    def touch_guest_session(self, conn: sqlite3.Connection, token_hash: str, now: str) -> None:
        conn.execute("UPDATE auth_guest_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))

    def create_guest_session(
        self,
        conn: sqlite3.Connection,
        *,
        token_hash: str,
        guest_user_id: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_guest_sessions(token_hash, guest_user_id, created_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, guest_user_id, now, now),
        )

    def create_session(
        self,
        conn: sqlite3.Connection,
        *,
        token_hash: str,
        user_id: str,
        now: str,
        expires_at: str,
        user_agent: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_sessions(token_hash, user_id, created_at, last_seen_at, expires_at, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token_hash, user_id, now, now, expires_at, user_agent),
        )

    def revoke_session(self, conn: sqlite3.Connection, *, token_hash: str, now: str) -> None:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE token_hash = ?",
            (now, token_hash),
        )

    def revoke_user_sessions(self, conn: sqlite3.Connection, *, user_id: str, now: str) -> None:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )

    def list_users(self, conn: sqlite3.Connection, *, now: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT
                users.*,
                (SELECT count(*) FROM course_packages WHERE owner_user_id = users.id) AS package_count,
                (
                    SELECT count(*)
                    FROM auth_sessions
                    WHERE user_id = users.id
                        AND revoked_at IS NULL
                        AND (expires_at IS NULL OR expires_at > ?)
                ) AS session_count
            FROM users
            ORDER BY created_at DESC, email
            """,
            (now,),
        ).fetchall()

    def admin_stats(self, conn: sqlite3.Connection, *, now: str) -> sqlite3.Row:
        return conn.execute(
            """
            SELECT
                (SELECT count(*) FROM users) AS users,
                (SELECT count(*) FROM users WHERE role = 'admin') AS admins,
                (SELECT count(*) FROM users WHERE status = 'disabled') AS disabled_users,
                (SELECT count(*) FROM users WHERE email_verified_at IS NULL AND email NOT LIKE '%@oauth.openclass.local') AS unverified_users,
                (
                    SELECT count(*)
                    FROM auth_sessions
                    WHERE revoked_at IS NULL
                        AND (expires_at IS NULL OR expires_at > ?)
                ) AS active_sessions,
                (SELECT count(*) FROM course_packages) AS packages,
                (SELECT count(*) FROM lessons) AS lessons,
                (SELECT count(*) FROM resources) AS resources
            """,
            (now,),
        ).fetchone()

    def create_email_verification(
        self,
        conn: sqlite3.Connection,
        *,
        token_hash: str,
        user_id: str,
        email: str,
        next_path: str,
        frontend_origin: str,
        guest_user_id: str | None,
        created_at: str,
        expires_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_email_verifications(
                token_hash, user_id, email, next_path, frontend_origin, guest_user_id, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token_hash, user_id, email, next_path, frontend_origin, guest_user_id, created_at, expires_at),
        )

    def consume_email_verification(self, conn: sqlite3.Connection, *, token_hash: str, now: str) -> sqlite3.Row | None:
        row = conn.execute(
            """
            SELECT *
            FROM auth_email_verifications
            WHERE token_hash = ?
                AND consumed_at IS NULL
                AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE auth_email_verifications SET consumed_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
        return row

    def create_password_reset(
        self,
        conn: sqlite3.Connection,
        *,
        token_hash: str,
        user_id: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_password_resets(token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, user_id, created_at, expires_at),
        )

    def consume_password_reset(self, conn: sqlite3.Connection, *, token_hash: str, now: str) -> sqlite3.Row | None:
        row = conn.execute(
            """
            SELECT *
            FROM auth_password_resets
            WHERE token_hash = ?
                AND consumed_at IS NULL
                AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE auth_password_resets SET consumed_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
        return row

    def create_oauth_state(
        self,
        conn: sqlite3.Connection,
        *,
        state: str,
        provider: str,
        next_path: str,
        frontend_origin: str,
        guest_user_id: str | None,
        code_verifier: str | None,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO auth_oauth_states(
                state, provider, next_path, frontend_origin, guest_user_id, code_verifier, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (state, provider, next_path, frontend_origin, guest_user_id, code_verifier, created_at),
        )

    def consume_oauth_state(
        self,
        conn: sqlite3.Connection,
        *,
        provider: str,
        state: str,
    ) -> sqlite3.Row | None:
        row = conn.execute(
            "SELECT * FROM auth_oauth_states WHERE state = ? AND provider = ?",
            (state, provider),
        ).fetchone()
        if row is not None:
            conn.execute("DELETE FROM auth_oauth_states WHERE state = ?", (state,))
        return row

    def guest_user_id_for_token_hash(self, conn: sqlite3.Connection, token_hash: str) -> str | None:
        row = conn.execute(
            "SELECT guest_user_id FROM auth_guest_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return row["guest_user_id"] if row is not None else None

    def claim_guest_workspace(self, conn: sqlite3.Connection, *, guest_user_id: str, user_id: str) -> None:
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

    def identities_for_user(self, conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT *
            FROM auth_identities
            WHERE user_id = ?
            ORDER BY
                CASE provider
                    WHEN 'email' THEN 0
                    WHEN 'wechat' THEN 1
                    WHEN 'google' THEN 2
                    WHEN 'github' THEN 3
                    WHEN 'phone' THEN 4
                    WHEN 'apple' THEN 5
                    WHEN 'microsoft' THEN 6
                    WHEN 'x' THEN 7
                    ELSE 9
                END,
                created_at
            """,
            (user_id,),
        ).fetchall()

    def update_user_admin_fields(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        role: str | None,
        status: str | None,
        now: str,
    ) -> None:
        current = self.find_user_by_id(conn, user_id)
        if current is None:
            return
        conn.execute(
            """
            UPDATE users
            SET role = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (role or current["role"], status or current["status"], now, user_id),
        )

    def create_audit_log(
        self,
        conn: sqlite3.Connection,
        *,
        log_id: str,
        actor_user_id: str,
        target_user_id: str | None,
        action: str,
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO admin_audit_logs(id, actor_user_id, target_user_id, action, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (log_id, actor_user_id, target_user_id, action, json.dumps(metadata, ensure_ascii=False), created_at),
        )

    def list_audit_logs(self, conn: sqlite3.Connection, *, limit: int = 100) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT
                admin_audit_logs.*,
                actor.email AS actor_email,
                target.email AS target_email
            FROM admin_audit_logs
            JOIN users AS actor ON actor.id = admin_audit_logs.actor_user_id
            LEFT JOIN users AS target ON target.id = admin_audit_logs.target_user_id
            ORDER BY admin_audit_logs.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
