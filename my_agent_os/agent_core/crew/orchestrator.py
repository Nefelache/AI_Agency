"""
Crew Orchestrator — Multi-agent discussion with escalation.

Three-phase protocol:
  Phase 1: Parallel independent analysis (each department)
  Phase 2: Cross-review (each agent sees others' views, can rebut)
  Phase 3: Chief of Staff synthesizes into final recommendation
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROFILES_PATH = Path(__file__).parent / "profiles.yaml"

LLMFunc = Callable[[str, str, bool], Awaitable[str]]


class CrewResult(BaseModel):
    """Output of a multi-agent discussion."""
    recommendation: str
    department_views: dict[str, str] = Field(default_factory=dict)
    consensus_level: str = "majority"


class CrewOrchestrator:
    """Runs multi-agent discussion with parallel analysis and escalation."""

    def __init__(self, llm: LLMFunc, profiles_path: str | Path | None = None):
        self._llm = llm
        path = Path(profiles_path) if profiles_path else _PROFILES_PATH
        with open(path, "r", encoding="utf-8") as f:
            self._profiles: dict[str, dict] = yaml.safe_load(f)

    @property
    def department_names(self) -> list[str]:
        return [k for k in self._profiles if k != "chief_of_staff"]

    async def discuss(
        self,
        task: str,
        agents: list[str] | None = None,
    ) -> CrewResult:
        """
        Full three-phase discussion.
        agents: list of profile keys to include. Defaults to all departments.
        """
        selected = agents or self.department_names
        selected = [a for a in selected if a in self._profiles]
        if not selected:
            selected = self.department_names

        # Phase 1: parallel independent analysis
        phase1 = await self._parallel_analyze(task, selected)
        logger.info("Crew Phase 1 complete: %d departments", len(phase1))

        # Phase 2: cross-review
        phase2 = await self._cross_review(task, phase1, selected)
        logger.info("Crew Phase 2 complete: cross-review done")

        # Phase 3: chief of staff synthesis
        recommendation = await self._synthesize(task, phase2)
        logger.info("Crew Phase 3 complete: recommendation ready")

        consensus = self._assess_consensus(phase2)

        return CrewResult(
            recommendation=recommendation,
            department_views={k: v for k, v in phase2.items()},
            consensus_level=consensus,
        )

    # ── Phase 1: Independent Analysis ────────────────────

    async def _parallel_analyze(
        self, task: str, agents: list[str]
    ) -> dict[str, str]:
        async def _one(agent_key: str) -> tuple[str, str]:
            profile = self._profiles[agent_key]
            persona = profile["persona"]
            prompt = f"Analyze this task from your department's perspective:\n\n{task}"
            try:
                result = await self._llm(persona, prompt, False)
                return agent_key, result.strip()
            except Exception as e:
                logger.warning("Agent %s failed: %s", agent_key, e)
                return agent_key, f"[{agent_key} unavailable]"

        results = await asyncio.gather(*[_one(a) for a in agents])
        return dict(results)

    # ── Phase 2: Cross-Review ────────────────────────────

    async def _cross_review(
        self, task: str, phase1: dict[str, str], agents: list[str]
    ) -> dict[str, str]:
        views_summary = "\n\n".join(
            f"**{self._profiles[k].get('name', k)}:**\n{v}"
            for k, v in phase1.items()
        )

        async def _review(agent_key: str) -> tuple[str, str]:
            profile = self._profiles[agent_key]
            persona = profile["persona"]
            prompt = (
                f"Original task: {task}\n\n"
                f"Other departments' analyses:\n{views_summary}\n\n"
                "Based on these perspectives and your own expertise, "
                "provide your FINAL position. You may adjust, agree, or disagree. "
                "Be specific and concise (3-5 sentences)."
            )
            try:
                result = await self._llm(persona, prompt, False)
                return agent_key, result.strip()
            except Exception as e:
                logger.warning("Cross-review failed for %s: %s", agent_key, e)
                return agent_key, phase1.get(agent_key, "[unavailable]")

        results = await asyncio.gather(*[_review(a) for a in agents])
        return dict(results)

    # ── Phase 3: Chief of Staff Synthesis ────────────────

    async def _synthesize(
        self, task: str, final_views: dict[str, str]
    ) -> str:
        cos_profile = self._profiles.get("chief_of_staff", {})
        persona = cos_profile.get("persona", "Synthesize all inputs into a recommendation.")

        views_text = "\n\n".join(
            f"**{self._profiles.get(k, {}).get('name', k)} ({self._profiles.get(k, {}).get('department', 'N/A')}):**\n{v}"
            for k, v in final_views.items()
        )

        prompt = (
            f"Task: {task}\n\n"
            f"Department analyses (after cross-review):\n{views_text}\n\n"
            "Synthesize into your final recommendation."
        )

        try:
            return (await self._llm(persona, prompt, False)).strip()
        except Exception as e:
            logger.error("Chief of Staff synthesis failed: %s", e)
            return "Unable to synthesize at this time. Individual department views are available."

    # ── Consensus Assessment ─────────────────────────────

    @staticmethod
    def _assess_consensus(views: dict[str, str]) -> str:
        """Simple heuristic for consensus level."""
        if len(views) <= 1:
            return "single"
        texts = list(views.values())
        positive = sum(1 for t in texts if any(w in t.lower() for w in ["agree", "recommend", "support", "approve", "proceed"]))
        ratio = positive / len(texts) if texts else 0
        if ratio >= 0.8:
            return "strong_consensus"
        if ratio >= 0.5:
            return "majority"
        return "divided"
