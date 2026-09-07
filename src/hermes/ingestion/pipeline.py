"""End-to-end ingestion pipeline orchestration.

    RawSource
        → build_canonical         (multi-strategy extraction, fallback-safe)
        → normalize               (whitespace / label canonicalization)
        → deduplicate             (drop exact duplicates, keep conflicts)
        → QualityValidator        (pre-embedding quality + diagnostics)
        → chunk_document          (bounded chunks preserving headings)

Extension points:
- ``add_extractor(extractor)`` — register a new general-purpose strategy.
- ``add_renderer(renderer)`` — register a future browser-renderer fallback for
  dynamic/client-rendered content (detected, not faked, today).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .chunk import chunk_document
from .dedupe import deduplicate
from .extractors import DEFAULT_EXTRACTORS, BaseExtractor, build_canonical
from .model import CanonicalDocument, ContentBlock, RawSource
from .normalize import normalize_text
from .quality import IngestionDiagnostics, QualityValidator, StageMetric


class Renderer:
    """Base class for a future browser renderer (dynamic-content strategy).

    Today the pipeline only *detects* client-rendered sources and keeps a safe
    placeholder; a real renderer would execute JS and return rendered text.
    """

    name = "renderer"
    enabled = False

    def render(self, raw: RawSource) -> RawSource:
        return raw


@dataclass
class ProcessedDocument:
    canonical: CanonicalDocument
    normalized: CanonicalDocument
    deduped: CanonicalDocument
    diagnostics: IngestionDiagnostics
    chunks: list[str] = field(default_factory=list)

    def indexed_text(self) -> str:
        """Deduplicated, normalized canonical text for the retrieval corpus."""
        return normalize_text(self.deduped.to_text())


class IngestionPipeline:
    """Run source documents through the ingestion pipeline with diagnostics."""

    def __init__(self,
                 extractors: list[BaseExtractor] | None = None,
                 max_chunk_words: int = 200,
                 min_content_chars: int = 20):
        self.extractors = list(extractors) if extractors else list(DEFAULT_EXTRACTORS)
        self.max_chunk_words = max_chunk_words
        self.validator = QualityValidator(min_content_chars=min_content_chars)
        self.renderers: list[Renderer] = []

    # ---- extension points ------------------------------------------------- #

    def add_extractor(self, extractor: BaseExtractor) -> None:
        """Register an additional general-purpose extraction strategy."""
        self.extractors.append(extractor)

    def add_renderer(self, renderer: Renderer) -> None:
        """Register a browser renderer for dynamic/client-rendered content."""
        self.renderers.append(renderer)

    def _pre_render(self, raw: RawSource) -> RawSource:
        """If raw looks dynamic, try a registered renderer before extraction."""
        if raw.rendered:
            return raw
        for renderer in self.renderers:
            if renderer.enabled:
                try:
                    rendered = renderer.render(raw)
                    if rendered and rendered.rendered:
                        return rendered
                except Exception:
                    continue
        return raw

    # ---- stages ----------------------------------------------------------- #

    def process(self, raw: RawSource) -> ProcessedDocument:
        raw = self._pre_render(raw)

        canonical = build_canonical(raw, self.extractors)

        normalized = CanonicalDocument(
            canonical.source_uri, canonical.doc_id,
            blocks=[ContentBlock(source=b.source, kind=b.kind, heading=b.heading,
                                 text=normalize_text(b.text)) for b in canonical.blocks],
            metadata=dict(canonical.metadata),
            strategy_evidence=list(canonical.strategy_evidence),
            dynamic=canonical.dynamic)

        deduped = deduplicate(normalized)

        diagnostics = self.validator.validate(canonical, raw)

        chunks = chunk_document(deduped, self.max_chunk_words)
        metrics = diagnostics.stages
        metrics.setdefault("chunked", StageMetric("chunked", ok=True, chars=0))
        metrics["deduped"].chars = len(deduped.to_text(normalized=True))
        metrics["deduped"].blocks = deduped.block_count()
        metrics["chunked"].chars = sum(len(c) for c in chunks)
        metrics["chunked"].blocks = len(chunks)
        metrics["chunked"].ok = bool(chunks)

        return ProcessedDocument(canonical, normalized, deduped, diagnostics, chunks)