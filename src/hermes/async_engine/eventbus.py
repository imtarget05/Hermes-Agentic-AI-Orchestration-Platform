"""Lifecycle event bus — off the critical path (spec §10).

RabbitMQ says "execute this task"; Kafka says "what happened to this task?".
Topics: hermes.task.created/.started/.completed/.failed/.retried.

Provides:
- KafkaEventBus   (confluent-kafka; import guarded)
- JsonlEventBus   (append events to a JSONL file — portable, no broker)
- InMemoryEventBus (in-memory list for tests)
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .contract import (
    EVENT_COMPLETED,
    EVENT_CREATED,
    EVENT_FAILED,
    EVENT_RETRIED,
    EVENT_STARTED,
)

EVENT_TOPICS = {
    EVENT_CREATED: "hermes.task.created",
    EVENT_STARTED: "hermes.task.started",
    EVENT_COMPLETED: "hermes.task.completed",
    EVENT_FAILED: "hermes.task.failed",
    EVENT_RETRIED: "hermes.task.retried",
}


class EventBus(Protocol):
    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]: ...


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    return {
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        **fields,
    }


class InMemoryEventBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        ev = make_event(event_type, **fields)
        self.events.append(ev)
        return ev

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]


class JsonlEventBus:
    """Append-only JSONL event log — zero broker dependencies."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        ev = make_event(event_type, **fields)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, default=str) + "\n")
        return ev

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


class KafkaEventBus:
    """confluent-kafka producer. Import guarded — works without the broker.

    Delivery is fire-and-forget (best-effort) so Kafka stays off the
    critical execution path.
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092", **producer_cfg: Any):
        self._bootstrap = bootstrap_servers
        self._cfg = producer_cfg
        self._producer = None
        self._topics = dict(EVENT_TOPICS)

    def _connect(self):
        if self._producer is None:
            from confluent_kafka import Producer  # guarded import

            cfg = {**self._cfg, "bootstrap.servers": self._bootstrap}
            self._producer = Producer(cfg)
        return self._producer

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        ev = make_event(event_type, **fields)
        topic = self._topics.get(event_type, "hermes.task.events")
        try:
            producer = self._connect()
            producer.produce(topic, key=str(fields.get("task_id", "")),
                             value=json.dumps(ev, default=str).encode("utf-8"))
            producer.poll(0)
        except Exception:  # Kafka is off the critical path — never block delivery
            pass
        return ev

    def flush(self, timeout: float = 1.0) -> None:
        if self._producer is not None:
            try:
                self._producer.flush(timeout)
            except Exception:
                pass