from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _workspace_setting_key(prefix: str, owner_user_id: str) -> str:
    return f"{prefix}:{owner_user_id}"


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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
            self.ensure_email_identities(conn)

    def _ensure_user_column(self, conn: sqlite3.Connection, name: str, definition: str) -> None:
        self._ensure_table_column(conn, "users", name, definition)

    def _ensure_table_column(self, conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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

    def principal_id_exists(self, conn: sqlite3.Connection, principal_id: str) -> bool:
        return bool(
            conn.execute("SELECT 1 FROM users WHERE id = ? LIMIT 1", (principal_id,)).fetchone()
            or conn.execute(
                "SELECT 1 FROM auth_guest_sessions WHERE guest_user_id = ? LIMIT 1",
                (principal_id,),
            ).fetchone()
        )

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
    ) -> None:
        conn.execute(
            """
            INSERT INTO users(id, email, phone, password_salt, password_hash, role, display_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, phone, password_salt, password_hash, role, display_name, created_at),
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
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))
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
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO users(
                id, email, password_salt, password_hash, role, display_name, avatar_url, created_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, password_salt, password_hash, role, display_name, avatar_url, now, now),
        )

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
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE users
            SET
                last_login_at = ?,
                display_name = COALESCE(NULLIF(?, ''), display_name),
                avatar_url = COALESCE(NULLIF(?, ''), avatar_url)
            WHERE id = ?
            """,
            (now, display_name or "", avatar_url or "", user_id),
        )

    def find_user_by_session_token(self, conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT users.*
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ?
            """,
            (token_hash,),
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

    def list_users(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute("SELECT * FROM users ORDER BY created_at DESC, email").fetchall()

    def admin_stats(self, conn: sqlite3.Connection) -> sqlite3.Row:
        return conn.execute(
            """
            SELECT
                (SELECT count(*) FROM users) AS users,
                (SELECT count(*) FROM users WHERE role = 'admin') AS admins,
                (SELECT count(*) FROM course_packages) AS packages,
                (SELECT count(*) FROM lessons) AS lessons,
                (SELECT count(*) FROM resources) AS resources
            """
        ).fetchone()

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

    def guest_user_id_for_token_hash(self, conn: sqlite3.Connection, token_hash: str) -> str | None:
        row = conn.execute(
            "SELECT guest_user_id FROM auth_guest_sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return row["guest_user_id"] if row is not None else None

    def delete_guest_sessions(self, conn: sqlite3.Connection, *, guest_user_id: str) -> None:
        conn.execute("DELETE FROM auth_guest_sessions WHERE guest_user_id = ?", (guest_user_id,))

    def claim_guest_workspace(self, conn: sqlite3.Connection, *, guest_user_id: str, user_id: str) -> None:
        owner_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for row in owner_tables:
            table_name = str(row["name"])
            quoted_table = _quoted_identifier(table_name)
            columns = {
                str(column["name"])
                for column in conn.execute(f"PRAGMA table_xinfo({quoted_table})").fetchall()
            }
            if "owner_user_id" not in columns:
                continue
            conn.execute(
                f"UPDATE {quoted_table} SET owner_user_id = ? WHERE owner_user_id = ?",
                (user_id, guest_user_id),
            )

        workspace_settings_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspace_settings'"
        ).fetchone()
        if workspace_settings_exists is not None:
            for prefix in ("active_package_id", "workspace_revision"):
                guest_setting_key = _workspace_setting_key(prefix, guest_user_id)
                user_setting_key = _workspace_setting_key(prefix, user_id)
                guest_row = conn.execute(
                    "SELECT value FROM workspace_settings WHERE key = ?",
                    (guest_setting_key,),
                ).fetchone()
                if prefix == "workspace_revision":
                    user_row = conn.execute(
                        "SELECT value FROM workspace_settings WHERE key = ?",
                        (user_setting_key,),
                    ).fetchone()
                    try:
                        guest_revision = int(guest_row["value"]) if guest_row is not None else 0
                    except (TypeError, ValueError):
                        guest_revision = 0
                    try:
                        user_revision = int(user_row["value"]) if user_row is not None else 0
                    except (TypeError, ValueError):
                        user_revision = 0
                    conn.execute(
                        "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                        (user_setting_key, str(max(guest_revision, user_revision) + 1)),
                    )
                    if guest_row is not None:
                        conn.execute("DELETE FROM workspace_settings WHERE key = ?", (guest_setting_key,))
                    continue
                if guest_row is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                    (user_setting_key, str(guest_row["value"])),
                )
                conn.execute("DELETE FROM workspace_settings WHERE key = ?", (guest_setting_key,))
        self.delete_guest_sessions(conn, guest_user_id=guest_user_id)

    def identities_for_user(self, conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT *
            FROM auth_identities
            WHERE user_id = ?
            ORDER BY
                CASE provider
                    WHEN 'email' THEN 0
                    WHEN 'phone' THEN 1
                    WHEN 'google' THEN 2
                    WHEN 'apple' THEN 3
                    WHEN 'github' THEN 4
                    WHEN 'microsoft' THEN 5
                    WHEN 'x' THEN 6
                    ELSE 9
                END,
                created_at
            """,
            (user_id,),
        ).fetchall()

    def create_session(self, conn: sqlite3.Connection, *, token_hash: str, user_id: str, now: str) -> None:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, now, now),
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
