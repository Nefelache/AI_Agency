from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class Event:
    """Represents a single activity captured by the personal agency."""

    source: str
    type: str
    ts_start: datetime
    ts_end: Optional[datetime] = None
    duration_sec: Optional[int] = None
    title: Optional[str] = None
    url: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
