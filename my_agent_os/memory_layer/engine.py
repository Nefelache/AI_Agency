"""Memory engine v2 (MemoryPalace style, embedding-first)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from collections import defaultdict
from typing import Any, Awaitable, Callable
import yaml

from my_agent_os.config.settings import settings
from my_agent_os.memory_layer.embedding_client import EmbeddingClient
from my_agent_os.memory_layer.models import InjectionContext, MemoryRecord, MemoryType, Session
from my_agent_os.memory_layer.palace_store import PalaceStore, WINGS

logger = logging.getLogger(__name__)

LLMFunc = Callable[[str, str, bool], Awaitable[str]]


class MemoryEngine:
    def __init__(
        self,
        db_path: str,
        llm: LLMFunc,
        top_k: int = 5,
        decay_days: float = 7.0,
        max_injection_chars: int = 2000,
    ):
        self._llm = llm
        self._top_k = top_k
        self._max_chars = max_injection_chars
        self._initialized = False
        self._enabled = bool(settings.MEMORY_V2_ENABLED)
        active_db = settings.MEMORY_V2_DB_PATH if self._enabled else db_path
        self._palace = PalaceStore(active_db)
        self._embed = EmbeddingClient()
        self._maintenance_last_run: dict[str, datetime] = {}
        self._maintenance_running: set[str] = set()
        self._maintenance_last_result: dict[str, dict[str, Any]] = {}
        self._prompts_cache: dict[str, Any] | None = None

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._palace.initialize()
        self._initialized = True
        logger.info("MemoryEngine v2 initialized.")

    async def close(self) -> None:
        await self._palace.close()
        self._initialized = False

    async def retrieve(self, user_id: str, query: str) -> InjectionContext:
        await self._ensure_init()
        if not query.strip():
            return InjectionContext()
        qvec = await self._embed.embed_text(query)
        hits = await self._palace.vector_search(user_id, qvec, query_text=query, top_k=self._top_k)

        baseline_id = (getattr(settings, "MEMORY_V2_BASELINE_USER_ID", "") or "").strip()
        baseline_hits: list[dict[str, Any]] = []
        if baseline_id and baseline_id != user_id:
            k = min(5, max(2, self._top_k))
            baseline_hits = await self._palace.vector_search(
                baseline_id, qvec, query_text=query, top_k=k
            )

        merged: list[tuple[bool, dict[str, Any]]] = []
        seen: set[str] = set()
        for h in hits:
            hid = str(h.get("id", ""))
            if hid and hid not in seen:
                seen.add(hid)
                merged.append((False, h))
        for h in baseline_hits:
            hid = str(h.get("id", ""))
            if hid and hid not in seen:
                seen.add(hid)
                merged.append((True, h))

        if not merged:
            return InjectionContext()

        max_total = max(self._top_k + 4, 8)
        merged = merged[:max_total]

        summary_parts: list[str] = []
        detail_parts: list[str] = []
        char_budget = max(800, self._max_chars)
        used = 0
        source_ids: list[str] = []
        for is_baseline, h in merged:
            snippet = (h["content"] or "").strip().replace("\n", " ")
            if not snippet:
                continue
            kind = h.get("kind", "raw")
            marker = "distilled" if kind == "distilled" else "raw"
            prefix = "[共享标准库] " if is_baseline else ""
            line = f"- {prefix}[{h['wing']}/{h['room']}/{marker}] {snippet[:180]}"
            summary_parts.append(line)
            source_ids.append(str(h.get("id", "")))
            if used < char_budget:
                detail = f"  ({h['role']}, {kind}, score={h['score']}) {snippet[:320]}"
                detail_parts.append(detail)
                used += len(detail)
        return InjectionContext(
            summary_layer="\n".join(summary_parts),
            decision_layer="",
            detail_layer="\n".join(detail_parts),
            source_ids=source_ids,
            token_estimate=used // 4,
        )

    async def process_turn(self, user_id: str, user_msg: str, assistant_msg: str) -> bool:
        await self._ensure_init()
        wing, source, confidence = await self._classify_wing(user_msg, assistant_msg)
        vectors = await self._embed.embed_texts([user_msg or "", assistant_msg or ""])
        await self._palace.ingest_turn(
            user_id=user_id,
            user_msg=user_msg or "",
            assistant_msg=assistant_msg or "",
            embedding_model=settings.EMBEDDING_MODEL,
            vectors=vectors,
            source_session_id=user_id,
            wing=wing,
            classifier_source=source,
            wing_confidence=confidence,
        )
        return False

    def process_turn_background(self, user_id: str, user_msg: str, assistant_msg: str) -> None:
        asyncio.create_task(self._safe_process(user_id, user_msg, assistant_msg))

    async def _safe_process(self, user_id: str, user_msg: str, assistant_msg: str) -> None:
        try:
            await self.process_turn(user_id, user_msg, assistant_msg)
            await self._maybe_run_maintenance(user_id)
        except Exception as e:
            logger.error("Background memory processing failed: %s", e)

    async def force_seal_session(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        recent = await self._palace.list_recent_drawers(user_id, limit=8)
        if not recent:
            return {"status": "no_active_session"}
        wing = recent[0]["wing"]
        summary = " | ".join((r["content"] or "")[:72] for r in recent[:3] if r["content"])
        return {"status": "sealed", "session_id": user_id, "topic": wing, "summary": summary}

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[Session]:
        await self._ensure_init()
        return []

    async def search_memories(self, user_id: str, query: str, top_k: int = 10) -> list[MemoryRecord]:
        await self._ensure_init()
        qvec = await self._embed.embed_text(query)
        hits = await self._palace.vector_search(user_id, qvec, query_text=query, top_k=top_k)
        return [self._drawer_to_record(h) for h in hits]

    async def get_all_memories(self, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        await self._ensure_init()
        rows = await self._palace.list_recent_drawers(user_id, limit=limit)
        return [self._drawer_to_record(r) for r in rows]

    async def delete_memory(self, memory_id: str) -> None:
        await self._ensure_init()
        await self._palace.delete_drawer(memory_id)

    async def get_tasks_by_status(self, user_id: str, status: str) -> list[MemoryRecord]:
        await self._ensure_init()
        return []

    async def stats(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        overview = await self._palace.palace_overview(user_id)
        wings = overview.get("wings", {})
        sem = wings.get("strategy", {}).get("drawers", 0) + wings.get("product", {}).get("drawers", 0)
        epi = wings.get("people", {}).get("drawers", 0)
        proc = wings.get("execution", {}).get("drawers", 0) + wings.get("ops", {}).get("drawers", 0)
        out = {"semantic": sem, "episodic": epi, "procedural": proc}
        for wing in WINGS:
            out[f"wing_{wing}"] = wings.get(wing, {}).get("drawers", 0)
            out[f"wing_{wing}_distilled"] = wings.get(wing, {}).get("distilled", 0)
        out["maintenance"] = self.maintenance_status(user_id)
        return out

    async def run_maintenance(self, user_id: str, lookback_days: int = 7, max_items: int = 30) -> dict[str, Any]:
        await self._ensure_init()
        now = datetime.now(timezone.utc)
        since_dt = now.timestamp() - max(1, int(lookback_days)) * 86400
        since_iso = datetime.fromtimestamp(since_dt, tz=timezone.utc).isoformat()
        recent = await self._palace.list_drawers_since(user_id=user_id, since_iso=since_iso, limit=max(120, max_items * 10))
        raw_recent = [r for r in recent if r.get("kind") == "raw"]
        distilled_created = await self._distill_recent_window(user_id, raw_recent)
        pruned_duplicates = await self._prune_duplicates(raw_recent)
        pruned_overflow = await self._prune_room_overflow(user_id=user_id, max_raw_per_room=max(20, max_items))
        result = {
            "status": "ok",
            "lookback_days": int(lookback_days),
            "rooms_touched": len({f"{r.get('wing','')}/{r.get('room','')}" for r in raw_recent}),
            "consolidated": pruned_duplicates,
            "pruned": pruned_duplicates + pruned_overflow,
            "pruned_duplicates": pruned_duplicates,
            "pruned_overflow": pruned_overflow,
            "distilled_created": distilled_created,
            "raw_considered": len(raw_recent),
        }
        self._maintenance_last_run[user_id] = now
        self._maintenance_last_result[user_id] = result
        return result

    async def ingest_file(self, file_path: str, user_id: str = "default") -> int:
        await self._ensure_init()
        path = Path(file_path)
        raw = path.read_text(encoding="utf-8")
        chunks = self._chunk_text(raw, max_chars=900, overlap=120)
        count = 0
        for chunk in chunks:
            await self.process_turn(user_id, f"[doc] {path.name}", chunk)
            count += 1
        return count

    async def palace_overview(self, user_id: str) -> dict[str, Any]:
        await self._ensure_init()
        return await self._palace.palace_overview(user_id)

    async def palace_rooms(self, user_id: str, wing: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        await self._ensure_init()
        return await self._palace.list_rooms(user_id=user_id, wing=wing, limit=limit)

    async def palace_search(self, user_id: str, query: str, top_k: int = 8, wing: str | None = None) -> list[dict[str, Any]]:
        await self._ensure_init()
        qvec = await self._embed.embed_text(query)
        return await self._palace.vector_search(user_id, qvec, query_text=query, top_k=top_k, wing=wing)

    def maintenance_status(self, user_id: str) -> dict[str, Any]:
        interval = max(300, int(getattr(settings, "MEMORY_V2_MAINTENANCE_INTERVAL_SECONDS", 1200) or 1200))
        last = self._maintenance_last_run.get(user_id)
        next_due_s = interval
        if last:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            next_due_s = max(0, int(interval - elapsed))
        return {
            "interval_seconds": interval,
            "last_run_at": last.isoformat() if last else None,
            "next_due_in_seconds": next_due_s,
            "running": user_id in self._maintenance_running,
            "last_result": self._maintenance_last_result.get(user_id),
        }

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + max_chars
            chunks.append(text[start:end])
            start += max_chars - overlap
        return chunks

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _maybe_run_maintenance(self, user_id: str) -> None:
        interval = max(300, int(getattr(settings, "MEMORY_V2_MAINTENANCE_INTERVAL_SECONDS", 1200) or 1200))
        now = datetime.now(timezone.utc)
        last = self._maintenance_last_run.get(user_id)
        if last and (now - last).total_seconds() < interval:
            return
        if user_id in self._maintenance_running:
            return
        self._maintenance_running.add(user_id)
        try:
            await self.run_maintenance(
                user_id=user_id,
                lookback_days=max(1, int(getattr(settings, "MEMORY_V2_MAINTENANCE_LOOKBACK_DAYS", 7) or 7)),
                max_items=max(20, int(getattr(settings, "MEMORY_V2_MAX_RAW_PER_ROOM", 40) or 40)),
            )
        except Exception as e:
            logger.warning("Memory maintenance skipped: %s", e)
        finally:
            self._maintenance_running.discard(user_id)

    async def _classify_wing(self, user_msg: str, assistant_msg: str) -> tuple[str, str, float]:
        combined = (user_msg or "").strip()
        rule = PalaceStore.classify_wing_rule(combined)
        if rule:
            return (rule[0], "rule", rule[1])

        prompt = self._memory_prompt("wing_classification", "")
        if prompt:
            payload = (
                f"User: {user_msg or ''}\n"
                f"Assistant: {assistant_msg or ''}\n"
                "Return JSON: {\"wing\": \"strategy|execution|product|ops|people\", \"confidence\": 0.0-1.0}"
            )
            try:
                raw = await self._llm(prompt, payload, True)
                data = self._try_json(raw)
                wing = str(data.get("wing", "")).strip().lower()
                confidence = float(data.get("confidence", 0.66) or 0.66)
                if wing in WINGS:
                    return (wing, "llm", max(0.0, min(1.0, confidence)))
            except Exception:
                pass
        return ("execution", "fallback", 0.35)

    async def _distill_recent_window(self, user_id: str, rows: list[dict[str, Any]]) -> int:
        window_minutes = max(5, int(getattr(settings, "MEMORY_V2_DISTILL_WINDOW_MINUTES", 20) or 20))
        now_ts = datetime.now(timezone.utc).timestamp()
        by_wing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            if r.get("kind") != "raw":
                continue
            if r.get("role") not in ("user", "assistant"):
                continue
            created = self._parse_time(r.get("created_at"))
            if not created:
                continue
            if now_ts - created.timestamp() > window_minutes * 60:
                continue
            by_wing[str(r.get("wing") or "execution")].append(r)

        created_count = 0
        for wing, items in by_wing.items():
            if len(items) < 4:
                continue
            conversation = []
            for it in sorted(items, key=lambda x: x.get("created_at", "")):
                conversation.append(f"{it.get('role')}: {(it.get('content') or '')[:280]}")
            prompt = self._memory_prompt("distillation", "")
            if not prompt:
                continue
            user_payload = prompt.replace("{conversation}", "\n".join(conversation[:24]))
            try:
                raw = await self._llm("You distill conversation memory into strict JSON.", user_payload, True)
                data = self._try_json(raw)
                formatted = self._format_distilled_block(data)
                if not formatted:
                    continue
                vec = await self._embed.embed_text(formatted)
                room_hint = f"{wing}-distilled-window"
                await self._palace.ingest_distilled(
                    user_id=user_id,
                    wing=wing if wing in WINGS else "execution",
                    content=formatted,
                    embedding_model=settings.EMBEDDING_MODEL,
                    vector=vec,
                    source_session_id=user_id,
                    room_hint=room_hint,
                )
                created_count += 1
            except Exception as e:
                logger.debug("Distillation skipped for wing %s: %s", wing, e)
        return created_count

    async def _prune_duplicates(self, rows: list[dict[str, Any]]) -> int:
        seen: set[tuple[str, str, str]] = set()
        to_delete: list[str] = []
        for r in sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True):
            key = (
                str(r.get("wing", "")),
                str(r.get("room", "")),
                self._normalize_text(str(r.get("content", "")))[:180],
            )
            if len(key[2]) < 20:
                continue
            if key in seen:
                to_delete.append(str(r.get("id")))
            else:
                seen.add(key)
        for drawer_id in to_delete[:200]:
            await self._palace.delete_drawer(drawer_id)
        return len(to_delete[:200])

    async def _prune_room_overflow(self, user_id: str, max_raw_per_room: int) -> int:
        recent = await self._palace.list_recent_drawers(user_id=user_id, limit=2000)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in recent:
            if row.get("kind") != "raw":
                continue
            key = (str(row.get("wing", "")), str(row.get("room", "")))
            grouped[key].append(row)
        removed = 0
        for _, items in grouped.items():
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            overflow = items[max_raw_per_room:]
            for row in overflow[:200]:
                await self._palace.delete_drawer(str(row.get("id")))
                removed += 1
        return removed

    def _memory_prompt(self, key: str, fallback: str) -> str:
        if self._prompts_cache is None:
            path = Path(__file__).parent / "prompts" / "memory_prompts.yaml"
            try:
                with path.open("r", encoding="utf-8") as f:
                    self._prompts_cache = yaml.safe_load(f) or {}
            except Exception:
                self._prompts_cache = {}
        return str(self._prompts_cache.get(key, fallback) or fallback)

    @staticmethod
    def _try_json(raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _format_distilled_block(data: dict[str, Any]) -> str:
        facts = data.get("facts", []) if isinstance(data.get("facts"), list) else []
        decisions = data.get("decisions", []) if isinstance(data.get("decisions"), list) else []
        tasks = data.get("tasks", []) if isinstance(data.get("tasks"), list) else []
        risks = data.get("risks", []) if isinstance(data.get("risks"), list) else []
        summary = str(data.get("summary", "") or "").strip()
        lines: list[str] = []
        if summary:
            lines.append(f"Summary: {summary}")
        if facts:
            lines.append("Facts:")
            lines.extend(f"- {str(x)[:180]}" for x in facts[:6])
        if decisions:
            lines.append("Decisions:")
            lines.extend(f"- {str(x)[:180]}" for x in decisions[:6])
        if tasks:
            lines.append("Tasks:")
            lines.extend(f"- {str(x)[:180]}" for x in tasks[:8])
        if risks:
            lines.append("Risks:")
            lines.extend(f"- {str(x)[:180]}" for x in risks[:6])
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_time(iso: Any) -> datetime | None:
        if not iso:
            return None
        try:
            return datetime.fromisoformat(str(iso))
        except Exception:
            return None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join((text or "").lower().split())

    @staticmethod
    def _drawer_to_record(row: dict[str, Any]) -> MemoryRecord:
        wing = row.get("wing", "")
        mt = MemoryType.SEMANTIC
        if wing == "people":
            mt = MemoryType.EPISODIC
        elif wing in ("execution", "ops"):
            mt = MemoryType.PROCEDURAL
        iso = row.get("created_at")
        try:
            dt = datetime.fromisoformat(iso) if iso else datetime.now(timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
        content = row.get("content", "")
        return MemoryRecord(
            id=row.get("id", ""),
            memory_type=mt,
            content=content,
            summary=content[:120],
            key_points=[],
            entities=[],
            priority=max(0.1, float(row.get("score", 0.5) or 0.5)),
            created_at=dt,
            updated_at=dt,
            access_count=0,
            user_id="default",
            metadata={"wing": wing, "room": row.get("room")},
        )
