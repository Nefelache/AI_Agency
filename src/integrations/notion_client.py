from __future__ import annotations

from typing import Any, Dict


def push_daily_summary_to_notion(summary: Dict[str, Any], text: str) -> None:
    print("【Notion 占位】即将推送以下内容：")
    print(text)
