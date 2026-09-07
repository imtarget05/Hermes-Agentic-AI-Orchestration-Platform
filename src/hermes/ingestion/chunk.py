"""Bounded-text chunking over canonical content.

The chunker only knows about `CanonicalDocument` — never about any source type.
Headings stay attached to their content so metadata survives into every chunk.
Short documents yield a single chunk (backward compatible with the RAG index's
one-chunk-per-quote contract).
"""
from __future__ import annotations

from .model import CanonicalDocument


def chunk_document(doc: CanonicalDocument, max_words: int = 200) -> list[str]:
    if max_words <= 0:
        return [doc.to_text(normalized=True)]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for block in doc.blocks:
        text = (block.searchable() or "").strip()
        if not text:
            continue
        words = len(text.split())
        if current and current_words + words > max_words:
            chunks.append("\n".join(current))
            current, current_words = [], 0
        current.append(text)
        current_words += words

    if current:
        chunks.append("\n".join(current))

    if not chunks:
        chunks.append("")
    return chunks