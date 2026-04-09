"""Memory engine v2 (MemoryPalace style, embedding-first)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from my_agent_os.config.settings import settings
from my_agent_os.memory_layer.embedding_client import EmbeddingClient
from my_agent_os.memory_layer.models import InjectionContext, MemoryRecord, MemoryType, Session
from my_agent_os.memory_layer.palace_store import PalaceStore, WINGS

logger = logging.getLogger(__name__)

LLMFunc = Callable[[str, str, bool], Awaitable[str]]


class MemoryEngine:
    def __init__(
        self,
        db_path: str,
        llm: LLMFunc,
        top_k: int = 5,
        decay_days: float = 7.0,
        max_injection_chars: int = 2000,
    ):
        self._llm = llm
        self._top_k = top_k
        self._max_chars = max_injection_chars
        self._initialized = False
        self._enabled = bool(settings.MEMORY_V2_ENABLED)
        active_db = settings.MEMORY_V2_DB_PATH if self._enabled else db_path
        self._palace = PalaceStore(active_db)
        self._embed = EmbeddingClient()

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._palace.initialize()
        self._initialized = True
        logger.info("MemoryEngine v2 initialized.")

    async def close(self) -> None:
        await self._palace.close()
        self._initialized = False

    async def retrieve(self, user_id: str, query: str) -> InjectionContext:
        await self._ensure_init()
        if not query.strip():
            return InjectionContext()
        qvec = await self._embed.embed_text(query)
        hits = await self._palace.vector_search(user_id, qvec, query_text=query, top_k=self._top_k)
        if not hits:
            return InjectionContext()
        summary_parts: list[str] = []
        detail_parts: list[str] = []
        char_budget = max(800, self._max_chars)
        used = 0
        for h in hits:
            snippet = (h["content"] or "").strip().replace("\n", " ")
            if not snippet:
                continue
            line = f"- [{h['wing']}/{h['room']}] {snippet[:180]}"
            summary_parts.append(line)
            if used < char_budget:
                detail = f"  ({h['role']}, score={h['score']}) {snippet[:320]}"
                detail_parts.append(detail)
                used += len(detail)
        return InjectionContext(
            summary_layer="\n".join(summary_parts),
            decision_layer="",
            detail_layer="\n".join(detail_parts),
            source_ids=[h["id"] for h in hits],
            token_estimate=used // 4,
        )

    async def process_turn(self, user_id: str, user_msg: str, assistant_msg: str) -> bool:
        await self._ensure_init()
        vectors = await self._embed.embed_texts([user_msg or "", assistant_msg or ""])
        await self._palace.ingest_turn(
            user_id=user_id,
            user_msg=user_msg or "",
            assistant_msg=assistant_msg or "",
            embedding_model=settings.EMBEDDING_MODEL,
            vectors=vectors,
            source_session_id=user_id,
        )
        return False

    def process_turn_background(self, user_id: str, user_msg: str, assistant_msg: str) -> None:
        asyncio.create_task(self._safe_process(user_id, user_msg, assistant_msg))

    async def _safe_process(self, user_id: str, user_msg: str, assistant_msg: str) -> None:
        try:
            await self.process_turn(user_id, user_msg, assistant_msg)
        except Exception as e:
            logger.error("Background memory processing failed: %s", e)

    async def force_seal_session(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        recent = await self._palace.list_recent_drawers(user_id, limit=8)
        if not recent:
            return {"status": "no_active_session"}
        wing = recent[0]["wing"]
        summary = " | ".join((r["content"] or "")[:72] for r in recent[:3] if r["content"])
        return {"status": "sealed", "session_id": user_id, "topic": wing, "summary": summary}

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[Session]:
        await self._ensure_init()
        return []

    async def search_memories(self, user_id: str, query: str, top_k: int = 10) -> list[MemoryRecord]:
        await self._ensure_init()
        qvec = await self._embed.embed_text(query)
        hits = await self._palace.vector_search(user_id, qvec, query_text=query, top_k=top_k)
        return [self._drawer_to_record(h) for h in hits]

    async def get_all_memories(self, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        await self._ensure_init()
        rows = await self._palace.list_recent_drawers(user_id, limit=limit)
        return [self._drawer_to_record(r) for r in rows]

    async def delete_memory(self, memory_id: str) -> None:
        await self._ensure_init()
        await self._palace.delete_drawer(memory_id)

    async def get_tasks_by_status(self, user_id: str, status: str) -> list[MemoryRecord]:
        await self._ensure_init()
        return []

    async def stats(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        overview = await self._palace.palace_overview(user_id)
        wings = overview.get("wings", {})
        sem = wings.get("strategy", {}).get("drawers", 0) + wings.get("product", {}).get("drawers", 0)
        epi = wings.get("people", {}).get("drawers", 0)
        proc = wings.get("execution", {}).get("drawers", 0) + wings.get("ops", {}).get("drawers", 0)
        out = {"semantic": sem, "episodic": epi, "procedural": proc}
        for wing in WINGS:
            out[f"wing_{wing}"] = wings.get(wing, {}).get("drawers", 0)
        return out

    async def run_maintenance(self, user_id: str, lookback_days: int = 7, max_items: int = 30) -> dict[str, Any]:
        await self._ensure_init()
        return {"consolidated": 0, "pruned": 0}

    async def ingest_file(self, file_path: str, user_id: str = "default") -> int:
        await self._ensure_init()
        path = Path(file_path)
        raw = path.read_text(encoding="utf-8")
        chunks = self._chunk_text(raw, max_chars=900, overlap=120)
        count = 0
        for chunk in chunks:
            await self.process_turn(user_id, f"[doc] {path.name}", chunk)
            count += 1
        return count

    async def palace_overview(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        return await self._palace.palace_overview(user_id)

    async def palace_rooms(self, user_id: str, wing: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        await self._ensure_init()
        return await self._palace.list_rooms(user_id=user_id, wing=wing, limit=limit)

    async def palace_search(self, user_id: str, query: str, top_k: int = 8, wing: str | None = None) -> list[dict[str, Any]]:
        await self._ensure_init()
        qvec = await self._embed.embed_text(query)
        return await self._palace.vector_search(user_id, qvec, query_text=query, top_k=top_k, wing=wing)

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + max_chars
            chunks.append(text[start:end])
            start += max_chars - overlap
        return chunks

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self.initialize()

    @staticmethod
    def _drawer_to_record(row: dict[str, Any]) -> MemoryRecord:
        wing = row.get("wing", "")
        mt = MemoryType.SEMANTIC
        if wing == "people":
            mt = MemoryType.EPISODIC
        elif wing in ("execution", "ops"):
            mt = MemoryType.PROCEDURAL
        iso = row.get("created_at")
        try:
            dt = datetime.fromisoformat(iso) if iso else datetime.now(timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
        content = row.get("content", "")
        return MemoryRecord(
            id=row.get("id", ""),
            memory_type=mt,
            content=content,
            summary=content[:120],
            key_points=[],
            entities=[],
            priority=max(0.1, float(row.get("score", 0.5) or 0.5)),
            created_at=dt,
            updated_at=dt,
            access_count=0,
            user_id="default",
            metadata={"wing": wing, "room": row.get("room")},
        )
