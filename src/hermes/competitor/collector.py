"""CompetitorCollector — fetch + classify raw web content into findings.

Injectable `fetcher(url) -> html` keeps it fully offline/testable. Content that
is fetched is the only evidence surfaced; nothing is fabricated.
"""
from __future__ import annotations

import re
from typing import Callable

from .schemas import CompetitorFinding, CompetitorTarget

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_LAUNCH_HINT = ("launch", "ra mắt", "ra mat", "announce", "giới thiệu", "new product")
_PRICING_HINT = ("price", "giá", "gia", "pricing", "starting at", "cost")


def strip_html(raw: str) -> str:
    return _WS.sub(" ", _HTML_TAG.sub(" ", raw or "")).strip()


def classify(text: str) -> str:
    low = text.lower()
    if any(k in low for k in _LAUNCH_HINT):
        return "launch"
    if any(k in low for k in _PRICING_HINT):
        return "pricing"
    if any(k in low for k in ("news", "thông báo", "announcement")):
        return "news"
    return "post"


def first_sentence(text: str, max_len: int = 180) -> str:
    text = text.strip()
    if not text:
        return text
    dot = text.find(". ")
    cut = dot if dot != -1 else len(text)
    cut = min(cut, max_len)
    return text[:cut].strip()


class CompetitorCollector:
    def __init__(self, fetcher: Callable[[str], str] | None = None):
        self._fetcher = fetcher or _http_fetcher

    def collect_target(self, target: CompetitorTarget) -> list[CompetitorFinding]:
        findings: list[CompetitorFinding] = []
        for url in (target.urls + target.feeds):
            try:
                raw = self._fetcher(url)
            except Exception:  # noqa: BLE001
                continue
            text = strip_html(raw)
            if not text:
                continue
            findings.append(CompetitorFinding(
                competitor=target.competitor, kind=classify(text),
                headline=first_sentence(text), summary=text[:300],
                source_uri=url, raw=text[:1200]))
        return findings

    def collect(self, targets: list[CompetitorTarget]) -> list[CompetitorFinding]:
        out: list[CompetitorFinding] = []
        for t in targets or []:
            if not t.enabled:
                continue
            out.extend(self.collect_target(t))
        return out


def _http_fetcher(url: str) -> str:
    import httpx
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    return resp.text