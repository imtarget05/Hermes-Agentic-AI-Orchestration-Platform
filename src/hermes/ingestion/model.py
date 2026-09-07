"""Canonical document representation between extraction and downstream stages.

`RawSource` is whatever a fetcher/parser produced (text, HTML, a parsed dict,
PDF-extracted text, a JS shell). Extractors map it to a `CanonicalDocument` —
a domain-neutral structure of tagged `ContentBlock`s + metadata. The chunker,
embedder and vector store only ever read the canonical form, so adding a new
source type never requires touching retrieval code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .normalize import normalize_text


@dataclass
class RawSource:
    """What a fetcher produced, before any extraction."""

    source_uri: str = ""
    doc_id: str = ""
    kind: str = "text"  # text | html | dict | json | pdf | bytes
    text: str = ""  # visible text captured (from fetcher / parser)
    html: str = ""
    payload: Any = None  # structured dict / parsed object
    metadata: dict[str, Any] = field(default_factory=dict)
    dynamic: bool = False  # fetcher signalled only a JS shell / client-rendered

    @property
    def rendered(self) -> bool:
        """True when static content is available (not only a client-rendered shell)."""
        if self.dynamic:
            return False
        return bool((self.text or "").strip()) or self.payload is not None


@dataclass
class ContentBlock:
    """One unit of canonical content, tagged with its provenance extractor."""

    source: str  # provenance strategy: structured | metadata | visible | fallback
    kind: str = "text"  # text | field | list | raw
    heading: str = ""  # human label (e.g. "Currency", "Legal Entity", "Terms")
    text: str = ""

    def searchable(self) -> str:
        if self.heading:
            return f"{self.heading}: {self.text}"
        return self.text


@dataclass
class CanonicalDocument:
    """Vendor/domain-neutral representation fed to norm/dedupe/validate/chunk."""

    source_uri: str
    doc_id: str = ""
    blocks: list[ContentBlock] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    strategy_evidence: list[str] = field(default_factory=list)
    dynamic: bool = False

    def add_block(self, block: ContentBlock) -> None:
        self.blocks.append(block)

    def block_count(self) -> int:
        return len(self.blocks)

    def to_text(self, *, normalized: bool = False) -> str:
        parts = [b.searchable() for b in self.blocks if (b.text or "").strip()]
        joined = "\n".join(parts)
        return normalize_text(joined) if normalized else joined

    def text_len(self) -> int:
        return len(self.to_text())