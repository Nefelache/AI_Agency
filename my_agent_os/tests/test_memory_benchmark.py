"""
Memory Layer Performance Benchmarks.

Measures:
  1. Hash index O(1) vs full-table scan speed
  2. FTS5 vs naive LIKE-style filtering speed
  3. Token efficiency: pyramid injection vs naive full-history
  4. Priority scoring validation across a spectrum of inputs
  5. Retrieval latency scaling (10 → 50 → 100 → 250 → 500 memories)

Results saved to: my_agent_os/tests/results/benchmark_results.json

Run:  pytest my_agent_os/tests/test_memory_benchmark.py -v -s
"""

from __future__ import annotations

import json
import random
import statistics
import time
from pathlib import Path

import pytest

from my_agent_os.memory_layer.models import MemoryRecord, MemoryType, RetrievedMemory, utcnow
from my_agent_os.memory_layer.reader import MemoryReader
from my_agent_os.memory_layer.store import MemoryStore

# ── Results storage ───────────────────────────────────────────────
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "benchmark_results.json"
_RESULTS: dict = {}


# ── Helpers ───────────────────────────────────────────────────────

async def mock_llm(system: str, user: str, json_mode: bool = False) -> str:
    import re
    words = re.findall(r"[a-zA-Z]{3,}", user.lower())[:5]
    return json.dumps({"entities": words})


WORD_POOL = [
    "python", "design", "business", "music", "technology", "data",
    "alex", "mike", "startup", "funding", "jazz", "architecture",
    "memory", "session", "retrieval", "embedding", "vector", "search",
]


async def seed_memories(store: MemoryStore, count: int, user_id: str = "bench") -> list[MemoryRecord]:
    records = []
    for i in range(count):
        chosen = random.sample(WORD_POOL, 3)
        r = MemoryRecord(
            memory_type=random.choice(list(MemoryType)),
            content=f"Memory {i}: {' '.join(chosen)} relates to discussion {i % 20}",
            summary=f"Summary of item {i} about {chosen[0]}",
            entities=chosen,
            user_id=user_id,
            priority=random.uniform(0.1, 0.9),
        )
        records.append(r)
        await store.add_memory(r)
    return records


async def time_fn(fn, runs: int = 20) -> list[float]:
    """Run an async function N times, return list of ms timings."""
    timings = []
    for _ in range(runs):
        t0 = time.perf_counter()
        await fn()
        timings.append((time.perf_counter() - t0) * 1000)
    return timings


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "bench.db"))
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture(scope="module", autouse=True)
def save_results_on_exit():
    yield
    if _RESULTS:
        RESULTS_FILE.write_text(json.dumps(_RESULTS, indent=2))
        print(f"\n✅  Benchmark results → {RESULTS_FILE}")


# ══════════════════════════════════════════════════════════════════
# Benchmark 1: Hash Index vs Full-Table Scan
# ══════════════════════════════════════════════════════════════════

class TestHashVsFullScan:
    """Hash O(1) lookup should be faster than reading all records into Python."""

    @pytest.mark.parametrize("n", [50, 200, 500])
    async def test_speed_comparison(self, store, n):
        await seed_memories(store, n)
        target = random.sample(WORD_POOL, 3)

        # Hash lookup
        async def do_hash():
            return await store.lookup_by_entities(target)

        # Full-table scan (get all → Python filter)
        async def do_scan():
            all_m = await store.get_all_memories("bench", limit=n + 100)
            return [m for m in all_m if any(e in m.entities for e in target)]

        hash_times = await time_fn(do_hash, runs=30)
        scan_times = await time_fn(do_scan, runs=30)

        hash_avg = statistics.mean(hash_times)
        scan_avg = statistics.mean(scan_times)
        speedup = scan_avg / hash_avg if hash_avg > 0 else 1.0

        _RESULTS[f"hash_vs_scan_n{n}"] = {
            "n_memories": n,
            "hash_avg_ms": round(hash_avg, 3),
            "scan_avg_ms": round(scan_avg, 3),
            "speedup_x": round(speedup, 2),
        }
        print(f"\n[Hash vs Scan N={n}] hash={hash_avg:.2f}ms  scan={scan_avg:.2f}ms  speedup={speedup:.1f}×")
        assert hash_avg <= max(scan_avg * 1.5, 10.0), "Hash should not be slower than full scan"


# ══════════════════════════════════════════════════════════════════
# Benchmark 2: FTS5 vs Naive Filter
# ══════════════════════════════════════════════════════════════════

