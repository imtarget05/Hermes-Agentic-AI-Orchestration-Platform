"""Integration test for the async engine against a real RabbitMQ broker.

Skipped automatically when pika is not installed or no broker listens on
localhost:5672 (or HERMES_RABBITMQ_URL). Run the stack with `docker compose up
rabbitmq` to validate the real broker path: manual ACK, exchange/queue
binding, publish/get, retry queue (TTL+DLX).
"""
from __future__ import annotations

import os
import socket

import pytest


def _broker_available():
    url = os.environ.get("HERMES_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")
    try:
        import pika  # noqa
    except Exception:
        return False, "pika not installed"
    host = "localhost"
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or "localhost"
        with socket.create_connection((host, 5672), timeout=1.5):
            return True, ""
    except Exception as e:
        return False, f"no broker reachable: {e}"


_AVAILABLE, _REASON = _broker_available()


@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
def test_rabbitmq_publish_get_manual_ack(tmp_path):
    from hermes.async_engine.backends import RabbitMQBus
    from hermes.async_engine.contract import ROUTING, Task

    bus = RabbitMQBus(os.environ.get("HERMES_RABBITMQ_URL", ""))
    try:
        ex, rk, q = ROUTING["research"]
        bus.declare(ex, rk, q)
        task = Task(task_id="it-1", workflow_id="it", task_type="research", payload={"x": 1})
        bus.publish(ex, rk, task.to_message())
        delivery = bus.get(q)
        assert delivery is not None
        assert delivery.message["task_id"] == "it-1"
        assert delivery.message["payload"] == {"x": 1}
        delivery.ack()
    finally:
        bus.close()


@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
def test_rabbitmq_full_worker_roundtrip(tmp_path):
    from hermes.async_engine.backends import RabbitMQBus
    from hermes.async_engine.contract import ROUTING, Task
    from hermes.async_engine.eventbus import InMemoryEventBus
    from hermes.async_engine.metrics import NoopMetrics
    from hermes.async_engine.store import AsyncTaskStore
    from hermes.async_engine.worker import Worker

    bus = RabbitMQBus(os.environ.get("HERMES_RABBITMQ_URL", ""))
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    events = InMemoryEventBus()
    try:
        ex, rk, q = ROUTING["research"]
        bus.declare(ex, rk, q)
        task = Task(task_id="it-2", workflow_id="it", task_type="research")
        store.create_task(task)
        bus.publish(ex, rk, task.to_message())

        w = Worker("it-worker", "research", lambda t: "s3://it/2",
                   store, bus, events=events, metrics=NoopMetrics())
        while w.pump_once() == 0:
            import time
            time.sleep(0.1)
        assert store.is_completed("it-2") is True
        assert store.task_results("it-2")[0]["result_uri"] == "s3://it/2"
    finally:
        bus.close()


@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
def test_rabbitmq_retry_queue_requeues_after_ttl(tmp_path):
    from hermes.async_engine.backends import RabbitMQBus

    bus = RabbitMQBus(os.environ.get("HERMES_RABBITMQ_URL", ""))
    try:
        bus.declare("hermes.tasks", "agent.analyze", "q.agent.analyze")
        # publish a message and requeue it with a short delay; it should come
        # back via the TTL+DLX retry queue.
        bus.publish("hermes.tasks", "agent.analyze", {"task_type": "analyze", "attempt": 1})
        raw = bus.get("q.agent.analyze")
        assert raw is not None
        bus.requeue("q.agent.analyze", raw.message, delay_seconds=0.3)
        import time
        time.sleep(0.6)
        again = bus.get("q.agent.analyze")
        assert again is not None
        again.ack()
    finally:
        bus.close()