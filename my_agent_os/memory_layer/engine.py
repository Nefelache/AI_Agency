"""
Memory Engine — The unified façade for the entire memory subsystem.

All external code (router_engine, API routes) talks ONLY to this class.
Internal components (store, writer, reader, session) are never exposed.

层级:
  L4  SQLite + Hash-trick 向量 → 语义检索（跨会话模糊召回）
  L3  MEMORY.md Markdown      → 长效偏好/决策（跨重启持久化）
  L2  SQLite 结构化记忆        → 事实、事件、模式
  L1  JSONL 会话缓存           → 短期对话上下文窗口 + 压缩

Lifecycle:
  engine = MemoryEngine(settings)
  await engine.initialize()
  ...
  await engine.close()
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
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
    统一记忆操作入口 (L1–L4 全层)。

    Usage:
        engine = MemoryEngine(db_path, llm_func)
        await engine.initialize()

        # LLM 调用前 — 检索相关记忆（L2 FTS + L4 向量双路）
        context = await engine.retrieve(user_id, query)

        # LLM 调用后 — 后台处理本轮对话
        engine.process_turn_background(user_id, user_msg, assistant_msg)
    """

    def __init__(
        self,
        db_path: str,
        llm: LLMFunc,
        top_k: int = 5,
        decay_days: float = 7.0,
        max_injection_chars: int = 2000,
        memory_md_path: Path | None = None,
        sessions_dir: Path | None = None,
        context_window_tokens: int = 8192,
        compaction_threshold: float = 0.80,
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

        # L3 MEMORY.md
        self._markdown_store = None
        if memory_md_path:
            try:
                from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore
                self._markdown_store = MarkdownMemoryStore(memory_md_path)
                logger.info("L3 MEMORY.md 已启用: %s", memory_md_path)
            except Exception as exc:
                logger.warning("L3 MarkdownMemoryStore 初始化失败: %s", exc)

        # L1 JSONL + 上下文压缩
        self._sessions_dir = sessions_dir
        self._compaction_guard = None
        if sessions_dir:
            try:
                from my_agent_os.memory_layer.compaction import ContextWindowGuard
                self._compaction_guard = ContextWindowGuard(
                    max_tokens=context_window_tokens,
                    threshold=compaction_threshold,
                )
                sessions_dir.mkdir(parents=True, exist_ok=True)
                logger.info("L1 JSONL 压缩守卫已启用 (max=%d tokens)", context_window_tokens)
            except Exception as exc:
                logger.warning("ContextWindowGuard 初始化失败: %s", exc)

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
        检索相关记忆，在 LLM 生成回答前调用。
        双路检索: L2 FTS（精确）+ L4 语义向量（模糊）+ L3 MEMORY.md 注入。
        """
        await self._ensure_init()

        # L2 FTS + hash 双路检索（已有实现）
        ctx = await self._reader.retrieve(query, user_id)

        # L4 语义向量补充（取 Top-3 非重叠记忆）
        try:
            semantic_hits = await self._store.semantic_search(query, top_k=3, user_id=user_id)
            fts_ids = set()
            if ctx.summary_layer:
                pass  # summary_layer 已含 FTS 结果
            extra_contents: list[str] = []
            for mid, score in semantic_hits:
                if score < 0.05:
                    continue
                if mid in fts_ids:
                    continue
                rec = await self._store.get_memory(mid)
                if rec and rec.content not in (ctx.summary_layer or ""):
                    extra_contents.append(f"[semantic:{score:.2f}] {rec.content[:200]}")
            if extra_contents:
                extra_block = "\n".join(extra_contents)
                ctx = InjectionContext(
                    summary_layer=(ctx.summary_layer or "") + "\n" + extra_block,
                    decision_layer=ctx.decision_layer,
                )
        except Exception as exc:
            logger.debug("L4 语义补充检索失败（非致命）: %s", exc)

        # L3 MEMORY.md 快照注入
        if self._markdown_store:
            try:
                md_snapshot = self._markdown_store.snapshot_for_prompt(max_chars=800)
                if md_snapshot:
                    ctx = InjectionContext(
                        summary_layer=(ctx.summary_layer or "") + "\n\n[L3 Long-term Memory]\n" + md_snapshot,
                        decision_layer=ctx.decision_layer,
                    )
            except Exception as exc:
                logger.debug("L3 MEMORY.md 快照失败（非致命）: %s", exc)

        return ctx

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

        # L4: 存储 embedding 供语义检索
        await self._index_embeddings_for_session(session.id)

        # L3: 高优先级事实写入 MEMORY.md
        if self._markdown_store:
            await self._maybe_upsert_to_markdown(user_id, user_msg, assistant_msg)

        # L1: JSONL 会话缓存 + 压缩
        if self._sessions_dir and self._compaction_guard:
            await self._update_jsonl_cache(user_id, user_msg, assistant_msg)

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

    async def semantic_search_memories(
        self, user_id: str, query: str, top_k: int = 10
    ) -> list[MemoryRecord]:
        """L4 语义向量搜索记忆（余弦相似度排序）。"""
        await self._ensure_init()
        hits = await self._store.semantic_search(query, top_k=top_k, user_id=user_id)
        records = []
        for mid, _score in hits:
            rec = await self._store.get_memory(mid)
            if rec:
                records.append(rec)
        return records

    def get_markdown_store(self):
        """返回 L3 MarkdownMemoryStore 实例（供 API 层直接读写 MEMORY.md）。"""
        return self._markdown_store

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

    async def get_tasks_by_status(
        self, user_id: str, status: str
    ) -> list[MemoryRecord]:
        """Return all memories whose metadata['status'] matches the given value."""
        await self._ensure_init()
        return await self._store.get_memories_by_metadata(user_id, "status", status)

    async def stats(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        return await self._store.stats(user_id)

    async def run_maintenance(
        self,
        user_id: str,
        lookback_days: int = 7,
        max_items: int = 30,
    ) -> dict[str, Any]:
        """
        Background memory maintenance:
          - consolidate recent episodic fragments into a semantic memory
          - prune low-signal episodic fragments after consolidation
        """
        await self._ensure_init()
        return await self._writer.consolidate_episodic_memories(
            user_id=user_id,
            lookback_days=lookback_days,
            max_items=max_items,
        )

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

    # ── L4 Embedding Indexing ─────────────────────────────

    async def _index_embeddings_for_session(self, session_id: str) -> None:
        """将本 session 最近的记忆内容更新到 L4 向量索引。"""
        try:
            records = await self._store.get_all_memories("default", limit=20)
            for rec in records:
                if rec.session_id == session_id:
                    text = rec.content
                    if rec.summary:
                        text = rec.summary + " " + text
                    await self._store.store_embedding(rec.id, text)
        except Exception as exc:
            logger.debug("L4 embedding 索引失败（非致命）: %s", exc)

    # ── L3 MEMORY.md 更新 ─────────────────────────────────

    async def _maybe_upsert_to_markdown(
        self, user_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        """提取高价值事实并追加到 MEMORY.md（轻量启发式判断）。"""
        if not self._markdown_store:
            return
        # 简单规则：若 assistant 回答包含明确的偏好/决策信号词，写入对应章节
        combined = assistant_msg.lower()
        fact = None
        section = "Important Facts"
        _decision_signals = ("决定", "批准", "已确认", "agreed", "approved", "confirmed")
        _pref_signals = ("偏好", "喜欢", "prefer", "always", "usually")
        if any(s in combined for s in _decision_signals):
            fact = assistant_msg[:200]
            section = "Key Decisions"
        elif any(s in combined for s in _pref_signals):
            fact = assistant_msg[:200]
            section = "Core Preferences"
        if fact:
            try:
                self._markdown_store.upsert_fact(fact, section=section)
            except Exception as exc:
                logger.debug("L3 MEMORY.md upsert 失败（非致命）: %s", exc)

    # ── L1 JSONL 缓存 ─────────────────────────────────────

    async def _update_jsonl_cache(
        self, user_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        """追加本轮对话到 JSONL 缓存，并在超出 Token 预算时自动压缩。"""
        if not self._sessions_dir or not self._compaction_guard:
            return
        try:
            from my_agent_os.memory_layer.compaction import JsonlSessionCache
            cache = JsonlSessionCache(self._sessions_dir, user_id)
            cache.append_turn("user", user_msg)
            cache.append_turn("assistant", assistant_msg)
            cache.compact_if_needed(self._compaction_guard)
        except Exception as exc:
            logger.debug("L1 JSONL 缓存更新失败（非致命）: %s", exc)
