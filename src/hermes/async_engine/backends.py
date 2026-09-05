"""Message bus abstraction for work distribution.

- RabbitMQBus: real RabbitMQ via pika (optional; import guarded).
- InMemoryBus: thread-safe in-memory queues for tests / load tests and the
  no-broker run mode. It models the critical properties of RabbitMQ that the
  engine relies on:
    * manual ACK  (basic_get -> ack()/nack())
    * delivery is durable until acked (crash-safe requeue)
    * per-message delay for retry backoff (respects `retry_at`)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .contract import DEAD_LETTER_QUEUE, ROUTING


class MessageBus(Protocol):
    def declare(self, exchange: str, routing_key: str, queue: str) -> None: ...
    def publish(self, exchange: str, routing_key: str, message: dict[str, Any]) -> None: ...
    def get(self, queue: str) -> Delivery | None: ...
    def queue_depth(self, queue: str) -> int: ...


@dataclass
class Delivery:
    """A fetched message plus its ACK/NACK controls (manual_ack)."""

    message: dict[str, Any]
    delivery_tag: int
    _acked: bool = False
    _rejected: bool = False

    def ack(self) -> None:
        self._acked = True

    def nack(self, requeue: bool = True) -> None:
        self._rejected = True

    @property
    def task(self):
        return self.message


class InMemoryBus:
    """Thread-safe in-memory queues, mirroring RabbitMQ manual-ack semantics."""

    def __init__(self) -> None:
        self._queues: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._tag = 0

    def _ensure(self, queue: str) -> list[dict]:
        if queue not in self._queues:
            self._queues[queue] = []
        return self._queues[queue]

    def declare(self, exchange: str, routing_key: str, queue: str) -> None:
        with self._lock:
            self._ensure(queue)

    def publish(self, exchange: str, routing_key: str, message: dict[str, Any]) -> None:
        with self._lock:
            self._queues.setdefault(_queue_for(exchange, routing_key), []).append(
                {"payload": message.copy(), "publish_time": time.time()}
            )

    def get(self, queue: str) -> Delivery | None:
        with self._lock:
            q = self._ensure(queue)
            idx = _first_due(q)
            if idx is None:
                return None
            entry = q.pop(idx)
            delivery = Delivery(message=entry["payload"], delivery_tag=self._tag)
            self._tag += 1
            return delivery

    def requeue(self, queue: str, message: dict[str, Any], delay_seconds: float = 0.0) -> None:
        with self._lock:
            self._queues.setdefault(queue, []).append(
                {
                    "payload": message.copy(),
                    "publish_time": time.time(),
                    "retry_at": time.time() + max(0.0, delay_seconds),
                }
            )

    def queue_depth(self, queue: str) -> int:
        with self._lock:
            q = self._ensure(queue)
            return len([e for e in q if (e.get("retry_at") or 0) <= time.time() + 1e-9])

    def publish_to_queue(self, queue: str, message: dict[str, Any]) -> None:
        with self._lock:
            self._queues.setdefault(queue, []).append({"payload": message.copy(), "publish_time": time.time()})

    def publish_deadletter(self, message: dict[str, Any]) -> None:
        self.publish_to_queue(DEAD_LETTER_QUEUE, message)


def _queue_for(exchange: str, routing_key: str) -> str:
    for tt, (_ex, rk, q) in ROUTING.items():
        if routing_key == rk:
            return q
    return f"q.agent.{routing_key.split('.')[-1]}" if "." in routing_key else f"q.{routing_key}"


def _first_due(q: list[dict]):
    for i, e in enumerate(q):
        if (e.get("retry_at") or 0) <= time.time() + 1e-9:
            return i
    return None
class RabbitMQBus:
    """pika-backed RabbitMQ client (manual_ack, exchanges, queues, DLX).

    Requires `pika`; import is guarded so the package works without it.
    """

    def __init__(self, url: str = "amqp://guest:guest@localhost:5672/%2F",
                 exchanges: tuple[tuple[str, str, bool], ...] | None = None):
        self.url = url
        self.exchanges = exchanges or (
            ("hermes.tasks", "direct", True),
            ("hermes.retry", "direct", True),
            ("hermes.dlx", "fanout", True),
        )
        self._conn = None
        self._channel = None

    def _connect(self):
        import pika  # guarded import

        params = pika.URLParameters(self.url)
        self._conn = pika.BlockingConnection(params)
        self._channel = self._conn.channel()
        self._channel.confirm_delivery()
        for name, typ, durable in self.exchanges:
            self._channel.exchange_declare(exchange=name, exchange_type=typ, durable=durable)
        return self._channel

    def close(self) -> None:
        if self._conn and self._conn.is_open:
            self._conn.close()

    def declare(self, exchange: str, routing_key: str, queue: str) -> None:
        ch = self._channel or self._connect()
        ch.queue_declare(queue=queue, durable=True)
        ch.queue_bind(exchange=exchange, queue=queue, routing_key=routing_key)

    def publish(self, exchange: str, routing_key: str, message: dict[str, Any]) -> None:
        ch = self._channel or self._connect()
        import pika  # guarded import

        ch.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=_json_dumps(message),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            mandatory=True,
        )

    def get(self, queue: str) -> Delivery | None:
        ch = self._channel or self._connect()
        method, _props, body = ch.basic_get(queue=queue, auto_ack=False)  # manual_ack=True
        if method is None:
            return None

        delivery = Delivery(message=_json_loads(body), delivery_tag=method.delivery_tag)

        def _ack() -> None:
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                pass

        def _nack(requeue: bool = True) -> None:
            try:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=requeue)
            except Exception:
                pass

        delivery.ack = _ack  # type: ignore[assignment]
        delivery.nack = _nack  # type: ignore[assignment]
        return delivery

    def queue_depth(self, queue: str) -> int:
        ch = self._channel or self._connect()
        method = ch.queue_declare(queue=queue, durable=True, passive=True)
        return method.method.message_count

    def publish_deadletter(self, message: dict[str, Any]) -> None:
        self.publish(exchange="hermes.dlx", routing_key="dead.letter", message=message)

    def requeue(self, queue: str, message: dict[str, Any], delay_seconds: float = 0.0) -> None:
        """Re-publish with a TTL+DLX retry queue so the message is delivered back
        to `queue` after `delay_seconds` (canonical RabbitMQ backoff pattern)."""
        import pika  # guarded import

        ch = self._channel or self._connect()
        delay_ms = int(max(0.0, delay_seconds) * 1000)
        retry_queue = f"{queue}.retry"
        ch.queue_declare(
            queue=retry_queue, durable=True,
            arguments={
                "x-message-ttl": delay_ms,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": queue,
            },
        )
        ch.basic_publish(
            exchange="",
            routing_key=retry_queue,
            body=_json_dumps(message),
            properties=pika.BasicProperties(delivery_mode=2, expiration=str(delay_ms)),
        )


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)


def _json_loads(body) -> dict:
    import json

    return json.loads(body)