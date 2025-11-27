from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List

STOPWORDS = {
    "的",
    "了",
    "呢",
    "啊",
    "我",
    "你",
    "他",
    "她",
    "我们",
    "他们",
    "|",
}


def extract_keywords(titles: Iterable[str], top_k: int = 20) -> List[str]:
    counter: Counter[str] = Counter()
    for title in titles:
        if not title:
            continue
        tokens = re.split(r"[^\w\u4e00-\u9fff]+", title)
        for token in tokens:
            token = token.strip().lower()
            if len(token) <= 1 or token in STOPWORDS:
                continue
            counter[token] += 1
    return [word for word, _ in counter.most_common(top_k)]