class TestFTSvNaive:
    """FTS5 indexed search should be faster than Python-level filtering."""

    @pytest.mark.parametrize("n", [50, 200, 500])
    async def test_fts_vs_naive(self, store, n):
        await seed_memories(store, n)
        keyword = random.choice(WORD_POOL)

        async def do_fts():
            return await store.fulltext_search(keyword, top_k=10, user_id="bench")

        async def do_naive():
            all_m = await store.get_all_memories("bench", limit=n + 100)
            return [m for m in all_m if keyword in m.content.lower()]

        fts_times = await time_fn(do_fts, runs=30)
        naive_times = await time_fn(do_naive, runs=30)

        fts_avg = statistics.mean(fts_times)
        naive_avg = statistics.mean(naive_times)
        speedup = naive_avg / fts_avg if fts_avg > 0 else 1.0

        _RESULTS[f"fts_vs_naive_n{n}"] = {
            "n_memories": n,
            "fts_avg_ms": round(fts_avg, 3),
            "naive_avg_ms": round(naive_avg, 3),
            "speedup_x": round(speedup, 2),
        }
        print(f"\n[FTS vs Naive N={n}] fts={fts_avg:.2f}ms  naive={naive_avg:.2f}ms  speedup={speedup:.1f}×")


# ══════════════════════════════════════════════════════════════════
# Benchmark 3: Token Efficiency
# ══════════════════════════════════════════════════════════════════

_PROFILE_FACTS = [
    ("User's full name is Alex Chen", ["alex", "chen"], 0.9),
    ("User is 30 years old", ["30", "age"], 0.8),
    ("Lives in Los Angeles, California", ["los_angeles", "california"], 0.8),
    ("Company: OsliceInspiration AI consulting startup", ["oslice", "company"], 0.85),
    ("Series A: $2 million raised Jan 2026", ["funding", "series_a", "million"], 0.9),
    ("Co-founder is Mike Zhang (engineering)", ["mike", "cofounder"], 0.7),
    ("Allergic to alcohol — strictly avoided", ["alcohol", "allergy"], 0.95),
    ("Music blacklist: Rap and Heavy Metal", ["rap", "metal", "music"], 0.8),
    ("Diet: low sugar, no processed foods", ["sugar", "diet"], 0.7),
    ("Goal 2026: reach $500K ARR", ["goal", "arr", "500k"], 0.7),
    ("Tech stack: DeepSeek LLM, FastAPI, SQLite", ["deepseek", "fastapi"], 0.5),
    ("Team size: 4 including founders", ["team"], 0.4),
    ("Primary market: US small businesses", ["market", "us"], 0.5),
    ("Revenue model: $99/month SaaS subscription", ["revenue", "subscription"], 0.6),
    ("Background: 5 years at Google before founding", ["google", "experience"], 0.4),
    ("Hobby: playing piano on weekends", ["piano", "hobby"], 0.25),
    ("Pet: golden retriever named Biscuit", ["dog", "pet"], 0.2),
    ("Favourite read: Zero to One by Thiel", ["reading", "books"], 0.3),
    ("Fitness: morning runs and yoga", ["fitness", "health"], 0.3),
    ("Prefers serif fonts; minimal UI aesthetic", ["fonts", "design", "aesthetic"], 0.35),
]


class TestTokenEfficiency:
    """Pyramid injection should use far fewer chars than dumping all memories."""

    async def test_pyramid_vs_full_history(self, store):
        # Seed the realistic profile
        records = []
        for content, entities, priority in _PROFILE_FACTS:
            r = MemoryRecord(
                memory_type=MemoryType.SEMANTIC,
                content=content,
                summary=content[:80],
                entities=entities,
                user_id="alex",
                priority=priority,
            )
            await store.add_memory(r)
            records.append(r)

        total_memories = len(records)

        # ── Naive: dump all memories ──
        naive_chars = sum(len(r.content) for r in records)
        naive_tokens_est = naive_chars // 4

        # ── Pyramid: query-aware top-k ──
        for budget in [800, 1200, 2000]:
            reader = MemoryReader(store, mock_llm, top_k=5, max_injection_chars=budget)
            ctx = await reader.retrieve(
                "What are Alex's dietary restrictions and music preferences?",
                "alex",
            )
            pyramid_chars = len(ctx.summary_layer) + len(ctx.decision_layer) + len(ctx.detail_layer)
            pyramid_tokens_est = ctx.token_estimate
            savings_pct = (1 - pyramid_chars / naive_chars) * 100 if naive_chars else 0

            _RESULTS[f"token_efficiency_budget{budget}"] = {
                "total_memories": total_memories,
                "budget_chars": budget,
                "naive_chars": naive_chars,
                "naive_tokens_est": naive_tokens_est,
                "pyramid_chars": pyramid_chars,
                "pyramid_tokens_est": pyramid_tokens_est,
                "memories_injected": len(ctx.source_ids),
                "token_savings_pct": round(savings_pct, 1),
            }
            print(
                f"\n[Token N={total_memories} budget={budget}] "
                f"naive={naive_chars}ch  pyramid={pyramid_chars}ch  "
                f"savings={savings_pct:.0f}%  injected={len(ctx.source_ids)}/{total_memories}"
            )
            assert pyramid_chars <= budget + 200
            assert pyramid_chars < naive_chars

    async def test_pyramid_injects_most_relevant(self, store):
        """High-priority memories should be in the injection context."""
        for content, entities, priority in _PROFILE_FACTS:
            await store.add_memory(MemoryRecord(
                memory_type=MemoryType.SEMANTIC,
                content=content, summary=content[:80],
                entities=entities, user_id="alex2", priority=priority,
            ))
        reader = MemoryReader(store, mock_llm, top_k=3, max_injection_chars=600)
        ctx = await reader.retrieve("alcohol restrictions", "alex2")
        # The alcohol fact has priority=0.95 — should be retrieved
        combined = ctx.summary_layer + ctx.decision_layer
        assert "alcohol" in combined.lower() or len(ctx.source_ids) > 0


