from __future__ import annotations

from datetime import datetime, date as date_type

from sqlalchemy import Column, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON

from .base import Base


class WatchHistory(Base):
    __tablename__ = "watch_history"
    __table_args__ = (
        UniqueConstraint("bvid", "view_at", name="uq_bvid_view_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    bvid = Column(String(32), nullable=False)
    title = Column(String(512), nullable=False)
    author = Column(String(256), nullable=True)
    view_at = Column(DateTime, nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    duration = Column(Integer, nullable=False, default=0)
    tname = Column(String(128), nullable=True)
    category = Column(String(64), nullable=True)
    source = Column(String(64), nullable=False, default="bilibili")
    tags = Column(JSON, nullable=True)
    extra_meta = Column(Text, nullable=True)
