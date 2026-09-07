"""Scraped document store: RagIndex wrapper with scraper metadata fields.

Adds backward-compatible metadata (source_type, task_id, scraped_at, ttl)
to each stored chunk, plus query helpers for dedup and TTL eviction.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..rag import RagIndex as _RagIndex


class Chunk:
    def __init__(self, doc_id: str, source_uri: str, text: str, tokens: list[str],
                 source_type: str, task_id: str, scraped_at: str, ttl_days: int):
        self.doc_id = doc_id
        self.source_uri = source_uri
        self.text = text
        self.tokens = tokens
        self.source_type = source_type
        self.task_id = task_id
        self.scraped_at = scraped_at
        self.ttl_days = ttl_days

    def age_days(self) -> int:
        try:
            dt = datetime.fromisoformat(self.scraped_at)
            return (datetime.now(UTC) - dt).days
        except Exception:
            return 9999

    def is_stale(self) -> bool:
        return self.age_days() > self.ttl_days

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source_uri": self.source_uri,
            "text": self.text,
            "tokens": self.tokens,
            "source_type": self.source_type,
            "task_id": self.task_id,
            "scraped_at": self.scraped_at,
            "ttl_days": self.ttl_days,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chunk":
        return cls(
            doc_id=str(d.get("doc_id", "")),
            source_uri=str(d.get("source_uri", "")),
            text=str(d.get("text", "")),
            tokens=list(d.get("tokens", [])),
            source_type=str(d.get("source_type", "")),
            task_id=str(d.get("task_id", "")),
            scraped_at=str(d.get("scraped_at", "")),
            ttl_days=int(d.get("ttl_days", 30)),
        )


class ScrapedDocStore:
    def __init__(self, rag_path: str = "", ttl_days: int = 30):
        self._index = _RagIndex()
        self._chunks: list[Chunk] = []
        self.rag_path = rag_path
        self.ttl_days = ttl_days
        if rag_path:
            self._load(rag_path)

    def _load(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text())
            for c in data.get("chunks", []):
                ch = Chunk.from_dict(c)
                self._chunks.append(ch)
                self._index.add(ch.doc_id, ch.source_uri, ch.text)
        except Exception:
            pass

    def save(self, path: str = "") -> str:
        p = Path(path or self.rag_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"chunks": [c.to_dict() for c in self._chunks]}
        p.write_text(json.dumps(data, indent=1))
        self.rag_path = str(p)
        return str(p)

    def add(self, doc_id: str, source_uri: str, text: str,
            source_type: str = "", task_id: str = "", scraped_at: str = "",
            ttl_days: int = 30) -> Chunk:
        from ..rag import tokenize
        ch = Chunk(
            doc_id=doc_id,
            source_uri=source_uri,
            text=text,
            tokens=tokenize(text),
            source_type=source_type,
            task_id=task_id,
            scraped_at=scraped_at or datetime.now(UTC).isoformat(),
            ttl_days=ttl_days or self.ttl_days,
        )
        self._chunks.append(ch)
        self._index.add(doc_id, source_uri, text)
        return ch

    def evict_stale(self, ttl_days: int = 0) -> list[Chunk]:
        stale = [c for c in self._chunks if c.is_stale()]
        self._chunks = [c for c in self._chunks if not c.is_stale()]
        self._rebuild_index()
        return stale

    def _rebuild_index(self) -> None:
        self._index = _RagIndex()
        for c in self._chunks:
            self._index.add(c.doc_id, c.source_uri, c.text)

    def find_by_source(self, source_uri: str) -> list[Chunk]:
        return [c for c in self._chunks if c.source_uri == source_uri]

    def find_by_task(self, task_id: str) -> list[Chunk]:
        return [c for c in self._chunks if c.task_id == task_id]

    def find_by_source_type(self, source_type: str) -> list[Chunk]:
        return [c for c in self._chunks if c.source_type == source_type]

    @property
    def index(self) -> _RagIndex:
        return self._index
