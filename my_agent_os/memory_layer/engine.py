"""
Memory Engine — The unified façade for the entire memory subsystem.

All external code (router_engine, API routes) talks ONLY to this class.
Internal components (store, writer, reader, session) are never exposed.

Lifecycle:
  engine = MemoryEngine(settings)
  await engine.initialize()
  ...
  await engine.close()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from my_agent_os.memory_layer.models import (
    InjectionContext,
    MemoryRecord,
    MemoryType,
    Session,
)
from my_agent_os.memory_layer.reader import MemoryReader
from my_agent_os.memory_layer.session import SessionManager
from my_agent_os.memory_layer.store import MemoryStore
from my_agent_os.memory_layer.writer import MemoryWriter

logger = logging.getLogger(__name__)

LLMFunc = Callable[[str, str, bool], Awaitable[str]]


class MemoryEngine:
    """
    Single entry point for memory operations.

    Usage:
        engine = MemoryEngine(db_path, llm_func)
        await engine.initialize()

        # Before LLM call — retrieve relevant memories
        context = await engine.retrieve(user_id, query)

        # After LLM call — process the turn in background
        engine.process_turn_background(user_id, user_msg, assistant_msg)
    """

    def __init__(
        self,
        db_path: str,
        llm: LLMFunc,
        top_k: int = 5,
        decay_days: float = 7.0,
        max_injection_chars: int = 2000,
    ):
        self._store = MemoryStore(db_path)
        self._writer = MemoryWriter(self._store, llm)
        self._reader = MemoryReader(
            self._store, llm,
            top_k=top_k,
            decay_days=decay_days,
            max_injection_chars=max_injection_chars,
        )
        self._session_mgr = SessionManager(self._store, self._writer)
        self._initialized = False

    # ── Lifecycle ────────────────────────────────────────

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._store.initialize()
        self._initialized = True
        logger.info("MemoryEngine initialized.")

    async def close(self) -> None:
        await self._store.close()
        self._initialized = False

    # ── Core Operations ──────────────────────────────────

    async def retrieve(
        self, user_id: str, query: str
    ) -> InjectionContext:
        """
        Retrieve relevant memories for a given query.
        Called BEFORE the LLM generates a response.
        """
        await self._ensure_init()
        return await self._reader.retrieve(query, user_id)

    async def process_turn(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
    ) -> bool:
        """
        Process a completed conversation turn:
          1. Ensure an active session exists.
          2. Record both turns.
          3. Extract memories (facts, events, patterns).
          4. Consolidate with existing memories.
          5. Seal session if topic concluded.

        Returns True if the session was sealed.
        """
        await self._ensure_init()
        session = await self._session_mgr.get_or_create(user_id)

        await self._session_mgr.record_turn(session.id, "user", user_msg)
        await self._session_mgr.record_turn(session.id, "assistant", assistant_msg)

        sealed = await self._session_mgr.process_and_maybe_seal(
            session.id, user_msg, assistant_msg, user_id
        )
        return sealed

    def process_turn_background(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
    ) -> None:
        """
        Fire-and-forget version of process_turn.
        The response is returned to the user immediately;
        memory processing happens asynchronously.
        """
        asyncio.create_task(
            self._safe_process(user_id, user_msg, assistant_msg)
        )

    async def _safe_process(
        self, user_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        try:
            await self.process_turn(user_id, user_msg, assistant_msg)
        except Exception as e:
            logger.error("Background memory processing failed: %s", e)

    # ── Session Management ───────────────────────────────

    async def force_seal_session(self, user_id: str) -> dict[str, Any]:
        """Manually seal the current active session."""
        await self._ensure_init()
        session = await self._store.get_active_session(user_id)
        if not session:
            return {"status": "no_active_session"}

        await self._session_mgr.force_seal(session.id, user_id)
        updated = await self._store.get_session(session.id)
        return {
            "status": "sealed",
            "session_id": session.id,
            "topic": updated.topic if updated else None,
            "summary": updated.summary if updated else None,
        }

    async def list_sessions(
        self, user_id: str, limit: int = 20
    ) -> list[Session]:
        await self._ensure_init()
        return await self._store.list_sessions(user_id, limit)

    # ── Memory Management ────────────────────────────────

    async def search_memories(
        self, user_id: str, query: str, top_k: int = 10
    ) -> list[MemoryRecord]:
        """Search memories by full-text search."""
        await self._ensure_init()
        hits = await self._store.fulltext_search(query, top_k=top_k, user_id=user_id)
        records = []
        for mid, _ in hits:
            rec = await self._store.get_memory(mid)
            if rec:
                records.append(rec)
        return records

    async def get_all_memories(
        self, user_id: str, limit: int = 100
    ) -> list[MemoryRecord]:
        await self._ensure_init()
        return await self._store.get_all_memories(user_id, limit)

    async def delete_memory(self, memory_id: str) -> None:
        await self._ensure_init()
        await self._store.delete_memory(memory_id)

    async def stats(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        return await self._store.stats(user_id)

    # ── File Ingestion ───────────────────────────────────

    async def ingest_file(
        self, file_path: str, user_id: str = "default"
    ) -> int:
        """
        Ingest a document into memory.
        Reads the file, chunks it, and stores each chunk as a semantic memory.
        Returns the number of memories created.
        """
        await self._ensure_init()
        from pathlib import Path

        path = Path(file_path)
        raw = path.read_text(encoding="utf-8")

        chunks = self._chunk_text(raw, max_chars=800, overlap=100)
        count = 0
        for chunk in chunks:
            record = MemoryRecord(
                memory_type=MemoryType.SEMANTIC,
                content=chunk,
                entities=self._writer._extract_entities_simple(chunk),
                user_id=user_id,
                priority=0.4,
            )
            await self._store.add_memory(record)
            count += 1

        logger.info("Ingested %d chunks from %s", count, file_path)
        return count

    @staticmethod
    def _chunk_text(
        text: str, max_chars: int = 800, overlap: int = 100
    ) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + max_chars
            chunks.append(text[start:end])
            start += max_chars - overlap
        return chunks

    # ── Internal ─────────────────────────────────────────

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self.initialize()
