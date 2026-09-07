"""Scraper policy: rate limits, TTL, allowed/blocked domains, concurrency.

Reads from ``HERMES_SCRAPER_POLICY`` env var (YAML path) with sane defaults.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

DEFAULT_POLICY: dict[str, Any] = {
    "rate_limit_per_domain": 2,
    "ttl_days": 30,
    "allowed_domains": [],
    "blocked_domains": [],
    "max_concurrent": 5,
    "dedup_by": "content_hash",
}


@dataclass
class ScrapePolicy:
    rate_limit_per_domain: float = 2.0
    ttl_days: int = 30
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    max_concurrent: int = 5
    dedup_by: str = "content_hash"

    @classmethod
    def from_env(cls, path: str = "") -> "ScrapePolicy":
        raw = dict(DEFAULT_POLICY)
        env_path = path or os.environ.get("HERMES_SCRAPER_POLICY", "")
        if env_path:
            try:
                p = Path(env_path)
                if p.exists() and _HAS_YAML:
                    raw.update(yaml.safe_load(p.read_text()) or {})
            except Exception:
                pass
        return cls(**{k: raw[k] for k in raw if k in cls.__dataclass_fields__})

    def domain_allowed(self, domain: str) -> bool:
        if self.blocked_domains and domain in self.blocked_domains:
            return False
        if not self.allowed_domains:
            return True
        return domain in self.allowed_domains


@dataclass
class RateLimiter:
    rate_per_sec: float = 2.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last: dict[str, float] = field(default_factory=dict, repr=False)

    def acquire(self, domain: str) -> None:
        min_interval = 1.0 / self.rate_per_sec
        with self._lock:
            now = time.monotonic()
            if domain not in self._last:
                self._last[domain] = now
                return
            last = self._last[domain]
            wait = min_interval - (now - last)
            if wait > 0:
                time.sleep(wait)
            self._last[domain] = time.monotonic()
