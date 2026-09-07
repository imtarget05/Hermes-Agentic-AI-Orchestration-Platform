"""Scraper fetchers: HTTP, PDF URL, and vendor catalog extraction.

All fetchers return a ``RawSource`` compatible with the existing ingestion
pipeline. ``WebFetcher`` and ``PdfUrlFetcher`` respect ``ScrapePolicy``
(domain allow/block lists + TTL-based dedup skip via ``ScrapedDocStore``).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

from ..ingestion.htmlparse import html_is_probably_dynamic_shell, parse_semantic_html
from ..ingestion.model import RawSource
from .contract import ScrapeSource
from .policy import RateLimiter, ScrapePolicy


class BaseFetcher(ABC):
    source_type: ScrapeSource = ScrapeSource.WEB

    @abstractmethod
    def fetch(self, key: str, policy: ScrapePolicy) -> RawSource | None:
        ...


class WebFetcher(BaseFetcher):
    source_type = ScrapeSource.WEB

    def __init__(self, timeout_s: float = 15.0, rate_limiter: RateLimiter | None = None):
        self.timeout_s = timeout_s
        self.rate_limiter = rate_limiter

    def fetch(self, key: str, policy: ScrapePolicy) -> RawSource | None:
        try:
            import httpx  # guarded: optional extra

            url = key if key.startswith("http") else f"https://www.google.com/search?q={key}"
            domain = urlparse(url).netloc.lower()
            if not policy.domain_allowed(domain):
                return None
            if self.rate_limiter is not None:
                self.rate_limiter.acquire(domain)
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "HermesScraper/1.0"})
                resp.raise_for_status()
                text = _html_to_text(resp.text)
                sem = parse_semantic_html(resp.text)
                metadata = {"scraped_from": key, "domain": domain}
                if sem.title.strip():
                    metadata["title"] = sem.title.strip()
                for k, v in sem.meta.items():
                    metadata.setdefault(f"meta:{k}", v)
                dynamic = html_is_probably_dynamic_shell(resp.text)
                return RawSource(
                    source_uri=url,
                    doc_id=f"scrape-{_safe_id(url)}",
                    kind="html",
                    text=text,
                    html=resp.text,
                    metadata=metadata,
                    dynamic=dynamic,
                )
        except Exception:
            return None


class PdfUrlFetcher(BaseFetcher):
    source_type = ScrapeSource.PDF

    def __init__(self, timeout_s: float = 30.0, rate_limiter: RateLimiter | None = None):
        self.timeout_s = timeout_s
        self.rate_limiter = rate_limiter

    def fetch(self, key: str, policy: ScrapePolicy) -> RawSource | None:
        try:
            import httpx  # guarded: optional extra

            url = key if key.startswith("http") else ""
            if not url or not url.lower().endswith(".pdf"):
                return None
            domain = urlparse(url).netloc.lower()
            if not policy.domain_allowed(domain):
                return None
            if self.rate_limiter is not None:
                self.rate_limiter.acquire(domain)
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "HermesScraper/1.0"})
                resp.raise_for_status()
                try:
                    import io

                    from pypdf import PdfReader  # optional dependency

                    reader = PdfReader(io.BytesIO(resp.content))
                    pages = [p.extract_text() or "" for p in reader.pages]
                    text = "\n\n".join(pages)
                except Exception:
                    text = ""
                return RawSource(
                    source_uri=url,
                    doc_id=f"scrape-pdf-{_safe_id(url)}",
                    kind="pdf",
                    text=text,
                    metadata={"scraped_from": key, "domain": domain},
                )
        except Exception:
            return None


def _safe_id(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower())[:40].strip("-")


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()
