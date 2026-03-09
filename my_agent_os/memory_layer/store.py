"""
Memory Store — SQLite-only persistence with dual retrieval.

Structured storage  → standard tables with indexes
Full-text search    → SQLite FTS5 (built-in, zero external deps)
Hash index          → entity_hash → memory_id mapping for O(1) lookups

Design: all public methods are async via aiosqlite.
No external vector DB dependency — everything lives in one SQLite file.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from my_agent_os.memory_layer.models import (
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    Session,
    SessionStatus,
    Turn,
    entity_hash,
    utcnow,
)

logger = logging.getLogger(__name__)

_ISO = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def _to_iso(dt: datetime) -> str:
    return dt.strftime(_ISO)


def _from_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


# ── SQL Schema ───────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    memory_type     TEXT NOT NULL,
    content         TEXT NOT NULL,
    summary         TEXT,
    key_points      TEXT DEFAULT '[]',
    entities        TEXT DEFAULT '[]',
    priority        REAL DEFAULT 0.5,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_accessed   TEXT,
    access_count    INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'active',
    session_id      TEXT,
    user_id         TEXT DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(user_id, memory_type, status);

CREATE TABLE IF NOT EXISTS hash_index (
    entity_hash TEXT NOT NULL,
    entity_text TEXT NOT NULL,
    memory_id   TEXT NOT NULL,
    PRIMARY KEY (entity_hash, memory_id),
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_entity_hash ON hash_index(entity_hash);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT DEFAULT 'default',
    status      TEXT DEFAULT 'active',
    topic       TEXT,
    summary     TEXT,
    created_at  TEXT NOT NULL,
    sealed_at   TEXT,
    turn_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    summary,
    content='memories',
    content_rowid='rowid'
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, summary)
    VALUES (new.rowid, new.content, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, summary)
    VALUES ('delete', old.rowid, old.content, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, summary)
    VALUES ('delete', old.rowid, old.content, old.summary);
    INSERT INTO memories_fts(rowid, content, summary)
    VALUES (new.rowid, new.content, new.summary);
END;
"""


