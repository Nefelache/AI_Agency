from __future__ import annotations

from datetime import date
from typing import List

from sqlalchemy.orm import Session

from app.models.category_insight import CategoryInsight


def persist_ai_categories(db: Session, categories: List[dict], start: date, end: date) -> None:
    for cat in categories:
        name = (cat or {}).get("name")
        if not name:
            continue
        description = cat.get("description")
        existing = db.query(CategoryInsight).filter(CategoryInsight.name == name).one_or_none()
        if existing:
            if description:
                existing.description = description
            existing.last_seen_start = start
            existing.last_seen_end = end
        else:
            db.add(
                CategoryInsight(
                    name=name,
                    description=description,
                    first_seen_start=start,
                    last_seen_start=start,
                    last_seen_end=end,
                )
            )
    db.commit()
