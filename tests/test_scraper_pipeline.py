"""Scraper pipeline tests: run_scrape with mock fetcher → RagIndex storage."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes.ingestion.model import RawSource
from hermes.scraper.contract import ScrapeSource, ScrapeTask
from hermes.scraper.db import ScrapedDocStore
from hermes.scraper.fetcher import WebFetcher
from hermes.scraper.pipeline import run_scrape
from hermes.scraper.policy import ScrapePolicy


class FakeFetcher:
    source_type = ScrapeSource.WEB

    def fetch(self, key: str, policy: ScrapePolicy):
        domain = key.split("/")[2] if "://" in key else ""
        if not policy.domain_allowed(domain):
            return None
        if key == "blocked":
            return None
        return RawSource(
            source_uri=f"https://example.com/{key}",
            doc_id=f"doc-{key}",
            kind="html",
            text=f"Content for {key}",
            html=f"<html><body>Content for {key}</body></html>",
        )


def test_run_scrape_stores_chunks_in_index(tmp_path):
    task = ScrapeTask(task_id="t1", keys=["dell-xps"], source_type=ScrapeSource.WEB)
    store = ScrapedDocStore(rag_path=str(tmp_path / "idx.rag.json"), ttl_days=30)
    with patch.object(WebFetcher, "fetch", FakeFetcher.fetch):
        result = run_scrape(task, store=store, policy=ScrapePolicy())
    assert result.ok is True
    assert result.chunks_stored == 1
    assert len(store.index) == 1
    assert store.index.chunks[0].source_uri == "https://example.com/dell-xps"


def test_run_scrape_skips_blocked_domain():
    policy = ScrapePolicy(blocked_domains=["blocked.com"])
    task = ScrapeTask(task_id="t2", keys=["https://blocked.com/page"], source_type=ScrapeSource.WEB)
    store = ScrapedDocStore()
    with patch.object(WebFetcher, "fetch", FakeFetcher.fetch):
        result = run_scrape(task, store=store, policy=policy)
    assert result.chunks_stored == 0


def test_run_scrape_dedup_skips_existing_uri():
    task = ScrapeTask(task_id="t3", keys=["dell-xps"], source_type=ScrapeSource.WEB)
    store = ScrapedDocStore()
    store.add("a", "https://example.com/dell-xps", "old content", ttl_days=30)
    with patch.object(WebFetcher, "fetch", FakeFetcher.fetch):
        result = run_scrape(task, store=store, policy=ScrapePolicy())
    assert result.chunks_stored == 0


def test_run_scrape_respects_rate_limit(tmp_path):
    task = ScrapeTask(task_id="t1", keys=["https://example.com/page"], source_type=ScrapeSource.WEB)
    store = ScrapedDocStore(rag_path=str(tmp_path / "idx.rag.json"), ttl_days=30)
    mock_limiter = MagicMock()
    captured = {}
    original_init = WebFetcher.__init__

    def capturing_init(self, *args, **kwargs):
        captured.update(kwargs)
        return original_init(self, *args, **kwargs)

    with patch.object(WebFetcher, "__init__", capturing_init):
        with patch.object(WebFetcher, "fetch", FakeFetcher.fetch):
            result = run_scrape(task, store=store, policy=ScrapePolicy(), rate_limiter=mock_limiter)
    assert captured.get("rate_limiter") is mock_limiter
    assert result.chunks_stored == 1


def test_scraped_doc_store_evict_stale():
    store = ScrapedDocStore(ttl_days=30)
    store.add("d1", "uri://1", "fresh", scraped_at="2026-09-05T00:00:00+00:00", ttl_days=30)
    store.add("d2", "uri://2", "stale", scraped_at="2020-01-01T00:00:00+00:00", ttl_days=30)
    stale = store.evict_stale(ttl_days=30)
    assert len(stale) == 1
    assert stale[0].doc_id == "d2"
    assert len(store._chunks) == 1


def test_scraped_doc_store_evict_keeps_recent():
    store = ScrapedDocStore(ttl_days=30)
    store.add("d1", "uri://1", "recent", scraped_at="2026-09-01T00:00:00+00:00", ttl_days=30)
    store.add("d2", "uri://2", "old", scraped_at="2020-01-01T00:00:00+00:00", ttl_days=30)
    stale = store.evict_stale(ttl_days=30)
    assert len(stale) == 1
    assert stale[0].doc_id == "d2"
    assert len(store._chunks) == 1
    assert store._chunks[0].doc_id == "d1"


def test_scraped_doc_store_find_by_source():
    store = ScrapedDocStore()
    store.add("d1", "uri://a", "content a")
    store.add("d2", "uri://b", "content b")
    found = store.find_by_source("uri://a")
    assert len(found) == 1
    assert found[0].doc_id == "d1"


def test_scraped_doc_store_roundtrip_json(tmp_path):
    p = str(tmp_path / "store.json")
    store = ScrapedDocStore(rag_path=p, ttl_days=30)
    store.add("d1", "uri://x", "hello world", source_type="web",
              task_id="task-1", scraped_at="2024-06-01T00:00:00+00:00", ttl_days=30)
    store.save(p)
    loaded = ScrapedDocStore(rag_path=p, ttl_days=30)
    assert len(loaded._chunks) == 1
    assert loaded._chunks[0].source_type == "web"
    assert loaded._chunks[0].task_id == "task-1"


def test_run_scrape_preserves_html_semantic_metadata(tmp_path):
    """Regression: HTML metadata (title/meta/JSON-LD) present in the static
    response must survive ingestion into the stored vector-store payload."""
    def html_fetch(self, key, policy):
        domain = key.split("/")[2]
        if not policy.domain_allowed(domain):
            return None
        html = ("<html><head><title>Server 2024 Blade</title>"
                "<meta name='vendor' content='Acme Corp'>"
                "<meta property='og:price:amount' content='1899.00'>"
                "<script type='application/ld+json'>"
                '{"@type":"Product","weight":"1.8 kg"}'
                "</script></head><body><p>Premium blade server.</p></body></html>")
        return RawSource(source_uri=key, doc_id="doc-html", kind="html",
                         text="Premium blade server.", html=html,
                         metadata={"domain": domain})

    task = ScrapeTask(task_id="t-html", keys=["https://acme.com/server"],
                      source_type=ScrapeSource.WEB)
    store = ScrapedDocStore(rag_path=str(tmp_path / "idx.rag.json"), ttl_days=30)
    with patch.object(WebFetcher, "fetch", html_fetch):
        result = run_scrape(task, store=store, policy=ScrapePolicy())
    assert result.ok is True
    assert result.chunks_stored == 1
    stored = store._chunks[0].text
    for probe in ("Server 2024 Blade", "Acme Corp", "1899.00", "1.8 kg"):
        assert probe.lower() in stored.lower(), f"metadata {probe!r} lost in storage"
