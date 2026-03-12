"""
Memory Data Models — The schema of cognition.

Three canonical memory types inspired by cognitive science:
  Episodic   — specific events, conversations, experiences
  Semantic   — extracted facts, knowledge, decisions
  Procedural — behavioral patterns, preferences, routines

Pipeline DTOs carry data between extraction → consolidation → injection.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Helpers ──────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def short_uuid() -> str:
    return uuid4().hex[:16]


def entity_hash(text: str) -> str:
    """Deterministic hash for entity-based O(1) lookups."""
    normalized = text.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Enums ────────────────────────────────────────────────

class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SEALED = "sealed"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    SEALED = "sealed"


class ConsolidationOp(str, Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"


# ── Core Records ─────────────────────────────────────────

class MemoryRecord(BaseModel):
    id: str = Field(default_factory=short_uuid)
    memory_type: MemoryType
    content: str
    summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    priority: float = 0.5
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_accessed: datetime | None = None
    access_count: int = 0
    status: MemoryStatus = MemoryStatus.ACTIVE
    session_id: str | None = None
    user_id: str = "default"


class Session(BaseModel):
    id: str = Field(default_factory=short_uuid)
    user_id: str = "default"
    status: SessionStatus = SessionStatus.ACTIVE
    topic: str | None = None
    summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    sealed_at: datetime | None = None
    turn_count: int = 0


class Turn(BaseModel):
    id: int | None = None
    session_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime = Field(default_factory=utcnow)


# ── Pipeline DTOs ────────────────────────────────────────

class ExtractionResult(BaseModel):
    """Output of the LLM extraction phase."""
    facts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    patterns: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    should_seal: bool = False
    topic: str | None = None


class ConsolidationDecision(BaseModel):
    """Output of the LLM consolidation phase."""
    operation: ConsolidationOp
    memory_id: str | None = None
    content: str | None = None
    reason: str = ""


class RetrievedMemory(BaseModel):
    """A memory record annotated with retrieval metadata."""
    record: MemoryRecord
    relevance_score: float = 0.0  # Final priority for ranking (includes recency, etc.)
    query_relevance: float = 0.0  # Raw query similarity, for budget allocation
    source: str = "vector"  # "hash" | "vector" | "both"


class InjectionContext(BaseModel):
    """The final context package injected into the LLM prompt."""
    summary_layer: str = ""
    decision_layer: str = ""
    detail_layer: str = ""
    source_ids: list[str] = Field(default_factory=list)
    token_estimate: int = 0
