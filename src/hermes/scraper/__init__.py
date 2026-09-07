"""Scraper service — event-driven document fetcher that pipes through the
existing ingestion pipeline into RagIndex.

Triggered by procurement task creation via the async message bus.
Reuses the canonical ingestion pipeline so scraped content is chunked,
normalized, deduplicated, and validated exactly like locally-uploaded documents.

Architecture:
    procurement task created
        → async_engine emits task.created event
        → scraper_worker picks up scrape task from bus
        → fetcher produces RawSource
        → ingestion.pipeline.run(raw) → ProcessedDocument
        → ScrapedDocStore saves chunks into RagIndex with scraper metadata
"""
from __future__ import annotations

from .contract import ScrapeResult, ScrapeSource, ScrapeTask
from .fetcher import BaseFetcher, PdfUrlFetcher, WebFetcher
from .pipeline import run_scrape
from .policy import ScrapePolicy

__all__ = [
    "BaseFetcher",
    "PdfUrlFetcher",
    "ScrapePolicy",
    "ScrapeResult",
    "ScrapeSource",
    "ScrapeTask",
    "WebFetcher",
    "run_scrape",
]
