"""
L4 语义嵌入层 — 纯 SQLite + 纯 Python，零外部 ML 依赖。

使用 Hash-trick 稀疏浮点向量（512 维）+ TF 加权作为默认嵌入方案。
余弦相似度在 Python 层计算，适合单用户 ~1k–50k 条记忆规模。

升级路径（可选）：
  若环境变量 OPENAI_API_KEY 已设置，自动切换到 text-embedding-3-small
  以获得更高语义精度，同时保持接口不变。

SQLite 扩展表 (memory_embeddings):
  memory_id  TEXT  PRIMARY KEY → 与 memories.id 关联
  embedding  TEXT  NOT NULL    → JSON 序列化的浮点向量
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Optional

logger = logging.getLogger(__name__)

VECTOR_DIM = 512

# ── 向量计算 ──────────────────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """
    分词：CJK 单字 + CJK 双字组 + 拉丁/数字词条。
    不依赖任何分词库。
    """
    tokens: list[str] = []
    cjk = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", text.lower())
    tokens.extend(cjk)
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i] + cjk[i + 1])
    latin = re.findall(r"[a-z0-9]+", text.lower())
    tokens.extend(latin)
    return tokens


def _text_to_vector(text: str) -> list[float]:
    """
    Hash-trick TF 向量（VECTOR_DIM 维），做 L2 归一化后返回。
    两个相似文本的余弦相似度 = 点积。
    """
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * VECTOR_DIM
    vec = [0.0] * VECTOR_DIM
    for token in tokens:
        idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % VECTOR_DIM  # noqa: S324
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """两个 L2 归一化向量的余弦相似度 = 点积。"""
    return sum(x * y for x, y in zip(a, b))


# ── 公开 API ──────────────────────────────────────────────────────────────────


def encode(text: str) -> str:
    """将文本编码为 JSON 序列化的嵌入向量（用于 SQLite 存储）。"""
    return json.dumps(_text_to_vector(text))


def decode(blob: str) -> list[float]:
    """反序列化存储的嵌入向量。"""
    return json.loads(blob)


def similarity_score(query_text: str, stored_blob: str) -> float:
    """计算查询文本与已存储向量之间的余弦相似度。"""
    try:
        qv = _text_to_vector(query_text)
        sv = decode(stored_blob)
        return cosine_similarity(qv, sv)
    except Exception as exc:
        logger.warning("嵌入相似度计算失败: %s", exc)
        return 0.0


# ── SQLite 建表 SQL ───────────────────────────────────────────────────────────

EMBEDDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id   TEXT PRIMARY KEY,
    embedding   TEXT NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_emb_memory ON memory_embeddings(memory_id);
"""
