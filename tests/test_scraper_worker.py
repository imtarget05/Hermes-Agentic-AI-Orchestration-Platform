"""Scraper worker tests: message handling, ack/nack, result, rate limiter."""
from __future__ import annotations

from unittest.mock import patch

from hermes.async_engine.backends import InMemoryBus
from hermes.scraper.contract import ScrapeSource, ScrapeTask
from hermes.scraper.policy import ScrapePolicy
from hermes.scraper_worker import ScraperWorker


def test_worker_processes_scrape_task(tmp_path):
    bus = InMemoryBus()
    task = ScrapeTask(task_id="w1", keys=["laptop-dell"], source_type=ScrapeSource.WEB)
    ex, rk, q = ("hermes.tasks", "agent.scrape", "q.agent.scrape")
    bus.declare(ex, rk, q)
    bus.publish(ex, rk, task.model_dump())
    worker = ScraperWorker(bus=bus, policy=ScrapePolicy(), rag_path=str(tmp_path / "idx.json"))
    result = worker.run_once()
    assert result is not None
    assert result.task_id == "w1"


def test_worker_acks_successful_task(tmp_path):
    bus = InMemoryBus()
    task = ScrapeTask(task_id="w2", keys=["laptop-dell"], source_type=ScrapeSource.WEB)
    ex, rk, q = ("hermes.tasks", "agent.scrape", "q.agent.scrape")
    bus.declare(ex, rk, q)
    bus.publish(ex, rk, task.model_dump())
    worker = ScraperWorker(bus=bus, policy=ScrapePolicy(), rag_path=str(tmp_path / "idx.json"))
    result = worker.run_once()
    assert result is not None
    assert bus.queue_depth(q) == 0


def test_worker_nacks_on_bad_payload():
    bus = InMemoryBus()
    ex, rk, q = ("hermes.tasks", "agent.scrape", "q.agent.scrape")
    bus.declare(ex, rk, q)
    bus.publish(ex, rk, {"not_a_task": True})
    worker = ScraperWorker(bus=bus, policy=ScrapePolicy(), rag_path="")
    result = worker.run_once()
    assert result is None


def test_worker_uses_rate_limiter(tmp_path):
    bus = InMemoryBus()
    task = ScrapeTask(task_id="w1", keys=["laptop-dell"], source_type=ScrapeSource.WEB)
    ex, rk, q = ("hermes.tasks", "agent.scrape", "q.agent.scrape")
    bus.declare(ex, rk, q)
    bus.publish(ex, rk, task.model_dump())
    with patch("hermes.scraper.pipeline.RateLimiter") as mock_rl_cls:
        worker = ScraperWorker(bus=bus, policy=ScrapePolicy(), rag_path=str(tmp_path / "idx.json"))
        result = worker.run_once()
    mock_rl_cls.assert_called_once_with(rate_per_sec=2.0)
    assert result is not None
