from __future__ import annotations

import hashlib
import logging
import math
from typing import Iterable

import httpx

from my_agent_os.config.settings import settings

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """
    Embedding client with graceful fallback.

    Primary path: OpenAI-compatible /v1/embeddings endpoint.
    Fallback path: deterministic local embedding to keep retrieval available.
    """

    def __init__(self):
        self._model = settings.EMBEDDING_MODEL
        self._base_url = settings.EMBEDDING_BASE_URL.rstrip("/")
        self._api_key = settings.EMBEDDING_API_KEY or settings.DEEPSEEK_API_KEY
        self._dim = max(64, int(settings.EMBEDDING_DIM))

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await self._embed_remote(texts)
        except Exception as e:
            logger.warning("Embedding API failed, fallback local vectors: %s", e)
            return [self._embed_local(t) for t in texts]

    async def embed_text(self, text: str) -> list[float]:
        vectors = await self.embed_texts([text])
        return vectors[0] if vectors else self._embed_local(text)

    async def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        if not self._api_key:
            raise RuntimeError("Missing EMBEDDING_API_KEY/DEEPSEEK_API_KEY")
        url = f"{self._base_url}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self._model, "input": texts}
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
        proxy = settings.HTTPS_PROXY or None
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        data = resp.json()
        out = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        vectors = [item.get("embedding", []) for item in out]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding response length mismatch")
        return [self._normalize(v) for v in vectors]

    def _embed_local(self, text: str) -> list[float]:
        dim = self._dim
        vec = [0.0] * dim
        tokens = list(_tokenize(text))
        if not tokens:
            return vec
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return self._normalize(vec)

    @staticmethod
    def _normalize(vec: Iterable[float]) -> list[float]:
        arr = [float(x) for x in vec]
        norm = math.sqrt(sum(x * x for x in arr)) or 1.0
        return [x / norm for x in arr]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    return float(dot)


def _tokenize(text: str) -> Iterable[str]:
    token = []
    for ch in text.lower():
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
            token.append(ch)
        elif token:
            yield "".join(token)
            token = []
    if token:
        yield "".join(token)
