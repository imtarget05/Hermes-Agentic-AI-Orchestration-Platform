"""Ingestion pipeline — source preservation → multi-strategy extraction →
canonical document → normalization → deduplication → quality validation →
chunking.

This package implements the architectural fix for information loss during
ingestion:

    Fetcher → RawSource → Extractor(s) → CanonicalDocument
        → Normalizer → Deduplicator → QualityValidator → Chunker
        → (Embedder) → Vector Store

The key invariant: extraction must *maximize* preservation of valuable
information into a vendor/domain-neutral canonical representation *before*
chunking. Nothing downstream (chunker / embedder / vector store) should know
about any specific source type.

Extractors are general-purpose and pluggable — no domain, website, product,
field, vendor-format, or single-record regex is hard-coded. A new source type
adds a new extractor (or a browser renderer for dynamic content) without
touching the rest of the pipeline.
"""
from __future__ import annotations

from .chunk import chunk_document
from .dedupe import deduplicate
from .extractors import (
    DEFAULT_EXTRACTORS,
    BaseExtractor,
    HtmlSemanticExtractor,
    build_canonical,
)
from .htmlparse import (
    HtmlSemanticData,
    html_is_probably_dynamic_shell,
    parse_semantic_html,
)
from .model import CanonicalDocument, ContentBlock, RawSource
from .normalize import normalize_key, normalize_text
from .pipeline import IngestionPipeline, ProcessedDocument
from .quality import IngestionDiagnostics, QualityValidator, validate_canonical

__all__ = [
    "BaseExtractor",
    "CanonicalDocument",
    "ContentBlock",
    "DEFAULT_EXTRACTORS",
    "HtmlSemanticData",
    "HtmlSemanticExtractor",
    "IngestionDiagnostics",
    "IngestionPipeline",
    "ProcessedDocument",
    "QualityValidator",
    "RawSource",
    "build_canonical",
    "chunk_document",
    "deduplicate",
    "html_is_probably_dynamic_shell",
    "normalize_key",
    "normalize_text",
    "parse_semantic_html",
    "validate_canonical",
]