"""
Memory Reader — Dual-layer retrieval + pyramid injection.

Retrieval:
  Layer 1 (Hash)  → O(1) entity-based deterministic lookup
  Layer 2 (Vector) → semantic similarity fuzzy matching
  Merge + Priority Rank → top-k selection

Injection (anti-hallucination pyramid):
  Level 1: summaries only        (default, lightest)
  Level 2: + key decisions       (medium)
  Level 3: + raw content excerpts (only when depth is needed)
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from my_agent_os.memory_layer.models import (
    InjectionContext,
    MemoryRecord,
    RetrievedMemory,
    utcnow,
)
from my_agent_os.memory_layer.store import MemoryStore

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent / "prompts" / "memory_prompts.yaml"

LLMFunc = Callable[[str, str, bool], Awaitable[str]]


class MemoryReader:
    """Reads and ranks memories, then builds layered injection context."""

    def __init__(
        self,
        store: MemoryStore,
        llm: LLMFunc,
        top_k: int = 5,
        decay_days: float = 7.0,
        max_injection_chars: int = 2000,
    ):
        self._store = store
        self._llm = llm
        self._top_k = top_k
        self._decay_days = decay_days
        self._max_chars = max_injection_chars
        self._prompts = yaml.safe_load(
            open(_PROMPTS_PATH, "r", encoding="utf-8").read()
        )

    # ── Public API ───────────────────────────────────────

    async def retrieve(
        self, query: str, user_id: str = "default"
    ) -> InjectionContext:
        """
        Full read pipeline:
          extract entities → hash lookup → FTS search → merge → rank → inject
        """
        entities = await self._extract_entities(query)

        hash_ids = await self._store.lookup_by_entities(entities) if entities else []

        fts_hits = await self._store.fulltext_search(
            query, top_k=self._top_k * 2, user_id=user_id
        )

        merged = await self._merge_and_rank(hash_ids, fts_hits, user_id)
        top = merged[: self._top_k]

        for rm in top:
            await self._store.touch_memory(rm.record.id)

        return self._build_injection(top)

    # ── Entity Extraction (Read Path) ────────────────────

    async def _extract_entities(self, query: str) -> list[str]:
        """Use LLM to extract key entities from a query for hash lookup."""
        try:
            prompt = self._prompts["entity_extraction"].replace("{query}", query)
            raw = await self._llm("You are an entity extraction engine.", prompt, True)
            data = self._parse_json(raw)
            return data.get("entities", [])
        except Exception as e:
            logger.warning("Entity extraction failed, using fallback: %s", e)
            return self._fallback_entities(query)

    @staticmethod
    def _fallback_entities(query: str) -> list[str]:
        """Rule-based fallback for entity extraction."""
        import re
        words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{2,}", query)
        stopwords = {
            "the", "what", "how", "when", "where", "which", "about",
            "什么", "怎么", "如何", "哪个", "关于", "还有", "可以",
        }
        return [w.lower() for w in words if w.lower() not in stopwords][:10]

    # ── Merge & Rank ─────────────────────────────────────

    async def _merge_and_rank(
        self,
        hash_ids: list[str],
        vector_hits: list[tuple[str, float]],
        user_id: str,
    ) -> list[RetrievedMemory]:
        scored: dict[str, RetrievedMemory] = {}

        for mid in hash_ids:
            record = await self._store.get_memory(mid)
            if record and record.user_id == user_id:
                scored[mid] = RetrievedMemory(
                    record=record,
                    relevance_score=0.0,
                    source="hash",
                )

        for mid, rank in vector_hits:
            similarity = max(0.0, 1.0 / (1.0 + abs(rank)))
            if mid in scored:
                scored[mid].relevance_score = similarity
                scored[mid].source = "both"
            else:
                record = await self._store.get_memory(mid)
                if record and record.user_id == user_id:
                    scored[mid] = RetrievedMemory(
                        record=record,
                        relevance_score=similarity,
                        source="vector",
                    )

        now = utcnow()
        for rm in scored.values():
            rm.relevance_score = self._compute_priority(rm, now)

        ranked = sorted(scored.values(), key=lambda x: x.relevance_score, reverse=True)
        return ranked

    def _compute_priority(self, rm: RetrievedMemory, now: datetime) -> float:
        r = rm.record

        age_hours = max(0, (now - r.updated_at).total_seconds() / 3600)
        half_life_hours = self._decay_days * 24
        recency = math.exp(-0.693 * age_hours / half_life_hours)

        frequency = math.log1p(r.access_count) / 10.0

        decision_boost = 0.15 if r.key_points else 0.0

        source_boost = 0.2 if rm.source == "both" else (0.1 if rm.source == "hash" else 0.0)

        base_relevance = rm.relevance_score

        return (
            0.30 * base_relevance
            + 0.30 * recency
            + 0.15 * frequency
            + 0.10 * decision_boost
            + 0.10 * source_boost
            + 0.05 * r.priority
        )

    # ── Pyramid Injection Builder ────────────────────────

    def _build_injection(self, memories: list[RetrievedMemory]) -> InjectionContext:
        if not memories:
            return InjectionContext()

        summary_parts: list[str] = []
        decision_parts: list[str] = []
        detail_parts: list[str] = []
        source_ids: list[str] = []
        total_chars = 0

        for rm in memories:
            r = rm.record
            source_ids.append(r.id)

            s = r.summary or r.content[:150]
            summary_parts.append(f"- [{r.memory_type.value}] {s}")
            total_chars += len(s)

            if r.key_points and total_chars < self._max_chars:
                for kp in r.key_points:
                    decision_parts.append(f"  * {kp}")
                    total_chars += len(kp)

            if total_chars < self._max_chars * 1.5:
                excerpt = r.content[:300]
                if len(r.content) > 300:
                    excerpt += "..."
                detail_parts.append(f"  [{r.id[:8]}] {excerpt}")
                total_chars += len(excerpt)

        return InjectionContext(
            summary_layer="\n".join(summary_parts),
            decision_layer="\n".join(decision_parts),
            detail_layer="\n".join(detail_parts),
            source_ids=source_ids,
            token_estimate=total_chars // 4,
        )

    # ── Helpers ──────────────────────────────────────────

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
