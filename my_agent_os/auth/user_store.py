"""
User Store — SQLite-backed multi-user registry.

Separate from the memory DB so that users.db can be backed up independently.
Passwords are hashed with PBKDF2-HMAC-SHA256 (100k iterations).

Table: users(id, email, password_hash, plan, role, created_at, stripe_customer_id)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(os.getenv("USERS_DB_PATH", "my_agent_os/memory_layer/data/users.db"))
_ITERS   = 100_000
_HASH_ALG = "sha256"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk   = hashlib.pbkdf2_hmac(_HASH_ALG, password.encode(), salt.encode(), _ITERS)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        return secrets.compare_digest(stored, _hash_password(password, salt))
    except Exception:
        return False


class UserStore:
    """Sync SQLite wrapper (run in executor for async contexts)."""

    def __init__(self, db_path: str | Path = _DB_PATH):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id                 TEXT PRIMARY KEY,
                    email              TEXT UNIQUE NOT NULL,
                    password_hash      TEXT NOT NULL,
                    plan               TEXT DEFAULT 'free',
                    role               TEXT DEFAULT 'owner',
                    created_at         TEXT NOT NULL,
                    stripe_customer_id TEXT DEFAULT '',
                    stripe_sub_id      TEXT DEFAULT '',
                    sub_status         TEXT DEFAULT 'none'
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """)

    # ── CRUD ─────────────────────────────────────────────────────

    def create_user(self, email: str, password: str, plan: str = "free") -> dict[str, Any]:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Invalid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        uid  = str(uuid.uuid4())
        phash = _hash_password(password)
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, plan, created_at) VALUES (?,?,?,?,?)",
                    (uid, email, phash, plan, _now_iso()),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"Email already registered: {email}")
        return self.get_user_by_id(uid)

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        email = email.strip().lower()
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        return dict(row)

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def update_plan(self, user_id: str, plan: str, stripe_customer_id: str = "",
                    stripe_sub_id: str = "", sub_status: str = "active") -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE users SET plan=?, stripe_customer_id=?, stripe_sub_id=?, sub_status=?
                   WHERE id=?""",
                (plan, stripe_customer_id, stripe_sub_id, sub_status, user_id),
            )

    def list_users(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, email, plan, role, created_at, sub_status FROM users LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# Singleton used across the app
_store: UserStore | None = None


def get_user_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store


async def get_user_store_async() -> UserStore:
    return await asyncio.get_event_loop().run_in_executor(None, get_user_store)
