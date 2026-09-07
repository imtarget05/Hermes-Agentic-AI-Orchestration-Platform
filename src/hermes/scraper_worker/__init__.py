"""Scraper worker — subscribes to scrape.* routing keys and runs scrape tasks.

Entry point: ``python -m hermes.scraper_worker`` or ``hermes-scraper`` console
script. Reads ``HERMES_SCRAPER_POLICY`` env var for policy config.
"""
from __future__ import annotations

import os
import signal
import time
from typing import Any

from ..async_engine.backends import InMemoryBus
from ..async_engine.contract import ROUTING
from ..scraper.contract import ScrapeResult, ScrapeTask
from ..scraper.db import ScrapedDocStore
from ..scraper.pipeline import run_scrape
from ..scraper.policy import ScrapePolicy

_SCRAPE_TASK_TYPES = {t for t in ROUTING if t == "scrape"}


class ScraperWorker:
    def __init__(self, bus: Any | None = None, policy: ScrapePolicy | None = None,
                 rag_path: str = "") -> None:
        self.bus = bus or InMemoryBus()
        self.policy = policy or ScrapePolicy.from_env()
        self.rag_path = rag_path or os.environ.get("HERMES_RAG_INDEX", "")
        self._running = True
        self._setup_bus()

    def _setup_bus(self) -> None:
        try:
            exchange, routing_key, queue = ROUTING["scrape"]
            self.bus.declare(exchange, routing_key, queue)
            self._queue = queue
        except (KeyError, Exception):
            self._queue = "q.agent.scrape"

    def _extract_task(self, msg: dict[str, Any]) -> ScrapeTask | None:
        try:
            payload = msg.get("payload", msg)
            return ScrapeTask(**payload)
        except Exception:
            return None

    def _ensure_store(self) -> ScrapedDocStore:
        return ScrapedDocStore(rag_path=self.rag_path, ttl_days=self.policy.ttl_days)

    def run_once(self) -> ScrapeResult | None:
        delivery = self.bus.get(self._queue)
        if delivery is None:
            return None
        task = self._extract_task(delivery.message)
        if task is None:
            try:
                delivery.ack()
            except Exception:
                pass
            return None
        try:
            store = self._ensure_store()
            result = run_scrape(task, store=store, policy=self.policy)
            try:
                delivery.ack()
            except Exception:
                pass
            return result
        except Exception as exc:
            try:
                delivery.nack(requeue=True)
            except Exception:
                pass
            return ScrapeResult(task_id=task.task_id, ok=False, error=str(exc))

    def run_forever(self, poll_interval: float = 1.0) -> None:
        def _stop(*_: Any) -> None:
            self._running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        while self._running:
            self.run_once()
            time.sleep(poll_interval)


def main() -> None:
    policy = ScrapePolicy.from_env()
    rag_path = os.environ.get("HERMES_RAG_INDEX", "")
    bus = InMemoryBus()
    worker = ScraperWorker(bus=bus, policy=policy, rag_path=rag_path)
    worker.run_forever()


if __name__ == "__main__":
    main()
