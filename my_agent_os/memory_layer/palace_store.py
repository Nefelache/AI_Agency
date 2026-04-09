from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import aiosqlite

from my_agent_os.memory_layer.embedding_client import cosine_similarity

WINGS = ("strategy", "execution", "product", "ops", "people")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS v2_palaces (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT 'default',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS v2_rooms (
    id              TEXT PRIMARY KEY,
    palace_id       TEXT NOT NULL,
    wing            TEXT NOT NULL,
    name            TEXT NOT NULL,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL,
    UNIQUE(palace_id, wing, name),
    FOREIGN KEY (palace_id) REFERENCES v2_palaces(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_v2_rooms_palace_wing ON v2_rooms(palace_id, wing);

CREATE TABLE IF NOT EXISTS v2_drawers (
    id                  TEXT PRIMARY KEY,
    room_id             TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    role                TEXT NOT NULL,
    content             TEXT NOT NULL,
    token_count         INTEGER DEFAULT 0,
    source_session_id   TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (room_id) REFERENCES v2_rooms(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_v2_drawers_user_time ON v2_drawers(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_v2_drawers_room_time ON v2_drawers(room_id, created_at DESC);

CREATE TABLE IF NOT EXISTS v2_embeddings (
    drawer_id    TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    vector_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (drawer_id) REFERENCES v2_drawers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_v2_embeddings_created ON v2_embeddings(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS v2_drawers_fts USING fts5(
    content,
    content='v2_drawers',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS v2_drawers_ai AFTER INSERT ON v2_drawers BEGIN
    INSERT INTO v2_drawers_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS v2_drawers_ad AFTER DELETE ON v2_drawers BEGIN
    INSERT INTO v2_drawers_fts(v2_drawers_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS v2_drawers_au AFTER UPDATE ON v2_drawers BEGIN
    INSERT INTO v2_drawers_fts(v2_drawers_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO v2_drawers_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return uuid4().hex[:16]


class PalaceStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def ingest_turn(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
        embedding_model: str,
        vectors: list[list[float]],
        source_session_id: str | None = None,
    ) -> dict[str, str]:
        wing = self.classify_wing(user_msg)
        palace = await self._ensure_default_palace(user_id)
        room_name = self._infer_room_name(user_msg or assistant_msg, wing)
        room = await self._ensure_room(palace["id"], wing, room_name)
        now = _now_iso()

        user_drawer = {
            "id": _short_id(),
            "room_id": room["id"],
            "user_id": user_id,
            "role": "user",
            "content": user_msg,
            "token_count": len(user_msg.split()),
            "source_session_id": source_session_id,
            "created_at": now,
        }
        assistant_drawer = {
            "id": _short_id(),
            "room_id": room["id"],
            "user_id": user_id,
            "role": "assistant",
            "content": assistant_msg,
            "token_count": len(assistant_msg.split()),
            "source_session_id": source_session_id,
            "created_at": now,
        }
        for drawer in (user_drawer, assistant_drawer):
            await self._db.execute(
                """INSERT INTO v2_drawers
                   (id, room_id, user_id, role, content, token_count, source_session_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    drawer["id"],
                    drawer["room_id"],
                    drawer["user_id"],
                    drawer["role"],
                    drawer["content"],
                    drawer["token_count"],
                    drawer["source_session_id"],
                    drawer["created_at"],
                ),
            )
        await self._upsert_embedding(user_drawer["id"], embedding_model, vectors[0], now)
        await self._upsert_embedding(assistant_drawer["id"], embedding_model, vectors[1], now)
        await self._db.execute(
            "UPDATE v2_rooms SET updated_at = ?, last_active_at = ? WHERE id = ?",
            (now, now, room["id"]),
        )
        await self._db.commit()
        return {"wing": wing, "room_id": room["id"]}

    async def palace_overview(self, user_id: str) -> dict[str, Any]:
        palace = await self._ensure_default_palace(user_id)
        wings = {w: {"rooms": 0, "drawers": 0, "recent": []} for w in WINGS}
        async with self._db.execute(
            """
            SELECT r.wing, COUNT(DISTINCT r.id) as room_count, COUNT(d.id) as drawer_count
            FROM v2_rooms r
            LEFT JOIN v2_drawers d ON d.room_id = r.id
            WHERE r.palace_id = ?
            GROUP BY r.wing
            """,
            (palace["id"],),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            wing = row["wing"]
            if wing in wings:
                wings[wing]["rooms"] = row["room_count"]
                wings[wing]["drawers"] = row["drawer_count"]

        async with self._db.execute(
            """
            SELECT r.wing, d.role, d.content, d.created_at
            FROM v2_drawers d
            JOIN v2_rooms r ON r.id = d.room_id
            JOIN v2_palaces p ON p.id = r.palace_id
            WHERE p.user_id = ?
            ORDER BY d.created_at DESC
            LIMIT 30
            """,
            (user_id,),
        ) as cur:
            recent = await cur.fetchall()
        for row in recent:
            wing = row["wing"]
            if wing not in wings:
                continue
            if len(wings[wing]["recent"]) >= 4:
                continue
            wings[wing]["recent"].append(
                {
                    "role": row["role"],
                    "content": (row["content"] or "")[:180],
                    "created_at": row["created_at"],
                }
            )
        return {"palace": palace["name"], "wings": wings}

    async def vector_search(
        self,
        user_id: str,
        query_vector: list[float],
        query_text: str | None = None,
        top_k: int = 8,
        wing: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        wing_clause = ""
        if wing and wing in WINGS:
            wing_clause = "AND r.wing = ?"
            params.append(wing)
        async with self._db.execute(
            f"""
            SELECT d.id, d.role, d.content, d.created_at, r.wing, r.name as room_name, e.vector_json
            FROM v2_drawers d
            JOIN v2_embeddings e ON e.drawer_id = d.id
            JOIN v2_rooms r ON r.id = d.room_id
            JOIN v2_palaces p ON p.id = r.palace_id
            WHERE p.user_id = ? {wing_clause}
            ORDER BY d.created_at DESC
            LIMIT 1200
            """,
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()

        scored: list[dict[str, Any]] = []
        lexical_scores = await self._lexical_search(user_id=user_id, query_text=query_text or "", wing=wing, limit=400)
        for row in rows:
            try:
                vec = json.loads(row["vector_json"] or "[]")
            except json.JSONDecodeError:
                continue
            vec_score = cosine_similarity(query_vector, vec)
            lex_score = lexical_scores.get(row["id"], 0.0)
            score = 0.72 * vec_score + 0.28 * lex_score
            scored.append(
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "wing": row["wing"],
                    "room": row["room_name"],
                    "vector_score": round(vec_score, 5),
                    "lexical_score": round(lex_score, 5),
                    "score": round(score, 5),
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    async def list_rooms(self, user_id: str, wing: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        wing_clause = ""
        if wing and wing in WINGS:
            wing_clause = "AND r.wing = ?"
            params.append(wing)
        params.append(limit)
        async with self._db.execute(
            f"""
            SELECT r.id, r.wing, r.name, r.last_active_at, COUNT(d.id) as drawers
            FROM v2_rooms r
            JOIN v2_palaces p ON p.id = r.palace_id
            LEFT JOIN v2_drawers d ON d.room_id = r.id
            WHERE p.user_id = ? {wing_clause}
            GROUP BY r.id, r.wing, r.name, r.last_active_at
            ORDER BY r.last_active_at DESC
            LIMIT ?
            """,
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": row["id"],
                "wing": row["wing"],
                "name": row["name"],
                "drawers": row["drawers"],
                "last_active_at": row["last_active_at"],
            }
            for row in rows
        ]

    async def list_recent_drawers(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        async with self._db.execute(
            """
            SELECT d.id, d.role, d.content, d.created_at, r.wing, r.name as room_name
            FROM v2_drawers d
            JOIN v2_rooms r ON r.id = d.room_id
            JOIN v2_palaces p ON p.id = r.palace_id
            WHERE p.user_id = ?
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
                "wing": r["wing"],
                "room": r["room_name"],
            }
            for r in rows
        ]

    async def delete_drawer(self, drawer_id: str) -> None:
        await self._db.execute("DELETE FROM v2_drawers WHERE id = ?", (drawer_id,))
        await self._db.commit()

    @staticmethod
    def classify_wing(text: str) -> str:
        low = (text or "").lower()
        rules = {
            "strategy": ("strategy", "plan", "roadmap", "方向", "战略", "规划"),
            "product": ("feature", "product", "ux", "ui", "需求", "产品", "交互"),
            "ops": ("deploy", "docker", "infra", "incident", "运维", "部署", "告警"),
            "people": ("team", "stakeholder", "person", "用户", "同事", "客户"),
            "execution": ("todo", "task", "next", "execute", "执行", "任务", "推进"),
        }
        for wing, keywords in rules.items():
            if any(k in low for k in keywords):
                return wing
        return "execution"

    async def _upsert_embedding(
        self,
        drawer_id: str,
        model: str,
        vector: list[float],
        created_at: str,
    ) -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO v2_embeddings
               (drawer_id, model, dim, vector_json, created_at)
               VALUES (?,?,?,?,?)""",
            (drawer_id, model, len(vector), json.dumps(vector), created_at),
        )

    async def _ensure_default_palace(self, user_id: str) -> dict[str, Any]:
        now = _now_iso()
        async with self._db.execute(
            "SELECT id, user_id, name FROM v2_palaces WHERE user_id = ? AND name = 'default'",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return {"id": row["id"], "user_id": row["user_id"], "name": row["name"]}
        palace_id = _short_id()
        await self._db.execute(
            "INSERT INTO v2_palaces (id, user_id, name, created_at, updated_at) VALUES (?,?,?,?,?)",
            (palace_id, user_id, "default", now, now),
        )
        await self._db.commit()
        return {"id": palace_id, "user_id": user_id, "name": "default"}

    async def _ensure_room(self, palace_id: str, wing: str, name: str) -> dict[str, Any]:
        async with self._db.execute(
            "SELECT id, wing, name FROM v2_rooms WHERE palace_id = ? AND wing = ? AND name = ?",
            (palace_id, wing, name),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return {"id": row["id"], "wing": row["wing"], "name": row["name"]}
        now = _now_iso()
        room_id = _short_id()
        await self._db.execute(
            """INSERT INTO v2_rooms
               (id, palace_id, wing, name, tags_json, created_at, updated_at, last_active_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (room_id, palace_id, wing, name, "[]", now, now, now),
        )
        await self._db.commit()
        return {"id": room_id, "wing": wing, "name": name}

    async def _lexical_search(
        self,
        user_id: str,
        query_text: str,
        wing: str | None,
        limit: int,
    ) -> dict[str, float]:
        fts_query = self._build_fts_query(query_text)
        if not fts_query:
            return {}
        params: list[Any] = [fts_query, user_id]
        wing_clause = ""
        if wing and wing in WINGS:
            wing_clause = "AND r.wing = ?"
            params.append(wing)
        params.append(limit)
        async with self._db.execute(
            f"""
            SELECT d.id, bm25(v2_drawers_fts) as rank
            FROM v2_drawers_fts
            JOIN v2_drawers d ON d.rowid = v2_drawers_fts.rowid
            JOIN v2_rooms r ON r.id = d.room_id
            JOIN v2_palaces p ON p.id = r.palace_id
            WHERE v2_drawers_fts MATCH ?
              AND p.user_id = ? {wing_clause}
            ORDER BY rank
            LIMIT ?
            """,
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        out: dict[str, float] = {}
        for row in rows:
            rank = float(row["rank"] or 0.0)
            out[row["id"]] = 1.0 - (1.0 / (1.0 + abs(rank)))
        return out

    @staticmethod
    def _build_fts_query(text: str) -> str:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", (text or "").lower())
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens[:12])

    @staticmethod
    def _infer_room_name(text: str, wing: str) -> str:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", (text or "").lower())
        stop = {
            "the", "and", "with", "that", "this", "from", "have", "what", "how",
            "我们", "你们", "这个", "那个", "今天", "然后", "需要", "问题",
        }
        core = [t for t in tokens if t not in stop][:3]
        if not core:
            return f"{wing}-main"
        return f"{wing}-{'-'.join(core)}"[:48]
