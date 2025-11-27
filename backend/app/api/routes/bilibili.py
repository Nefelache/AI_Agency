from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.watch_history import WatchHistory
from app.schemas.watch_history import (
    CollectResponse,
    DailyStatsResponse,
    BackfillResponse,
    InsightRangeRequest,
    InsightRangeResponse,
    MetaResponse,
    RangeStatsResponse,
    TitleRangeResponse,
)
from app.services.bilibili_client import BilibiliAPIError, get_bilibili_client
from app.services.ingest import store_history_items
from app.services.insights import analyze_range_with_ai
from app.services.keywords import extract_keywords
from app.services.stats import build_daily_stats, build_range_stats
from app.services.categories import persist_ai_categories

router = APIRouter(prefix="/bilibili", tags=["bilibili"])


@router.post("/collect", response_model=CollectResponse)
def collect_watch_history(
    day: date = Query(default=date.today(), description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    client = get_bilibili_client()
    try:
        items = list(client.iter_history_for_day(day))
    except (ValueError, BilibiliAPIError) as exc:
        # 提升为业务级错误响应，前端可以根据 code 进行处理
        raise HTTPException(
            status_code=400,
            detail={
                "code": "BILIBILI_COLLECT_FAILED",
                "message": str(exc),
                "day": str(day),
                "hint": "请检查 B 站登录状态 / cookies 是否仍然有效，或稍后重试。",
            },
        ) from exc
    inserted = store_history_items(db, items)
    return CollectResponse(status="ok", date=day, count=inserted)


@router.get("/stats/daily", response_model=DailyStatsResponse)
def get_daily_stats(
    day: date = Query(default=date.today(), description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    records = db.query(WatchHistory).filter(WatchHistory.date == day).order_by(WatchHistory.view_at).all()
    stats = build_daily_stats(records)
    return DailyStatsResponse(
        date=day,
        total_seconds=stats["total_seconds"],
        video_count=stats["video_count"],
        per_category=stats["per_category"],
        deep_session_minutes=stats["deep_session_minutes"],
        fragmented_session_minutes=stats["fragmented_session_minutes"],
        videos=stats["videos"],
    )


@router.get("/stats/range", response_model=RangeStatsResponse)
def get_range_stats(
    start: date = Query(..., description="开始日期 YYYY-MM-DD"),
    end: date = Query(..., description="结束日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    if start > end:
        raise HTTPException(status_code=400, detail="start 必须小于等于 end")
    earliest_date = db.query(func.min(WatchHistory.date)).scalar()
    latest_date = db.query(func.max(WatchHistory.date)).scalar()
    if not earliest_date or not latest_date:
        return RangeStatsResponse(
            requested_start=start,
            requested_end=end,
            effective_start=None,
            effective_end=None,
            covered_days=0,
            coverage_ratio=0.0,
            totals={
                "total_minutes": 0,
                "deep_minutes": 0,
                "mid_minutes": 0,
                "fragmented_minutes": 0,
                "video_count": 0,
            },
            by_category={},
        )

    effective_start = max(start, earliest_date)
    effective_end = min(end, latest_date)
    if effective_start > effective_end:
        return RangeStatsResponse(
            requested_start=start,
            requested_end=end,
            effective_start=None,
            effective_end=None,
            covered_days=0,
            coverage_ratio=0.0,
            totals={
                "total_minutes": 0,
                "deep_minutes": 0,
                "mid_minutes": 0,
                "fragmented_minutes": 0,
                "video_count": 0,
            },
            by_category={},
        )

    records = (
        db.query(WatchHistory)
        .filter(WatchHistory.date >= effective_start, WatchHistory.date <= effective_end)
        .order_by(WatchHistory.view_at)
        .all()
    )
    stats = build_range_stats(records)
    requested_days = (end - start).days + 1
    coverage_ratio = stats["covered_days"] / requested_days if requested_days > 0 else 0
    totals = {
        "total_minutes": round(stats["total_seconds"] / 60),
        "deep_minutes": stats["deep_minutes"],
        "mid_minutes": stats["mid_minutes"],
        "fragmented_minutes": stats["fragmented_minutes"],
        "video_count": stats["video_count"],
    }
    return RangeStatsResponse(
        requested_start=start,
        requested_end=end,
        effective_start=effective_start,
        effective_end=effective_end,
        covered_days=stats["covered_days"],
        coverage_ratio=round(coverage_ratio, 3),
        totals=totals,
        by_category=stats["by_category"],
    )


@router.get("/titles/range", response_model=TitleRangeResponse)
def get_titles_range(
    start: date = Query(..., description="开始日期 YYYY-MM-DD"),
    end: date = Query(..., description="结束日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    if start > end:
        raise HTTPException(status_code=400, detail="start 必须小于等于 end")
    records = (
        db.query(WatchHistory)
        .filter(WatchHistory.date >= start, WatchHistory.date <= end)
        .order_by(WatchHistory.view_at)
        .all()
    )
    stats = build_range_stats(records)
    titles = [r.title for r in records if r.title]
    total_seconds = stats["total_seconds"]
    per_category = []
    for name, data in stats["by_category"].items():
        seconds = data["minutes"] * 60
        ratio = (seconds / total_seconds) if total_seconds else 0
        per_category.append({"name": name, "seconds": seconds, "ratio": ratio})
    return TitleRangeResponse(
        date_range={"start": start, "end": end},
        total_videos=len(records),
        titles=titles,
        per_category_basic_stats=per_category,
        keywords=extract_keywords(titles),
    )


@router.post("/insights/range", response_model=InsightRangeResponse)
def create_range_insights(payload: InsightRangeRequest, db: Session = Depends(get_db)):
    if payload.start > payload.end:
        raise HTTPException(status_code=400, detail="start 必须小于等于 end")
    records = (
        db.query(WatchHistory)
        .filter(WatchHistory.date >= payload.start, WatchHistory.date <= payload.end)
        .order_by(WatchHistory.view_at)
        .all()
    )
    stats = build_range_stats(records)
    titles = [r.title for r in records if r.title]
    ai_payload = {
        "range": {"start": payload.start, "end": payload.end},
        "range_stats": stats,
        "keywords": extract_keywords(titles),
    }
    insights = analyze_range_with_ai(ai_payload, titles)
    if insights.get("categories"):
        persist_ai_categories(
            db,
            insights["categories"],
            payload.start,
            payload.end,
        )
    return InsightRangeResponse(**{k: insights.get(k) for k in InsightRangeResponse.model_fields})


@router.get("/meta", response_model=MetaResponse)
def get_meta(db: Session = Depends(get_db)):
    earliest_date = db.query(func.min(WatchHistory.date)).scalar()
    latest_date = db.query(func.max(WatchHistory.date)).scalar()
    total_videos = db.query(func.count(WatchHistory.id)).scalar() or 0
    total_seconds = db.query(func.coalesce(func.sum(WatchHistory.duration), 0)).scalar() or 0
    return MetaResponse(
        earliest_date=earliest_date,
        latest_date=latest_date,
        total_videos=total_videos,
        total_minutes=round(total_seconds / 60, 2),
    )


@router.post("/backfill/recent", response_model=BackfillResponse)
def backfill_recent(
    max_days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    client = get_bilibili_client()
    inserted = 0
    skipped = 0
    collected_items = []
    cutoff_dt = datetime.combine(date.today() - timedelta(days=max_days), datetime.min.time())
    cutoff_ts = int(cutoff_dt.timestamp())
    for item in client.iter_history(pages=200):
        ts = client._extract_timestamp(item)
        if ts is None:
            continue
        collected_items.append(item)
        if ts < cutoff_ts:
            break
    details = store_history_items(db, collected_items, return_details=True)
    earliest_date = db.query(func.min(WatchHistory.date)).scalar()
    return BackfillResponse(
        inserted=details["inserted"],
        skipped=details["skipped"],
        earliest_synced_date=earliest_date,
    )
