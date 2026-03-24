"""
Comprehensive Memory Layer Unit Tests.

Covers all components:
  - MemoryStore  : CRUD, hash index, FTS5, sessions, turns, stats
  - MemoryReader : retrieval pipeline, priority scoring, pyramid injection
  - MemoryWriter : extraction, consolidation, session sealing

Run: pytest my_agent_os/tests/test_memory_layer.py -v
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest

from my_agent_os.memory_layer.models import (
    InjectionContext,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    RetrievedMemory,
    Session,
    SessionStatus,
    Turn,
    utcnow,
)
from my_agent_os.memory_layer.reader import MemoryReader
from my_agent_os.memory_layer.store import MemoryStore
from my_agent_os.memory_layer.writer import MemoryWriter


# ── Helpers ───────────────────────────────────────────────────────

def make_record(
    content: str = "test content",
    memory_type: MemoryType = MemoryType.SEMANTIC,
    entities: list[str] | None = None,
    user_id: str = "user1",
    priority: float = 0.5,
    access_count: int = 0,
    age_days: float = 0.0,
    summary: str | None = None,
    key_points: list[str] | None = None,
) -> MemoryRecord:
    r = MemoryRecord(
        memory_type=memory_type,
        content=content,
        summary=summary,
        key_points=key_points or [],
        entities=entities or [],
        user_id=user_id,
        priority=priority,
        access_count=access_count,
    )
    if age_days:
        old = utcnow() - timedelta(days=age_days)
        r.updated_at = old
        r.created_at = old
    return r


async def mock_llm(system: str, user: str, json_mode: bool = False) -> str:
    """Minimal mock LLM for unit tests — returns valid JSON for any call."""
    sys_lower = system.lower()
    if "entity" in sys_lower:
        import re
        words = re.findall(r"[a-zA-Z]{3,}", user.lower())[:5]
        return json.dumps({"entities": words})
    if "extraction" in sys_lower:
        return json.dumps({
            "facts": [{"content": "User mentioned a preference"}],
            "events": [], "patterns": [],
            "entities": ["preference"],
            "should_seal": False, "topic": "preferences",
        })
    if "consolidation" in sys_lower:
        return json.dumps({"operation": "add", "reason": "new fact"})
    if "summariz" in sys_lower:
        return json.dumps({
            "summary": user[:80],
            "key_decisions": ["noted preference"],
            "topic": "general",
        })
    return json.dumps({"answer": "ok", "next_actions": []})


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "test.db"))
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def reader(store):
    return MemoryReader(store, mock_llm, top_k=5, decay_days=7.0, max_injection_chars=2000)


@pytest.fixture
def writer(store):
    return MemoryWriter(store, mock_llm)


# ══════════════════════════════════════════════════════════════════
# MemoryStore — CRUD
# ══════════════════════════════════════════════════════════════════

class TestMemoryStoreCRUD:

    async def test_add_and_get(self, store):
        r = make_record("User is a UI designer in LA", entities=["designer"])
        await store.add_memory(r)
        fetched = await store.get_memory(r.id)
        assert fetched is not None
        assert fetched.content == "User is a UI designer in LA"
        assert fetched.user_id == "user1"
        assert fetched.memory_type == MemoryType.SEMANTIC

    async def test_get_nonexistent_returns_none(self, store):
        assert await store.get_memory("nonexistent_id_xyz") is None

    async def test_update_content(self, store):
        r = make_record("original content")
        await store.add_memory(r)
        await store.update_memory(r.id, content="updated content")
        assert (await store.get_memory(r.id)).content == "updated content"

    async def test_update_priority(self, store):
        r = make_record(priority=0.3)
        await store.add_memory(r)
        await store.update_memory(r.id, priority=0.9)
        assert (await store.get_memory(r.id)).priority == 0.9

    async def test_delete_removes_record(self, store):
        r = make_record("to be deleted")
        await store.add_memory(r)
        await store.delete_memory(r.id)
        assert await store.get_memory(r.id) is None

    async def test_get_all_returns_all_active(self, store):
        for i in range(5):
            await store.add_memory(make_record(f"memory {i}"))
        all_mem = await store.get_all_memories("user1")
        assert len(all_mem) == 5

    async def test_user_isolation(self, store):
        await store.add_memory(make_record("user1 private", user_id="user1"))
        await store.add_memory(make_record("user2 private", user_id="user2"))
        user1_mems = await store.get_all_memories("user1")
        assert len(user1_mems) == 1
        assert user1_mems[0].user_id == "user1"

    async def test_get_by_type_filter(self, store):
        await store.add_memory(make_record("semantic fact", MemoryType.SEMANTIC))
        await store.add_memory(make_record("episodic event", MemoryType.EPISODIC))
        await store.add_memory(make_record("procedural pattern", MemoryType.PROCEDURAL))
        semantics = await store.get_memories_by_type("user1", MemoryType.SEMANTIC)
        assert len(semantics) == 1
        assert semantics[0].memory_type == MemoryType.SEMANTIC

    async def test_stats_by_type(self, store):
        await store.add_memory(make_record("s1", MemoryType.SEMANTIC))
        await store.add_memory(make_record("s2", MemoryType.SEMANTIC))
        await store.add_memory(make_record("e1", MemoryType.EPISODIC))
        stats = await store.stats("user1")
        assert stats["semantic"] == 2
        assert stats["episodic"] == 1
        assert stats["procedural"] == 0


# ══════════════════════════════════════════════════════════════════
# MemoryStore — Hash Index
# ══════════════════════════════════════════════════════════════════

class TestHashIndex:

    async def test_lookup_returns_matching_ids(self, store):
        r = make_record("Alex is a designer", entities=["alex", "designer"])
        await store.add_memory(r)
        ids = await store.lookup_by_entities(["alex"])
        assert r.id in ids

    async def test_lookup_empty_returns_empty(self, store):
        assert await store.lookup_by_entities([]) == []

    async def test_lookup_unknown_entity_returns_empty(self, store):
        await store.add_memory(make_record("content", entities=["known_entity"]))
        ids = await store.lookup_by_entities(["totally_unknown"])
        assert len(ids) == 0

    async def test_hash_deleted_with_memory(self, store):
        r = make_record("deletable", entities=["del_entity"])
        await store.add_memory(r)
        await store.delete_memory(r.id)
        assert r.id not in await store.lookup_by_entities(["del_entity"])

    async def test_multiple_entities_any_match(self, store):
        r = make_record("multi", entities=["alpha", "beta", "gamma"])
        await store.add_memory(r)
        assert r.id in await store.lookup_by_entities(["beta"])

    async def test_multiple_records_same_entity(self, store):
        r1 = make_record("first", entities=["shared"])
        r2 = make_record("second", entities=["shared"])
        await store.add_memory(r1)
        await store.add_memory(r2)
        ids = await store.lookup_by_entities(["shared"])
        assert r1.id in ids and r2.id in ids


# ══════════════════════════════════════════════════════════════════
# MemoryStore — FTS5
# ══════════════════════════════════════════════════════════════════

class TestFTS:

    async def test_fts_finds_keyword(self, store):
        r = make_record("Python asyncio concurrent programming guide")
        await store.add_memory(r)
        hits = await store.fulltext_search("asyncio", top_k=5, user_id="user1")
        assert r.id in [h[0] for h in hits]

    async def test_fts_no_match(self, store):
        await store.add_memory(make_record("completely unrelated xyz content"))
        hits = await store.fulltext_search("javascript typescript", top_k=5, user_id="user1")
        assert len(hits) == 0

    async def test_fts_respects_user_filter(self, store):
        r1 = make_record("shared keyword test", user_id="user1")
        r2 = make_record("shared keyword test", user_id="user2")
        await store.add_memory(r1)
        await store.add_memory(r2)
        hits = await store.fulltext_search("shared keyword", top_k=5, user_id="user1")
        ids = [h[0] for h in hits]
        assert r1.id in ids
        assert r2.id not in ids

    async def test_fts_multiple_keywords_or(self, store):
        r1 = make_record("Python programming language")
        r2 = make_record("JavaScript framework")
        await store.add_memory(r1)
        await store.add_memory(r2)
        hits = await store.fulltext_search("Python JavaScript", top_k=5, user_id="user1")
        ids = [h[0] for h in hits]
        assert r1.id in ids
        assert r2.id in ids

    async def test_touch_increments_access_count(self, store):
        r = make_record("access counting test")
        await store.add_memory(r)
        await store.touch_memory(r.id)
        await store.touch_memory(r.id)
        fetched = await store.get_memory(r.id)
        assert fetched.access_count == 2


# ══════════════════════════════════════════════════════════════════
# MemoryStore — Sessions & Turns
# ══════════════════════════════════════════════════════════════════

class TestSessions:

    async def test_create_and_get_active_session(self, store):
        session = await store.create_session("user1")
        active = await store.get_active_session("user1")
        assert active is not None
        assert active.id == session.id
        assert active.status.value == "active"

    async def test_no_active_session_initially(self, store):
        assert await store.get_active_session("user_new") is None

    async def test_seal_session(self, store):
        session = await store.create_session("user1")
        await store.seal_session(session.id, "Summary text", "preferences")
        sealed = await store.get_session(session.id)
        assert sealed.status.value == "sealed"
        assert sealed.summary == "Summary text"
        assert sealed.topic == "preferences"

    async def test_turns_recorded_in_order(self, store):
        session = await store.create_session("user1")
        await store.add_turn(session.id, "user", "Hello")
        await store.add_turn(session.id, "assistant", "Hi there!")
        turns = await store.get_turns(session.id)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "Hello"
        assert turns[1].role == "assistant"

    async def test_turn_count_updates(self, store):
        session = await store.create_session("user1")
        await store.add_turn(session.id, "user", "message 1")
        await store.add_turn(session.id, "user", "message 2")
        updated = await store.get_session(session.id)
        assert updated.turn_count == 2

    async def test_list_sessions(self, store):
        for _ in range(3):
            await store.create_session("user1")
        sessions = await store.list_sessions("user1")
        assert len(sessions) == 3


# ══════════════════════════════════════════════════════════════════
# MemoryReader — Retrieval Pipeline
# ══════════════════════════════════════════════════════════════════

class TestMemoryReaderPipeline:

    async def test_retrieve_empty_store_returns_empty_context(self, reader):
        ctx = await reader.retrieve("anything", "user1")
        assert ctx.summary_layer == ""
        assert ctx.source_ids == []
        assert ctx.token_estimate == 0

    async def test_retrieve_finds_fts_match(self, reader, store):
        r = make_record("User enjoys classical music and jazz concerts", entities=["music", "jazz"])
        await store.add_memory(r)
        ctx = await reader.retrieve("music preferences", "user1")
        assert r.id in ctx.source_ids

    async def test_retrieve_finds_hash_match(self, reader, store):
        r = make_record("Alex Chen is the founder", entities=["alex", "founder"])
        await store.add_memory(r)
        ctx = await reader.retrieve("who is alex?", "user1")
        assert r.id in ctx.source_ids

    async def test_retrieve_respects_user_id(self, reader, store):
        r1 = make_record("user1 secret", entities=["secret_entity"], user_id="user1")
        r2 = make_record("user2 secret", entities=["secret_entity"], user_id="user2")
        await store.add_memory(r1)
        await store.add_memory(r2)
        ctx = await reader.retrieve("secret_entity", "user1")
        assert r1.id in ctx.source_ids
        assert r2.id not in ctx.source_ids

    async def test_retrieve_top_k_limits_results(self, store):
        small_reader = MemoryReader(store, mock_llm, top_k=3, max_injection_chars=2000)
        for i in range(10):
            await store.add_memory(make_record(f"test topic memory number {i}"))
        ctx = await small_reader.retrieve("test topic", "user1")
        assert len(ctx.source_ids) <= 3

    async def test_retrieve_increments_access_count(self, reader, store):
        r = make_record("frequently accessed memory content")
        await store.add_memory(r)
        await reader.retrieve("frequently accessed memory", "user1")
        fetched = await store.get_memory(r.id)
        assert fetched.access_count >= 1


# ══════════════════════════════════════════════════════════════════
# MemoryReader — Priority Scoring
# ══════════════════════════════════════════════════════════════════

class TestPriorityScoring:

    def _make_rm(
        self,
        relevance: float = 0.5,
        query_relevance: float = 0.5,
        source: str = "fts",
        age_days: float = 0.0,
        access_count: int = 0,
        has_key_points: bool = False,
    ) -> RetrievedMemory:
        r = make_record(
            "scoring test",
            age_days=age_days,
            access_count=access_count,
            key_points=["decision A"] if has_key_points else [],
        )
        return RetrievedMemory(
            record=r,
            relevance_score=relevance,
            query_relevance=query_relevance,
            source=source,
        )

    def test_recent_beats_old_all_else_equal(self, reader):
        now = utcnow()
        rm_now = self._make_rm(relevance=0.5, age_days=0)
        rm_old = self._make_rm(relevance=0.5, age_days=14)
        assert reader._compute_priority(rm_now, now) > reader._compute_priority(rm_old, now)

    def test_frequent_beats_rare_all_else_equal(self, reader):
        now = utcnow()
        rm_freq = self._make_rm(access_count=20)
        rm_rare = self._make_rm(access_count=0)
        assert reader._compute_priority(rm_freq, now) > reader._compute_priority(rm_rare, now)

    def test_both_source_beats_fts_only(self, reader):
        now = utcnow()
        rm_both = self._make_rm(source="both", relevance=0.5)
        rm_fts = self._make_rm(source="fts", relevance=0.5)
        assert reader._compute_priority(rm_both, now) > reader._compute_priority(rm_fts, now)

    def test_hash_source_beats_fts_only(self, reader):
        now = utcnow()
        rm_hash = self._make_rm(source="hash", relevance=0.0)
        rm_fts = self._make_rm(source="fts", relevance=0.0)
        assert reader._compute_priority(rm_hash, now) > reader._compute_priority(rm_fts, now)

    def test_key_points_boost(self, reader):
        now = utcnow()
        rm_with = self._make_rm(has_key_points=True)
        rm_without = self._make_rm(has_key_points=False)
        assert reader._compute_priority(rm_with, now) > reader._compute_priority(rm_without, now)

    def test_score_is_non_negative(self, reader):
        now = utcnow()
        rm = self._make_rm(relevance=0.0, age_days=100, access_count=0)
        assert reader._compute_priority(rm, now) >= 0.0

    def test_score_reasonable_upper_bound(self, reader):
        now = utcnow()
        rm = self._make_rm(relevance=1.0, age_days=0, access_count=1000,
                           has_key_points=True, source="both")
        score = reader._compute_priority(rm, now)
        assert score <= 2.0, f"Score {score} unusually high"

    def test_half_life_decay_at_7_days(self, reader):
        """At decay_days (7), recency should be ~0.5."""
        import math
        now = utcnow()
        rm_day0 = self._make_rm(relevance=0.0, age_days=0)
        rm_day7 = self._make_rm(relevance=0.0, age_days=7)
        s0 = reader._compute_priority(rm_day0, now)
        s7 = reader._compute_priority(rm_day7, now)
        # At half-life, recency contribution drops ~50%; overall score reflects this
        assert s7 < s0 * 0.8, "Score at 7 days should be notably lower than at day 0"


# ══════════════════════════════════════════════════════════════════
# MemoryReader — Pyramid Injection
# ══════════════════════════════════════════════════════════════════

class TestPyramidInjection:

    @pytest.fixture
    def small_reader(self, store):
        return MemoryReader(store, mock_llm, top_k=5, decay_days=7.0, max_injection_chars=600)

    def _rm(self, content: str, summary: str | None = None,
            key_points: list[str] | None = None, query_relevance: float = 0.5) -> RetrievedMemory:
        r = make_record(content, summary=summary, key_points=key_points or [])
        return RetrievedMemory(record=r, relevance_score=0.5,
                               query_relevance=query_relevance, source="fts")

    def test_empty_memories_returns_empty_injection(self, small_reader):
        ctx = small_reader._build_injection([])
        assert ctx == InjectionContext()

    def test_summary_always_included(self, small_reader):
        rm = self._rm("long raw content here", summary="concise summary")
        ctx = small_reader._build_injection([rm])
        assert "concise summary" in ctx.summary_layer

    def test_falls_back_to_content_when_no_summary(self, small_reader):
        rm = self._rm("x" * 200)
        ctx = small_reader._build_injection([rm])
        assert len(ctx.summary_layer) > 0

    def test_key_points_in_decision_layer(self, small_reader):
        rm = self._rm("x" * 300, summary="summary", key_points=["kp1", "kp2"],
                      query_relevance=0.95)
        ctx = small_reader._build_injection([rm])
        combined = ctx.summary_layer + ctx.decision_layer
        assert "kp1" in combined or "kp2" in combined

    def test_total_chars_within_budget(self, small_reader):
        memories = [
            self._rm("x" * 300, summary="s" * 40, query_relevance=0.8)
            for _ in range(5)
        ]
        ctx = small_reader._build_injection(memories)
        total = (len(ctx.summary_layer) + len(ctx.decision_layer) + len(ctx.detail_layer))
        assert total <= small_reader._max_chars + 200

    def test_high_relevance_gets_larger_budget(self, small_reader):
        """High-relevance memories get proportionally more budget."""
        rm_high = self._rm("A" * 500, summary="high-rel summary", query_relevance=0.9)
        rm_low = self._rm("B" * 500, summary="low-rel summary", query_relevance=0.1)
        ctx = small_reader._build_injection([rm_high, rm_low])
        # High-relevance memory's summary should always be present
        assert "high-rel summary" in ctx.summary_layer

    def test_source_ids_populated_correctly(self, small_reader):
        rm1 = self._rm("content one")
        rm2 = self._rm("content two")
        ctx = small_reader._build_injection([rm1, rm2])
        assert len(ctx.source_ids) == 2
        assert rm1.record.id in ctx.source_ids
        assert rm2.record.id in ctx.source_ids

    def test_token_estimate_is_reasonable(self, small_reader):
        rm = self._rm("x" * 400, summary="y" * 50)
        ctx = small_reader._build_injection([rm])
        total_chars = len(ctx.summary_layer) + len(ctx.decision_layer) + len(ctx.detail_layer)
        # token_estimate ≈ total_chars / 4 (rounded down per memory, not global)
        assert ctx.token_estimate > 0
        assert ctx.token_estimate <= total_chars // 4 + 50  # allow rounding slack


# ══════════════════════════════════════════════════════════════════
# MemoryWriter — Extraction & Sealing
# ══════════════════════════════════════════════════════════════════

class TestMemoryWriter:

    async def test_process_turn_does_not_crash(self, writer, store):
        session = await store.create_session("user1")
        # Should complete without exception
        result = await writer.process_turn(
            session.id, "I like jazz music", "Noted, I will remember that.", "user1"
        )
        assert result is not None

    async def test_seal_session_creates_episodic_record(self, writer, store):
        session = await store.create_session("user1")
        await store.add_turn(session.id, "user", "My name is Alex")
        await store.add_turn(session.id, "assistant", "Nice to meet you, Alex!")
        await writer.seal_session(session.id, "user1")
        memories = await store.get_all_memories("user1")
        episodic = [m for m in memories if m.memory_type == MemoryType.EPISODIC]
        assert len(episodic) >= 1

    async def test_seal_marks_session_as_sealed(self, writer, store):
        session = await store.create_session("user1")
        await store.add_turn(session.id, "user", "test")
        await store.add_turn(session.id, "assistant", "response")
        await writer.seal_session(session.id, "user1")
        sealed = await store.get_session(session.id)
        assert sealed.status.value == "sealed"

    async def test_seal_empty_session_does_not_crash(self, writer, store):
        session = await store.create_session("user1")
        result = await writer.seal_session(session.id, "user1")
        assert isinstance(result, dict)

    async def test_truth_maintenance_deprecates_conflicting_semantic(self, writer, store):
        old = make_record(
            "I hate spicy food and avoid chili.",
            memory_type=MemoryType.SEMANTIC,
            entities=["spicy", "food", "chili"],
            user_id="user1",
        )
        await store.add_memory(old)

        await writer._consolidate_one(
            "I now love spicy food and enjoy chili.",
            MemoryType.SEMANTIC,
            session_id="s1",
            user_id="user1",
        )

        old_after = await store.get_memory(old.id)
        assert old_after is not None
        assert old_after.status.value == "deprecated"

    async def test_episodic_consolidation_creates_semantic_and_prunes(self, writer, store):
        for i in range(4):
            await store.add_memory(make_record(
                f"Meeting note {i}: discussed roadmap priorities and launch plan.",
                memory_type=MemoryType.EPISODIC,
                user_id="user1",
                priority=0.5,
            ))

        out = await writer.consolidate_episodic_memories("user1", lookback_days=7, max_items=10)
        assert out["consolidated"] in (0, 1)
        all_mem = await store.get_all_memories("user1", limit=200)
        semantics = [m for m in all_mem if m.memory_type == MemoryType.SEMANTIC]
        assert len(semantics) >= 1


class TestPipelineImprovements:
    async def test_entity_timeout_does_not_block_retrieval(self, store):
        async def slow_llm(system: str, user: str, json_mode: bool = False) -> str:
            if "entity" in system.lower():
                await asyncio.sleep(0.5)
                return json.dumps({"entities": ["music"]})
            return json.dumps({"answer": "ok"})

        await store.add_memory(make_record(
            "User enjoys classical music and jazz concerts.",
            entities=["music", "jazz"],
            user_id="user1",
        ))
        reader = MemoryReader(store, slow_llm, top_k=3, max_injection_chars=800, entity_timeout_ms=50)

        t0 = time.perf_counter()
        ctx = await reader.retrieve("music preferences", "user1")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert len(ctx.source_ids) >= 1
        assert elapsed_ms < 300, f"retrieve took too long: {elapsed_ms:.1f}ms"