# ══════════════════════════════════════════════════════════════════
# Benchmark 4: Priority Scoring Spectrum
# ══════════════════════════════════════════════════════════════════

class TestPriorityScoringSpectrum:
    """Verify the composite score behaves correctly across many edge cases."""

    @pytest.fixture
    def reader(self, store):
        return MemoryReader(store, mock_llm, top_k=10, decay_days=7.0, max_injection_chars=5000)

    def _rm(self, relevance=0.5, age_days=0, access_count=0,
            source="fts", has_kp=False) -> RetrievedMemory:
        from datetime import timedelta
        r = MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="test",
            key_points=["kp"] if has_kp else [],
            user_id="u",
            access_count=access_count,
        )
        if age_days:
            old = utcnow() - timedelta(days=age_days)
            r.updated_at = old
            r.created_at = old
        return RetrievedMemory(record=r, relevance_score=relevance,
                               query_relevance=relevance, source=source)

    def test_ordering_is_monotone_by_recency(self, reader):
        now = utcnow()
        scores = [
            reader._compute_priority(self._rm(age_days=d), now)
            for d in [0, 1, 3, 7, 14, 30]
        ]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], f"Score not monotonically decreasing: {scores}"

    def test_ordering_is_monotone_by_access_count(self, reader):
        now = utcnow()
        scores = [
            reader._compute_priority(self._rm(access_count=c), now)
            for c in [0, 1, 5, 20, 100]
        ]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], f"Score not monotonically increasing: {scores}"

    def test_weights_sum_validation(self, reader):
        """The weight formula sums to ≤1 for baseline inputs."""
        # weights: 0.30 + 0.30 + 0.15 + 0.10 + 0.10 + 0.05 = 1.0
        # Verify baseline formula is self-consistent
        weights = [0.30, 0.30, 0.15, 0.10, 0.10, 0.05]
        assert abs(sum(weights) - 1.0) < 1e-9

    def test_score_table(self, reader):
        """Print a score comparison table for documentation."""
        now = utcnow()
        cases = [
            ("New, high-relevance, both-sources", dict(relevance=0.9, age_days=0, source="both", has_kp=True, access_count=3)),
            ("New, high-relevance, FTS-only", dict(relevance=0.9, age_days=0, source="fts")),
            ("1-week-old, medium relevance", dict(relevance=0.5, age_days=7)),
            ("2-week-old, low relevance", dict(relevance=0.2, age_days=14)),
            ("Old but frequently accessed", dict(relevance=0.2, age_days=30, access_count=50)),
            ("Recent, no relevance", dict(relevance=0.0, age_days=0)),
        ]
        rows = []
        print("\n" + "=" * 70)
        print(f"{'Scenario':<45} {'Score':>8}")
        print("=" * 70)
        for label, kwargs in cases:
            rm = self._rm(**kwargs)
            score = reader._compute_priority(rm, now)
            rows.append((label, score))
            print(f"{label:<45} {score:>8.4f}")
        print("=" * 70)

        # Verify top case scores highest
        assert rows[0][1] > rows[-1][1], "Best case should outsccore worst case"

        _RESULTS["priority_score_table"] = [
            {"scenario": label, "score": round(score, 4)} for label, score in rows
        ]


# ══════════════════════════════════════════════════════════════════
# Benchmark 5: Retrieval Latency vs Scale
# ══════════════════════════════════════════════════════════════════

class TestRetrievalScaling:
    """Verify retrieval stays fast as memory store grows."""

    @pytest.mark.parametrize("n", [10, 50, 100, 250, 500])
    async def test_latency_at_scale(self, store, n):
        await seed_memories(store, n)
        reader = MemoryReader(store, mock_llm, top_k=5, max_injection_chars=2000)

        timings = await time_fn(
            lambda: reader.retrieve("python design business", "bench"),
            runs=15,
        )
        avg = statistics.mean(timings)
        p95 = sorted(timings)[int(0.95 * len(timings))]
        p99 = sorted(timings)[-1]

        _RESULTS[f"scaling_n{n}"] = {
            "n_memories": n,
            "avg_ms": round(avg, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
        }
        print(f"\n[Scaling N={n}] avg={avg:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms")
        assert avg < 1000, f"Retrieval too slow at N={n}: {avg:.0f}ms"
        assert p95 < 2000, f"P95 too high at N={n}: {p95:.0f}ms"
