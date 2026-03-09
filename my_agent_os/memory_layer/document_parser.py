"""
Document Parser — The File Shredder.

Responsibilities:
  1. Ingest raw documents (PDF, DOCX, Markdown, plain text).
  2. Chunk them into semantically coherent blocks.
  3. Push embeddings into the local vector_db.
  4. Expose a clean `retrieve()` interface consumed by agent_core.

The vector_db backend is fully abstracted — swap ChromaDB for FAISS
or any other store by implementing VectorStore protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Chunk:
    text: str
    metadata: dict[str, Any]
    source: str


@runtime_checkable
class VectorStore(Protocol):
    """Abstract contract for any local vector database."""

    def upsert(self, chunks: list[Chunk]) -> None: ...
    def query(self, text: str, top_k: int = 5) -> list[dict[str, Any]]: ...


class LocalChromaStore:
    """
    Default VectorStore backed by ChromaDB (pure-local, no cloud calls).
    Lazy-initialized so the import only happens when actually used.
    """

    def __init__(self, persist_dir: str = "my_agent_os/memory_layer/vector_db/chroma_data"):
        self._persist_dir = persist_dir
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            import chromadb
            client = chromadb.PersistentClient(path=self._persist_dir)
            self._collection = client.get_or_create_collection("agent_memory")
        return self._collection

    def upsert(self, chunks: list[Chunk]) -> None:
        col = self._get_collection()
        col.upsert(
            ids=[f"{c.source}_{i}" for i, c in enumerate(chunks)],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )

    def query(self, text: str, top_k: int = 5) -> list[dict[str, Any]]:
        col = self._get_collection()
        results = col.query(query_texts=[text], n_results=top_k)
        hits = []
        for doc, meta in zip(
            results.get("documents", [[]])[0],
            results.get("metadatas", [[]])[0],
        ):
            hits.append({"text": doc, "metadata": meta})
        return hits


_store: VectorStore = LocalChromaStore()


def set_store(store: VectorStore) -> None:
    """Swap the vector backend at runtime (useful for testing)."""
    global _store
    _store = store


def ingest(file_path: str | Path) -> int:
    """
    Parse a document, chunk it, and push to the vector store.
    Returns the number of chunks produced.
    """
    path = Path(file_path)
    raw = path.read_text(encoding="utf-8")
    chunks = _chunk_text(raw, source=str(path))
    _store.upsert(chunks)
    return len(chunks)


async def retrieve(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Standard retrieval interface consumed by router_engine."""
    return _store.query(query, top_k=top_k)


def _chunk_text(
    text: str,
    source: str,
    max_chars: int = 800,
    overlap: int = 100,
) -> list[Chunk]:
    """Sliding-window chunker with overlap for context continuity."""
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(Chunk(
            text=text[start:end],
            metadata={"char_start": start, "char_end": end},
            source=source,
        ))
        start += max_chars - overlap
    return chunks
