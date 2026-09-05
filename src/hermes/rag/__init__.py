"""Multi-Agent RAG — deterministic retrieval layer (stdlib only).

Design: BM25-lite lexical retrieval over an ingested corpus
(vendor quotes + approved-vendor registry + spec docs). No external
vector DB, no network — tests stay deterministic. An optional embedding
hook (`embed` callable) upgrades scoring to cosine similarity when
configured; the engine never depends on it.

    ingest (quotes / vendors / docs) → RagIndex (JSON-persisted)
        → retrieve_evidence(query) → cited chunks [source_uri]
        → specialists ground claims → verifier checks citations
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)?")


def tokenize(text: str) -> list[str]:
    toks = _TOKEN.findall((text or "").lower())
    # naive stemming: laptop/laptops, vendor/vendors → single form
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
            for t in toks]


@dataclass
class Chunk:
    doc_id: str
    source_uri: str
    text: str
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = tokenize(self.text)


@dataclass
class ScoredChunk:
    score: float
    chunk: Chunk


class RagIndex:
    """In-memory corpus + BM25-lite scorer, JSON-persistable for sharing
    across worker threads/processes via a file path."""

    def __init__(self, embed: Callable[[list[str]], list[list[float]]] | None = None):
        self.chunks: list[Chunk] = []
        self.embed = embed
        self._vectors: list[list[float]] = []

    def add(self, doc_id: str, source_uri: str, text: str) -> None:
        self.chunks.append(Chunk(doc_id, source_uri, text))

    def __len__(self) -> int:
        return len(self.chunks)

    # ---- scoring ---- #
    def _idf(self) -> dict[str, float]:
        n = max(1, len(self.chunks))
        df: dict[str, int] = {}
        for c in self.chunks:
            for tok in set(c.tokens):
                df[tok] = df.get(tok, 0) + 1
        return {tok: math.log(1 + n / max(1, f)) for tok, f in df.items()}

    def _lexical(self, query_tokens: list[str]) -> list[ScoredChunk]:
        idf = self._idf()
        qset = set(query_tokens)
        out = []
        for c in self.chunks:
            tf: dict[str, int] = {}
            for tok in c.tokens:
                if tok in qset:
                    tf[tok] = tf.get(tok, 0) + 1
            score = sum((1 + math.log(f)) * idf.get(tok, 0.0) for tok, f in tf.items())
            # small bonus for multi-term coverage
            score *= 1 + 0.1 * len(tf)
            out.append(ScoredChunk(score, c))
        return out

    def _cosine(self, query: str) -> list[ScoredChunk] | None:
        if not self.embed or not self.chunks:
            return None
        try:
            vecs = self.embed([query] + [c.text for c in self.chunks])
        except Exception:
            return None
        q, docs = vecs[0], vecs[1:]
        out = []
        for v, c in zip(docs, self.chunks):
            dot = sum(a * b for a, b in zip(q, v))
            nq = math.sqrt(sum(a * a for a in q)) or 1.0
            nv = math.sqrt(sum(b * b for b in v)) or 1.0
            out.append(ScoredChunk(dot / (nq * nv), c))
        return out

    def query(self, text: str, top_k: int = 3) -> list[ScoredChunk]:
        if not self.chunks or not (text or "").strip():
            return []
        ranked = self._cosine(text) or self._lexical(tokenize(text))
        ranked.sort(key=lambda s: s.score, reverse=True)
        return [s for s in ranked[:max(1, top_k)] if s.score > 0]

    # ---- persistence ---- #
    def to_dict(self) -> dict[str, Any]:
        return {"chunks": [{"doc_id": c.doc_id, "source_uri": c.source_uri,
                            "text": c.text} for c in self.chunks]}

    def save(self, path: str) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1))
        return str(p)

    @classmethod
    def load(cls, path: str, embed: Callable | None = None) -> "RagIndex":
        idx = cls(embed=embed)
        try:
            data = json.loads(Path(path).read_text())
            for c in data.get("chunks", []):
                idx.add(str(c.get("doc_id", "")), str(c.get("source_uri", "")),
                        str(c.get("text", "")))
        except Exception:
            pass
        return idx


def ingest_quotes(index: RagIndex, quotes: list[dict[str, Any]]) -> RagIndex:
    for q in quotes:
        vendor = str(q.get("vendor", "unknown"))
        body = q.get("raw_text", "") or json.dumps(q)
        text = (f"unit price ${q.get('unit_price', 0)} quantity {q.get('quantity', 0)} "
                f"total ${q.get('total', 0)}. {body}")
        index.add(f"quote-{vendor.lower()}", str(q.get("source_uri", "")),
                  f"Vendor quote {vendor}: {text}")
    return index


def ingest_vendors(index: RagIndex, vendors_path: str = "") -> RagIndex:
    vp = vendors_path or str(Path(__file__).resolve().parent.parent
                             / "procurement" / "vendors.json")
    try:
        data = json.loads(Path(vp).read_text())
    except Exception:
        data = {"approved_vendors": {"lenovo": True, "dell": True, "hp": False}, "notes": {}}
    for vendor, approved in (data.get("approved_vendors") or {}).items():
        note = (data.get("notes") or {}).get(vendor, "")
        status = "APPROVED vendor" if approved else "NOT approved vendor"
        index.add(f"vendor-{vendor}", "vendors.json",
                  f"{vendor}: {status}. {note}".strip())
    return index


def build_case_index(quotes: list[dict[str, Any]], required_spec: str = "",
                     vendors_path: str = "") -> RagIndex:
    index = RagIndex()
    ingest_quotes(index, quotes)
    ingest_vendors(index, vendors_path)
    if required_spec:
        index.add("required-spec", "request",
                  f"Required specification: {required_spec}")
    return index


def format_hits(hits: list[ScoredChunk]) -> str:
    lines = []
    for h in hits:
        snippet = " ".join(h.chunk.text.split())[:400]
        lines.append(f"[score={h.score:.2f} source={h.chunk.source_uri}] {snippet}")
    return "\n".join(lines) if lines else "(no evidence retrieved)"
