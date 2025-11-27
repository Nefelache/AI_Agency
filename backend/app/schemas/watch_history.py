from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class WatchHistoryItem(BaseModel):
    bvid: str
    title: str
    author: Optional[str] = None
    duration: int
    view_at: datetime
    category: Optional[str] = None


class CategoryStat(BaseModel):
    name: str
    seconds: int
    ratio: float


class DailyStatsResponse(BaseModel):
    date: date
    total_seconds: int
    video_count: int
    per_category: List[CategoryStat]
    deep_session_minutes: int
    fragmented_session_minutes: int
    videos: List[WatchHistoryItem]


class RangeTotals(BaseModel):
    total_minutes: int
    deep_minutes: int
    mid_minutes: int
    fragmented_minutes: int
    video_count: int


class RangeCategoryBreakdown(BaseModel):
    minutes: int
    video_count: int


class RangeStatsResponse(BaseModel):
    requested_start: date
    requested_end: date
    effective_start: Optional[date]
    effective_end: Optional[date]
    covered_days: int
    coverage_ratio: float
    totals: RangeTotals
    by_category: Dict[str, RangeCategoryBreakdown]


class TitleRangeResponse(BaseModel):
    date_range: dict
    total_videos: int
    titles: List[str]
    per_category_basic_stats: List[CategoryStat]
    keywords: List[str]


class CollectResponse(BaseModel):
    status: str
    date: date
    count: int


class InsightRangeRequest(BaseModel):
    start: date
    end: date
    force_refresh: bool = False


class InsightRangeResponse(BaseModel):
    title: str
    summary: str
    adhd_insights: List[str]
    gentle_suggestions: List[str]
    categories: Optional[List[dict]] = None


class MetaResponse(BaseModel):
    earliest_date: Optional[date]
    latest_date: Optional[date]
    total_videos: int
    total_minutes: float


class BackfillResponse(BaseModel):
    inserted: int
    skipped: int
    earliest_synced_date: Optional[date]
