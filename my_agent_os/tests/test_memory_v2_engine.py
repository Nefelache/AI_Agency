from __future__ import annotations

import json

import pytest

from my_agent_os.config.settings import settings
from my_agent_os.memory_layer.engine import MemoryEngine


async def _fake_llm(system: str, user: str, response_json: bool = False) -> str:
    sys_low = (system or "").lower()
    if "wing classifier" in sys_low:
        return json.dumps({"wing": "product", "confidence": 0.88})
    if "distill" in (user or "").lower() or "distill" in sys_low:
        return json.dumps(
            {
                "summary": "User discussed marketplace optimization and next iteration.",
                "facts": ["Marketplace listing quality affects conversion."],
                "decisions": ["Prioritize product refinement first."],
                "tasks": ["Review listing copy and pricing experiments."],
                "risks": ["Execution drift without milestone check-ins."],
            }
        )
    return json.dumps({"answer": "ok"})


@pytest.mark.asyncio
async def test_v2_classification_uses_llm_when_rules_miss(tmp_path):
    db = tmp_path / "memory_v2.db"
    settings.MEMORY_V2_DB_PATH = str(db)
    settings.MEMORY_V2_ENABLED = True
    settings.MEMORY_V2_MAINTENANCE_INTERVAL_SECONDS = 1200

    engine = MemoryEngine(db_path=str(db), llm=_fake_llm)
    await engine.initialize()
    try:
        await engine.process_turn(
            user_id="u1",
            user_msg="I keep thinking about improving market fit and interaction flow.",
            assistant_msg="Let's capture product opportunities and tighten iteration loop.",
        )
        rows = await engine._palace.list_recent_drawers("u1", limit=5)
        assert rows
        # At least one newly ingested drawer should be tagged by llm classifier.
        assert any(r.get("wing") == "product" and r.get("classifier_source") == "llm" for r in rows)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_v2_maintenance_distills_and_prunes(tmp_path):
    db = tmp_path / "memory_v2_maint.db"
    settings.MEMORY_V2_DB_PATH = str(db)
    settings.MEMORY_V2_ENABLED = True
    settings.MEMORY_V2_DISTILL_WINDOW_MINUTES = 60
    settings.MEMORY_V2_MAX_RAW_PER_ROOM = 2

    engine = MemoryEngine(db_path=str(db), llm=_fake_llm)
    await engine.initialize()
    try:
        repeated = "Marketplace listing quality impacts conversion and should be iterated weekly."
        for _ in range(5):
            await engine.process_turn(
                user_id="u2",
                user_msg=repeated,
                assistant_msg="Noted. We should prioritize product iteration and pricing tests.",
            )

        out = await engine.run_maintenance(user_id="u2", lookback_days=7, max_items=2)
        assert out["distilled_created"] >= 1
        assert out["pruned"] >= 1

        rows = await engine._palace.list_recent_drawers("u2", limit=50)
        assert any(r.get("kind") == "distilled" for r in rows)
    finally:
        await engine.close()
