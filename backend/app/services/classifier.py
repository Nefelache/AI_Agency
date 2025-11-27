from __future__ import annotations

import re
from typing import Optional

BILI_SECTION_MAP = {
    "知识": "study",
    "校园": "study",
    "职场": "study",
    "科技": "study",
    "数码": "consume",
    "生活": "other",
    "美食": "other",
    "音乐": "music",
    "演奏": "music",
    "影视": "other",
    "动画": "anime_game",
    "番剧": "anime_game",
    "游戏": "anime_game",
    "娱乐": "other",
    "记录": "psychology",
    "汽车": "consume",
    "时尚": "consume",
}

CATEGORY_RULES = {
    "study": [
        r"学习",
        r"课程",
        r"考试",
        r"GRE",
        r"知识",
        r"教学",
        r"校园",
        r"科普",
        r"技术",
        r"教程",
        r"编程",
        r"论文",
    ],
    "music": [
        r"音乐",
        r"演奏",
        r"live",
        r"钢琴",
        r"吉他",
        r"唱((歌)|歌)",
        r"音乐会",
    ],
    "anime_game": [
        r"番剧",
        r"动画",
        r"游戏",
        r"二次元",
        r"cos",
        r"手办",
        r"漫画",
    ],
    "psychology": [
        r"心理",
        r"冥想",
        r"疗愈",
        r"ADHD",
        r"焦虑",
        r"抑郁",
        r"情绪",
        r"身心",
        r"自我照顾",
    ],
    "consume": [
        r"开箱",
        r"评测",
        r"测评",
        r"购物",
        r"消费",
        r"上手",
        r"对比",
        r"种草",
        r"数码",
        r"科技",
        r"家电",
    ],
}

TITLE_KEYWORD_BOOSTS = [
    (r"vlog|生活记录|日常", "other"),
    (r"正念|呼吸|冥想|心理", "psychology"),
    (r"挑战|攻略|赛事", "anime_game"),
    (r"总结|复盘|经验", "study"),
]


def classify(tname: Optional[str], title: Optional[str]) -> str:
    section = _match_section(tname)
    if section:
        return section
    text = " ".join(filter(None, [tname or "", title or ""]))
    for pattern, category in TITLE_KEYWORD_BOOSTS:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    for category, patterns in CATEGORY_RULES.items():
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return category
    return "other"


def _match_section(tname: Optional[str]) -> Optional[str]:
    if not tname:
        return None
    for key, value in BILI_SECTION_MAP.items():
        if key in tname:
            return value
    return None
