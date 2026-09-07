"""Scraper pipeline: fetcher → ingestion → RagIndex storage.

Reuses the existing ``IngestionPipeline`` so scraped documents are chunked,
normalized, deduplicated, and validated exactly like locally-uploaded content.
"""
from __future__ import annotations

from datetime import UTC, datetime

from ..ingestion.pipeline import IngestionPipeline
from .contract import ScrapeResult, ScrapeSource, ScrapeTask
from .db import ScrapedDocStore
from .fetcher import BaseFetcher, PdfUrlFetcher, WebFetcher
from .policy import RateLimiter, ScrapePolicy

_FETCHERS: dict[ScrapeSource, type[BaseFetcher]] = {
    ScrapeSource.WEB: WebFetcher,
    ScrapeSource.PDF: PdfUrlFetcher,
    ScrapeSource.VENDOR_CATALOG: WebFetcher,
}


def run_scrape(task: ScrapeTask, store: ScrapedDocStore | None = None,
               policy: ScrapePolicy | None = None,
               rate_limiter: RateLimiter | None = None) -> ScrapeResult:
    pipe = IngestionPipeline()
    store = store or ScrapedDocStore(ttl_days=policy.ttl_days if policy else 30)
    policy = policy or ScrapePolicy()
    rate_limiter = rate_limiter or RateLimiter(rate_per_sec=policy.rate_limit_per_domain)

    chunks_stored = 0
    uris: list[str] = []
    fetcher_cls = _FETCHERS.get(task.source_type, WebFetcher)
    fetcher = fetcher_cls(rate_limiter=rate_limiter)

    now = datetime.now(UTC).isoformat()

    for key in task.keys:
        raw = fetcher.fetch(key, policy)
        if raw is None:
            continue

        dup_uri = raw.source_uri
        if store.find_by_source(dup_uri):
            continue

        proc = pipe.process(raw)
        indexed = proc.indexed_text()
        if not indexed.strip():
            continue

        doc_id = raw.doc_id or f"scrape-{_safe_doc_id(dup_uri)}"
        store.add(
            doc_id=doc_id,
            source_uri=dup_uri,
            text=indexed,
            source_type=task.source_type.value,
            task_id=task.task_id,
            scraped_at=now,
            ttl_days=policy.ttl_days,
        )
        chunks_stored += 1
        uris.append(dup_uri)

    return ScrapeResult(
        task_id=task.task_id,
        chunks_stored=chunks_stored,
        uris=uris,
        source_type=task.source_type,
    )


def _safe_doc_id(uri: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", uri.lower())[:40].strip("-")
