#!/usr/bin/env python3
"""
Run memory consolidation maintenance (episodic -> semantic).

Usage:
  python scripts/memory_maintenance.py --user-id default
"""

from __future__ import annotations

import argparse
import asyncio

from my_agent_os.agent_core.llm_client import call_llm
from my_agent_os.config.settings import settings
from my_agent_os.memory_layer.engine import MemoryEngine


async def _run(user_id: str, lookback_days: int, max_items: int) -> None:
    engine = MemoryEngine(
        db_path=settings.MEMORY_DB_PATH,
        llm=call_llm,
        top_k=settings.MEMORY_RETRIEVAL_TOP_K,
        decay_days=settings.MEMORY_PRIORITY_DECAY_DAYS,
        max_injection_chars=settings.MEMORY_MAX_INJECTION_CHARS,
    )
    await engine.initialize()
    try:
        out = await engine.run_maintenance(
            user_id=user_id,
            lookback_days=lookback_days,
            max_items=max_items,
        )
        print(out)
    finally:
        await engine.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Consolidate episodic memories and prune fragments")
    p.add_argument("--user-id", default="default")
    p.add_argument("--lookback-days", type=int, default=7)
    p.add_argument("--max-items", type=int, default=30)
    args = p.parse_args()
    asyncio.run(_run(args.user_id, args.lookback_days, args.max_items))


if __name__ == "__main__":
    main()