class MemoryStore:
    """Async SQLite store with FTS5 full-text search and hash indexing."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ────────────────────────────────────────

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.executescript(_FTS_SCHEMA)
        await self._db.executescript(_FTS_TRIGGERS)
        await self._db.commit()
        logger.info("MemoryStore initialized (SQLite + FTS5): %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Memory CRUD ──────────────────────────────────────

    async def add_memory(self, record: MemoryRecord) -> str:
        await self._db.execute(
            """INSERT INTO memories
               (id, memory_type, content, summary, key_points, entities,
                priority, created_at, updated_at, last_accessed,
                access_count, status, session_id, user_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.id,
                record.memory_type.value,
                record.content,
                record.summary,
                json.dumps(record.key_points, ensure_ascii=False),
                json.dumps(record.entities, ensure_ascii=False),
                record.priority,
                _to_iso(record.created_at),
                _to_iso(record.updated_at),
                _to_iso(record.last_accessed) if record.last_accessed else None,
                record.access_count,
                record.status.value,
                record.session_id,
                record.user_id,
            ),
        )
        await self._db.commit()
        await self._update_hash_index(record.id, record.entities)
        return record.id

    async def update_memory(self, memory_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = _to_iso(utcnow())

        if "key_points" in fields and isinstance(fields["key_points"], list):
            fields["key_points"] = json.dumps(fields["key_points"], ensure_ascii=False)
        if "entities" in fields and isinstance(fields["entities"], list):
            fields["entities"] = json.dumps(fields["entities"], ensure_ascii=False)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [memory_id]
        await self._db.execute(
            f"UPDATE memories SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()

        if "entities" in fields:
            entities = json.loads(fields["entities"]) if isinstance(fields["entities"], str) else fields["entities"]
            await self._delete_hash_entries(memory_id)
            await self._update_hash_index(memory_id, entities)

    async def delete_memory(self, memory_id: str) -> None:
        await self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self._db.commit()
        await self._delete_hash_entries(memory_id)

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        async with self._db.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_memory(row) if row else None

    async def get_memories_by_type(
        self, user_id: str, memory_type: MemoryType, limit: int = 50
    ) -> list[MemoryRecord]:
        async with self._db.execute(
            """SELECT * FROM memories
               WHERE user_id = ? AND memory_type = ? AND status = 'active'
               ORDER BY priority DESC, updated_at DESC
               LIMIT ?""",
            (user_id, memory_type.value, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_memory(r) for r in rows]

    async def get_all_memories(self, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        async with self._db.execute(
            """SELECT * FROM memories
               WHERE user_id = ? AND status = 'active'
               ORDER BY priority DESC, updated_at DESC
               LIMIT ?""",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_memory(r) for r in rows]

    # ── Hash Index ───────────────────────────────────────

    async def _update_hash_index(self, memory_id: str, entities: list[str]) -> None:
        for ent in entities:
            h = entity_hash(ent)
            await self._db.execute(
                "INSERT OR IGNORE INTO hash_index (entity_hash, entity_text, memory_id) VALUES (?,?,?)",
                (h, ent.strip().lower(), memory_id),
            )
        await self._db.commit()

    async def _delete_hash_entries(self, memory_id: str) -> None:
        await self._db.execute(
            "DELETE FROM hash_index WHERE memory_id = ?", (memory_id,)
        )
        await self._db.commit()

    async def lookup_by_entities(self, entities: list[str]) -> list[str]:
        """O(1) hash lookup → returns list of memory IDs."""
        if not entities:
            return []
        hashes = [entity_hash(e) for e in entities]
        placeholders = ",".join("?" * len(hashes))
        async with self._db.execute(
            f"SELECT DISTINCT memory_id FROM hash_index WHERE entity_hash IN ({placeholders})",
            hashes,
        ) as cur:
            rows = await cur.fetchall()
        return [r["memory_id"] for r in rows]

    # ── Full-Text Search (replaces vector search) ────────

    async def fulltext_search(
        self,
        query: str,
        top_k: int = 10,
        user_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """
        FTS5 search → returns list of (memory_id, relevance_rank) tuples.
        Rank is a BM25-based score (lower = more relevant in FTS5).
        """
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT m.id, fts.rank
            FROM memories_fts fts
            JOIN memories m ON m.rowid = fts.rowid
            WHERE memories_fts MATCH ?
              AND m.status = 'active'
        """
        params: list[Any] = [fts_query]

        if user_id:
            sql += " AND m.user_id = ?"
            params.append(user_id)

        sql += " ORDER BY fts.rank LIMIT ?"
        params.append(top_k)

        try:
            async with self._db.execute(sql, params) as cur:
                rows = await cur.fetchall()
            return [(r["id"], r["rank"]) for r in rows]
        except Exception as e:
            logger.warning("FTS search failed: %s", e)
            return []

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """Build an FTS5 query from natural language input."""
        import re
        tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", query)
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens[:15])

    # ── Access Tracking ──────────────────────────────────

    async def touch_memory(self, memory_id: str) -> None:
        now = _to_iso(utcnow())
        await self._db.execute(
            """UPDATE memories
               SET last_accessed = ?, access_count = access_count + 1
               WHERE id = ?""",
            (now, memory_id),
        )
        await self._db.commit()

    # ── Session CRUD ─────────────────────────────────────

    async def create_session(self, user_id: str) -> Session:
        session = Session(user_id=user_id)
        await self._db.execute(
            "INSERT INTO sessions (id, user_id, status, created_at, turn_count) VALUES (?,?,?,?,?)",
            (session.id, session.user_id, session.status.value,
             _to_iso(session.created_at), 0),
        )
        await self._db.commit()
        return session

    async def get_active_session(self, user_id: str) -> Session | None:
        async with self._db.execute(
            """SELECT * FROM sessions
               WHERE user_id = ? AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def get_session(self, session_id: str) -> Session | None:
        async with self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def seal_session(
        self, session_id: str, summary: str, topic: str
    ) -> None:
        now = _to_iso(utcnow())
        await self._db.execute(
            """UPDATE sessions
               SET status = 'sealed', summary = ?, topic = ?, sealed_at = ?
               WHERE id = ?""",
            (summary, topic, now, session_id),
        )
        await self._db.commit()

    async def list_sessions(
        self, user_id: str, limit: int = 20
    ) -> list[Session]:
        async with self._db.execute(
            """SELECT * FROM sessions WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_session(r) for r in rows]

    # ── Turn CRUD ────────────────────────────────────────

    async def add_turn(self, session_id: str, role: str, content: str) -> None:
        now = _to_iso(utcnow())
        await self._db.execute(
            "INSERT INTO turns (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, content, now),
        )
        await self._db.execute(
            "UPDATE sessions SET turn_count = turn_count + 1 WHERE id = ?",
            (session_id,),
        )
        await self._db.commit()

    async def get_turns(self, session_id: str) -> list[Turn]:
        async with self._db.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Turn(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                created_at=_from_iso(r["created_at"]),
            )
            for r in rows
        ]

    # ── Stats ────────────────────────────────────────────

    async def stats(self, user_id: str) -> dict[str, Any]:
        counts = {}
        for mt in MemoryType:
            async with self._db.execute(
                "SELECT COUNT(*) as c FROM memories WHERE user_id = ? AND memory_type = ?",
                (user_id, mt.value),
            ) as cur:
                row = await cur.fetchone()
                counts[mt.value] = row["c"] if row else 0

        async with self._db.execute(
            "SELECT COUNT(*) as c FROM sessions WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            counts["sessions"] = row["c"] if row else 0

        return counts

    # ── Internal Helpers ─────────────────────────────────

    @staticmethod
    def _row_to_memory(row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            summary=row["summary"],
            key_points=json.loads(row["key_points"] or "[]"),
            entities=json.loads(row["entities"] or "[]"),
            priority=row["priority"],
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
            last_accessed=_from_iso(row["last_accessed"]) if row["last_accessed"] else None,
            access_count=row["access_count"],
            status=MemoryStatus(row["status"]),
            session_id=row["session_id"],
            user_id=row["user_id"],
        )

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(
            id=row["id"],
            user_id=row["user_id"],
            status=SessionStatus(row["status"]),
            topic=row["topic"],
            summary=row["summary"],
            created_at=_from_iso(row["created_at"]),
            sealed_at=_from_iso(row["sealed_at"]) if row["sealed_at"] else None,
            turn_count=row["turn_count"],
        )
