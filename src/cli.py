from __future__ import annotations

from datetime import date

import typer

from src.analytics.daily_summary import build_daily_summary, format_summary_text
from src.collectors.browser_chrome import ChromeHistoryCollector
from src.core.config import DB_PATH, ensure_dirs
from src.core.db import Database
from src.integrations.notion_client import push_daily_summary_to_notion

app = typer.Typer(help="Personal ADHD agency CLI")


def parse_iso_date(day: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise typer.BadParameter("日期格式需为 YYYY-MM-DD") from exc


@app.command()
def init_db() -> None:
    ensure_dirs()
    db = Database(DB_PATH)
    db.init_schema()
    typer.echo(f"数据库已初始化：{DB_PATH}")


@app.command()
def collect(day: str = typer.Option(date.today().isoformat(), help="目标日期 YYYY-MM-DD")) -> None:
    target_date = parse_iso_date(day)
    ensure_dirs()
    db = Database(DB_PATH)
    collector = ChromeHistoryCollector()
    events = collector.collect_for_date(target_date)
    for event in events:
        db.insert_event(event)
    typer.echo(f"已写入 {len(events)} 条事件至数据库。")


@app.command()
def summary(day: str = typer.Option(date.today().isoformat(), help="目标日期 YYYY-MM-DD")) -> None:
    target_date = parse_iso_date(day)
    summary_data = build_daily_summary(target_date)
    text = format_summary_text(summary_data)
    typer.echo(text)
    push_daily_summary_to_notion(summary_data, text)


if __name__ == "__main__":
    app()
