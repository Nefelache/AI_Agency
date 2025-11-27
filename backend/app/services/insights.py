from __future__ import annotations

import json
from typing import Dict, List

import requests

from app.core.config import get_settings
from app.services.keywords import extract_keywords

PROMPT_TEMPLATE = """你是一位温柔的陪伴者，面对一位有 ADHD 特质、正在学习自我接纳的用户。
请根据以下观看统计，用中文写出 4 部分内容，不要批评，避免 KPI 语气：
1) 总体观看模式总结（3 句内）
2) 与 ADHD 特征相关的温柔洞察
3) 两条温柔的小建议
4) 给这个时间段取一个日记式标题

输入数据：
- 时间范围: {start} ~ {end}
- 总时长: {total_hours} 小时
- 深潜/中段/碎片 (分钟): {deep}/{mid}/{fragmented}
- 分类占比: {categories}
- 高频关键词: {keywords}
"""


def analyze_range_with_ai(stats: Dict, titles: List[str]) -> Dict[str, object]:
    settings = get_settings()
    keywords = extract_keywords(titles, top_k=8)
    # stats payload shape:
    # {
    #   "range": {"start": date, "end": date},
    #   "range_stats": {
    #       "total_seconds": int,
    #       "deep_minutes": int,
    #       "mid_minutes": int,
    #       "fragmented_minutes": int,
    #       "covered_days": int,
    #       "by_category": {
    #           "<name>": {"minutes": int, "video_count": int},
    #           ...
    #       },
    #   },
    #   "keywords": [...]
    # }
    range_meta = stats["range"]
    range_stats = stats["range_stats"]

    # Derive a human-readable category summary from by_category instead of
    # assuming a precomputed per_category list.
    by_category = range_stats.get("by_category", {}) or {}
    total_seconds = range_stats.get("total_seconds", 0) or 0
    per_category_items: List[str] = []
    for name, data in by_category.items():
        minutes = (data or {}).get("minutes", 0) or 0
        seconds = minutes * 60
        ratio = (seconds / total_seconds) if total_seconds else 0
        per_category_items.append(f"{name}: {minutes} 分钟 ({ratio:.0%})")
    categories_str = "，".join(per_category_items) if per_category_items else "无"

    prompt = PROMPT_TEMPLATE.format(
        start=range_meta["start"],
        end=range_meta["end"],
        total_hours=round(range_stats["total_seconds"] / 3600, 1),
        deep=range_stats["deep_minutes"],
        mid=range_stats["mid_minutes"],
        fragmented=range_stats["fragmented_minutes"],
        categories=categories_str,
        keywords=", ".join(keywords) or "无",
    )

    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法生成 AI 解读。")

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": "你是一位温柔的观察者，请返回 JSON 响应，字段包括 title, summary, adhd_insights(数组), gentle_suggestions(数组)。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        json=payload,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"DeepSeek API 调用失败：{response.status_code} {response.text}")

    content = response.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DeepSeek 返回内容无法解析为 JSON") from exc

    parsed.setdefault("categories", [])
    return parsed
