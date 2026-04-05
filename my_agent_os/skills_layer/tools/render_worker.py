"""
Video Render Worker — consumes pending_render tasks from the memory queue,
calls the local A1111 API for image generation, then uses MoviePy to produce
a vertical short-video MP4.

Requires:
  - A1111 running locally with --api flag:
      python launch.py --api --listen
  - moviepy installed: pip install moviepy
  - Output directory writable (RENDER_OUTPUT_DIR, default ~/AgentOS/renders)

Status lifecycle (tracked via MemoryRecord.metadata):
  pending_render → processing → completed | failed
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.context import get_memory_engine
from my_agent_os.skills_layer.tools import register

logger = logging.getLogger(__name__)

_A1111_URL    = os.getenv("A1111_URL", "http://127.0.0.1:7860")
_OUTPUT_DIR   = Path(os.getenv("RENDER_OUTPUT_DIR", Path.home() / "AgentOS" / "renders"))
_STEPS        = int(os.getenv("RENDER_STEPS", "20"))
_USE_HW_ACCEL = os.getenv("RENDER_HW_ACCEL", "1") == "1"


# ── Pure-sync CPU work — must run in a thread pool, never in the event loop ──

def _render_video_sync(image_path: str, output_path: str, duration: int = 10) -> str:
    """Compose a vertical MP4 from a static image using MoviePy."""
    from moviepy.editor import ImageClip

    clip = ImageClip(image_path).set_duration(duration)
    codec = "h264_videotoolbox" if _USE_HW_ACCEL else "libx264"
    clip.write_videofile(output_path, fps=30, codec=codec, logger=None)
    clip.close()
    return output_path


# ── Skill ─────────────────────────────────────────────────────────────────────

@register
class VideoRenderWorkerSkill(Skill):
    name = "render_worker"
    description = (
        "Start the background render pipeline: consumes all 'pending_render' "
        "video tasks from memory, generates images via A1111, and compresses "
        "them into MP4 with MoviePy. No params required."
    )
    skill_instructions = """
When to use: user says start render, 开始渲染, process video queue after matrix_planner.
No required params. Requires A1111_URL and memory tasks in pending_render.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        engine = get_memory_engine()
        pending = await engine.get_tasks_by_status("owner", "pending_render")

        if not pending:
            return {
                "success": True,
                "output": "渲染队列为空，没有待处理任务。",
                "queued": 0,
            }

        asyncio.create_task(self._consume_queue(pending))

        return {
            "success": True,
            "output": (
                f"渲染流水线已启动！\n"
                f"队列中：{len(pending)} 个任务\n"
                f"输出目录：{_OUTPUT_DIR}\n"
                f"A1111：{_A1111_URL}"
            ),
            "queued": len(pending),
        }

    async def _consume_queue(self, records) -> None:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for record in records:
            await self._process_one(record)

    async def _process_one(self, record) -> None:
        store = get_memory_engine()._store

        # Claim task: pending_render → processing
        await store.update_memory(
            record.id,
            metadata={**record.metadata, "status": "processing"},
        )

        try:
            task_data = json.loads(record.content)
            a1111_prompt = task_data.get("a1111_prompt", "high quality photo, 8k")
            account_id   = record.metadata.get("account_id", record.id[:8])

            # ── Step 1: Generate image via A1111 (I/O-bound, stays in event loop) ──
            image_path = await self._generate_image(a1111_prompt, account_id, record.id)

            # ── Step 2: Render video via MoviePy (CPU-bound, pushed to thread pool) ──
            output_path = str(_OUTPUT_DIR / f"video_{record.id[:8]}_{account_id}.mp4")
            await asyncio.to_thread(_render_video_sync, image_path, output_path)

            # ── Step 3: Mark completed ──────────────────────────────────────────────
            now = datetime.now(timezone.utc)
            await store.update_memory(
                record.id,
                summary=record.summary + " ✓",
                priority=0.1,
                metadata={
                    **record.metadata,
                    "status": "completed",
                    "output_path": output_path,
                    "completed_at": now.isoformat(),
                },
            )
            logger.info("[RenderWorker] Completed: %s → %s", record.id[:8], output_path)

        except Exception as exc:
            logger.error("[RenderWorker] Failed task %s: %s", record.id[:8], exc)
            await store.update_memory(
                record.id,
                metadata={
                    **record.metadata,
                    "status": "pending_render",  # rollback for retry
                    "last_error": str(exc)[:200],
                },
            )

    async def _generate_image(
        self, prompt: str, account_id: str, record_id: str
    ) -> str:
        """Call A1111 txt2img API, decode base64, save PNG, return path."""
        import httpx

        payload = {
            "prompt": prompt,
            "negative_prompt": "ugly, blurry, bad anatomy, watermark, text",
            "steps": _STEPS,
            "width": 1080,
            "height": 1920,
            "cfg_scale": 7,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{_A1111_URL}/sdapi/v1/txt2img", json=payload)
            resp.raise_for_status()

        images = resp.json().get("images", [])
        if not images:
            raise ValueError("A1111 returned no images")

        img_path = _OUTPUT_DIR / f"img_{record_id[:8]}_{account_id}.png"
        img_path.write_bytes(base64.b64decode(images[0]))
        logger.info("[RenderWorker] Image saved: %s", img_path)
        return str(img_path)
