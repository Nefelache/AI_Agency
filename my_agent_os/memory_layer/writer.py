"""
Memory Writer — Extraction + Consolidation pipeline.

Phase 1 (Extraction):
  Conversation turns → LLM → facts / events / patterns / entities

Phase 2 (Consolidation):
  Each candidate memory → compare with existing → ADD / UPDATE / DELETE / NOOP

Phase 3 (Seal):
  When a topic concludes → summarize session → create episodic record
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

import yaml

from my_agent_os.memory_layer.models import (
    ConsolidationDecision,
    ConsolidationOp,
    ExtractionResult,
    MemoryRecord,
    MemoryType,
    utcnow,
)
from my_agent_os.memory_layer.store import MemoryStore

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent / "prompts" / "memory_prompts.yaml"

LLMFunc = Callable[[str, str, bool], Awaitable[str]]


def _load_prompts() -> dict:
    with open(_PROMPTS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class MemoryWriter:
    """Writes memories through an LLM-powered extraction + consolidation pipeline."""

    def __init__(self, store: MemoryStore, llm: LLMFunc):
        self._store = store
        self._llm = llm
        self._prompts = _load_prompts()

    # ── Public API ───────────────────────────────────────

    async def process_turn(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        user_id: str = "default",
    ) -> ExtractionResult:
        """
        Full write pipeline for one conversation turn.
        Returns the extraction result (including should_seal signal).
        """
        conversation = f"User: {user_msg}\nAssistant: {assistant_msg}"
        extraction = await self._extract(conversation)

        await self._consolidate_batch(extraction.facts, MemoryType.SEMANTIC, session_id, user_id)
        await self._consolidate_batch(extraction.events, MemoryType.EPISODIC, session_id, user_id)
        await self._consolidate_batch(extraction.patterns, MemoryType.PROCEDURAL, session_id, user_id)

        return extraction

    async def seal_session(
        self,
        session_id: str,
        user_id: str = "default",
    ) -> dict[str, Any]:
        """
        Seal a completed session: summarize → create episodic record → update hash index.
        Returns {summary, key_decisions, topic}.
        """
        turns = await self._store.get_turns(session_id)
        if not turns:
            return {"summary": "", "key_decisions": [], "topic": "general"}

        turns_text = "\n".join(f"{t.role.capitalize()}: {t.content}" for t in turns)
        seal_data = await self._summarize(turns_text)

        record = MemoryRecord(
            memory_type=MemoryType.EPISODIC,
            content=turns_text[:2000],
            summary=seal_data.get("summary", ""),
            key_points=seal_data.get("key_decisions", []),
            entities=self._extract_entities_simple(seal_data.get("summary", "")),
            priority=0.7,
            session_id=session_id,
            user_id=user_id,
        )
        await self._store.add_memory(record)

        topic = seal_data.get("topic", "general")
        await self._store.seal_session(
            session_id,
            summary=seal_data.get("summary", ""),
            topic=topic,
        )

        return seal_data

    # ── Extraction ───────────────────────────────────────

    async def _extract(self, conversation: str) -> ExtractionResult:
        prompt = self._prompts["extraction"].replace("{conversation}", conversation)
        try:
            raw = await self._llm("You are a memory extraction engine.", prompt, True)
            data = self._parse_json(raw)
            return ExtractionResult(**data)
        except Exception as e:
            logger.warning("Extraction failed: %s", e)
            return ExtractionResult()

    # ── Consolidation ────────────────────────────────────

    async def _consolidate_batch(
        self,
        candidates: list[dict[str, Any]],
        memory_type: MemoryType,
        session_id: str,
        user_id: str,
    ) -> None:
        for candidate in candidates:
            content = candidate.get("content", "")
            if not content:
                continue
            try:
                await self._consolidate_one(content, memory_type, session_id, user_id)
            except Exception as e:
                logger.warning("Consolidation failed for '%s': %s", content[:50], e)

    async def _consolidate_one(
        self,
        content: str,
        memory_type: MemoryType,
        session_id: str,
        user_id: str,
    ) -> None:
        existing = await self._store.get_memories_by_type(user_id, memory_type, limit=20)

        if not existing:
            record = MemoryRecord(
                memory_type=memory_type,
                content=content,
                entities=self._extract_entities_simple(content),
                session_id=session_id,
                user_id=user_id,
            )
            await self._store.add_memory(record)
            return

        existing_text = "\n".join(
            f"[id={m.id}] {m.content}" for m in existing[:10]
        )
        decision = await self._decide_consolidation(content, existing_text)

        if decision.operation == ConsolidationOp.ADD:
            record = MemoryRecord(
                memory_type=memory_type,
                content=decision.content or content,
                entities=self._extract_entities_simple(decision.content or content),
                session_id=session_id,
                user_id=user_id,
            )
            await self._store.add_memory(record)

        elif decision.operation == ConsolidationOp.UPDATE and decision.memory_id:
            new_content = decision.content or content
            await self._store.update_memory(
                decision.memory_id,
                content=new_content,
                entities=json.dumps(
                    self._extract_entities_simple(new_content), ensure_ascii=False
                ),
            )

        elif decision.operation == ConsolidationOp.DELETE and decision.memory_id:
            await self._store.delete_memory(decision.memory_id)

    async def _decide_consolidation(
        self, candidate: str, existing: str
    ) -> ConsolidationDecision:
        prompt = (
            self._prompts["consolidation"]
            .replace("{candidate}", candidate)
            .replace("{existing}", existing)
        )
        try:
            raw = await self._llm("You are a memory consolidation engine.", prompt, True)
            data = self._parse_json(raw)
            return ConsolidationDecision(**data)
        except Exception as e:
            logger.warning("Consolidation decision failed: %s", e)
            return ConsolidationDecision(operation=ConsolidationOp.ADD, reason="fallback")

    # ── Summarization ────────────────────────────────────

    async def _summarize(self, turns_text: str) -> dict[str, Any]:
        prompt = self._prompts["summarization"].replace("{turns}", turns_text[:3000])
        try:
            raw = await self._llm("You are a summarization engine.", prompt, True)
            return self._parse_json(raw)
        except Exception as e:
            logger.warning("Summarization failed: %s", e)
            return {"summary": turns_text[:200], "key_decisions": [], "topic": "general"}

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_entities_simple(text: str) -> list[str]:
        """Fast, rule-based entity extraction for hash indexing during writes."""
        import re
        words = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9_]+", text)
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "shall", "can", "this",
            "that", "these", "those", "it", "its", "and", "or", "but",
            "with", "for", "from", "not", "null", "none", "true", "false",
            "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
            "和", "与", "或", "但", "而", "也", "都", "就", "会", "有",
        }
        entities = []
        seen = set()
        for w in words:
            low = w.lower()
            if len(low) < 2 or low in stopwords or low in seen:
                continue
            seen.add(low)
            entities.append(low)
        return entities[:20]

    @staticmethod
    def _parse_json(raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                return json.loads(cleaned[start : end + 1])
            raise
