"""
Matrix Content Planner — generates differentiated short-video scripts for N accounts
based on a single core topic, then queues each as a memory task for the render worker.

Flow:
  1. Receive topic + optional account count
  2. Fire-and-forget background coroutine (never blocks the response)
  3. Concurrently ask LLM to write one script per account (response_json=True)
  4. Validate each output with Pydantic
  5. Write each script as a PROCEDURAL MemoryRecord with metadata status=pending_render
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from my_agent_os.agent_core.llm_client import call_llm
from my_agent_os.memory_layer.models import MemoryRecord, MemoryStatus, MemoryType
from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.context import get_memory_engine
from my_agent_os.skills_layer.tools import register

_DEFAULT_ACCOUNTS = [
    {"id": "acc_01", "persona": "极简干货", "style": "clean fit, minimalist white, 8k sharp"},
    {"id": "acc_02", "persona": "情感毒舌", "style": "cyberpunk, dark neon, cinematic"},
    {"id": "acc_03", "persona": "知识科普", "style": "infographic, flat design, bright colors"},
    {"id": "acc_04", "persona": "生活Vlog", "style": "warm film grain, golden hour, candid"},
    {"id": "acc_05", "persona": "励志正能量", "style": "sunrise landscape, epic wide angle, HDR"},
    {"id": "acc_06", "persona": "职场干货", "style": "corporate modern, clean desk, soft lighting"},
    {"id": "acc_07", "persona": "潮流时尚", "style": "editorial fashion, high contrast, studio"},
    {"id": "acc_08", "persona": "搞笑段子", "style": "meme aesthetic, vibrant colors, exaggerated"},
]


class VideoScript(BaseModel):
    hook: str
    body: str
    cta: str
    a1111_prompt: str


@register
class MatrixContentPlannerSkill(Skill):
    name = "matrix_planner"
    description = (
        "Generate differentiated short-video scripts for 8 accounts from one core topic "
        "and queue them for rendering. Params: topic (str), count (int, default 8)."
    )

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        topic = params.get("topic", "").strip()
        if not topic:
            return {"success": False, "reason": "Missing 'topic'."}

        count = min(int(params.get("count", 8)), len(_DEFAULT_ACCOUNTS))
        accounts = _DEFAULT_ACCOUNTS[:count]
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"

        asyncio.create_task(self._plan_matrix_async(topic, accounts, batch_id))

        return {
            "success": True,
            "output": (
                f"矩阵裂变已推入后台队列！\n"
                f"话题：{topic}\n"
                f"账号数：{count}\n"
                f"批次号：{batch_id}\n"
                f"完成后将自动写入记忆层，发送 'start render' 触发渲染流水线。"
            ),
            "topic": topic,
            "batch_id": batch_id,
            "account_count": count,
        }

    async def _plan_matrix_async(
        self, topic: str, accounts: list[dict], batch_id: str
    ) -> None:
        tasks = [
            self._generate_single_script(topic, acc, batch_id)
            for acc in accounts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if r is True)
        import logging
        logging.getLogger(__name__).info(
            "[MatrixPlanner] batch=%s topic=%r %d/%d scripts queued",
            batch_id, topic, success, len(accounts),
        )

    async def _generate_single_script(
        self, topic: str, account: dict, batch_id: str
    ) -> bool:
        system = (
            "你是一个爆款短视频矩阵编导。"
            "严格按 JSON 格式输出，不添加任何解释文字。"
        )
        user = (
            f"核心痛点：{topic}\n"
            f"账号人设：{account['persona']}\n"
            f"视觉风格：{account['style']}\n\n"
            "输出字段：\n"
            "- hook: 前3秒抓人开场白（≤15字）\n"
            "- body: 核心信息5-7秒台词\n"
            "- cta: 结尾引导话术\n"
            "- a1111_prompt: SD出图英文提示词，必须包含视觉风格描述"
        )

        raw = await call_llm(
            system_message=system,
            user_message=user,
            response_json=True,
            temperature=1.0,
            max_tokens=600,
        )

        try:
            script = VideoScript(**json.loads(raw))
        except (ValidationError, json.JSONDecodeError, TypeError) as e:
            import logging
            logging.getLogger(__name__).warning(
                "[MatrixPlanner] parse failed for %s: %s", account["id"], e
            )
            return False

        engine = get_memory_engine()
        now = datetime.now(timezone.utc)
        record = MemoryRecord(
            memory_type=MemoryType.PROCEDURAL,
            content=json.dumps(script.model_dump(), ensure_ascii=False),
            summary=f"待渲染 | 账号:{account['id']} | {topic[:30]}",
            key_points=[f"account:{account['id']}", f"topic:{topic[:50]}"],
            entities=[f"batch:{batch_id}", f"account:{account['id']}"],
            priority=0.9,
            status=MemoryStatus.ACTIVE,
            user_id="owner",
            created_at=now,
            updated_at=now,
            metadata={
                "status": "pending_render",
                "batch_id": batch_id,
                "account_id": account["id"],
                "persona": account["persona"],
                "style": account["style"],
                "topic": topic,
            },
        )
        await engine._store.add_memory(record)
        return True
