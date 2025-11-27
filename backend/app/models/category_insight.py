from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import Column, Date, DateTime, Integer, String, Text, UniqueConstraint

from .base import Base


class CategoryInsight(Base):
    __tablename__ = "category_insights"
    __table_args__ = (UniqueConstraint("name", name="uq_category_name"),)

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    first_seen_start = Column(Date, nullable=True)
    last_seen_start = Column(Date, nullable=True)
    last_seen_end = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
