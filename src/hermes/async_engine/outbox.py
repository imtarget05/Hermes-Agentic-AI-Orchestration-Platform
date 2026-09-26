"""Transactional-outbox relay — drains outbox_events onto a live bus.

The outbox is the write half of the transactional-outbox pattern: an event row is
committed in the *same* transaction as the state change it describes (see
AsyncTaskStore.transaction), so an event can never be lost or claimed for
something that did not happen. This module is the read half. Without a relay the
outbox is write-only — rows accumulate and no event ever leaves the database.

One sweep (poll_once):

    SELECT unpublished rows (skipping backed-off and dead-lettered ones)
      -> publish each on the live bus
           success  -> mark_outbox_published
           failure  -> retryable and attempts left -> mark_outbox_retry (backoff)
                      otherwise                  -> mark_outbox_dead_lettered

Failure handling deliberately mirrors the worker (worker.py): the same
retry.classify_failure triage, the same RetryPolicy backoff schedule, the same
terminal dead-letter step, and a fatal failure for one event never stops the
sweep. Dead-lettered events are not deleted — the row stays, flagged with
attempts/last_error/dead_lettered_at, so an operator can see and replay it.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .contract import EVENT_FAILED
from .eventbus import emit_best_effort
from .retry import NonRetryableError, RetryPolicy, classify_failure


def _iso(ts: float) -> str:
    """Epoch seconds -> the ISO form the outbox stores timestamps in."""
    return datetime.fromtimestamp(ts, UTC).isoformat()


class OutboxRelay:
    """Polls the outbox and publishes to a live event bus.

    Args:
        store: AsyncTaskStore holding the outbox table.
        events: the live bus to publish on (any EventBus — Kafka, JSONL, ...).
        name: relay name, used in log lines and dead-letter envelopes.
        retry_policy: backoff schedule; defaults to the engine-wide policy.
        max_attempts: publish attempts before an event is dead-lettered.
        dead_letter_events: where terminal failures are announced. Defaults to
            the same bus, mirroring the worker announcing on its event bus.
        clock: epoch-seconds clock, injectable so tests can drive backoff.
    """

    def __init__(
        self,
        store,
        events,
        name: str = "outbox-relay",
        retry_policy: RetryPolicy | None = None,
        max_attempts: int = 3,
        dead_letter_events=None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.events = events
        self.name = name
        self.policy = retry_policy or RetryPolicy()
        self.max_attempts = max_attempts
        self.dead_letter_events = events if dead_letter_events is None else dead_letter_events
        self.clock = clock
        self.published = 0
        self.retried = 0
        self.dead_lettered = 0

    def poll_once(self, limit: int = 100) -> dict[str, Any]:
        """Run one sweep. Returns per-sweep counters; never raises for one event."""
        now = self.clock()
        rows = self.store.get_unpublished_outbox_events(limit=limit, now=_iso(now))
        published: list[int] = []
        retried: list[int] = []
        dead: list[int] = []

        for row in rows:
            try:
                self._publish(row)
            except Exception as e:
                # One unpublishable event must not stall the whole outbox.
                if self._record_failure(row, e, now):
                    dead.append(int(row["id"]))
                else:
                    retried.append(int(row["id"]))
                continue
            published.append(int(row["id"]))
            self.published += 1

        # Batch the bookkeeping: one UPDATE for the whole sweep.
        self.store.mark_outbox_published(published)
        return {
            "considered": len(rows),
            "published": published,
            "retried": retried,
            "dead_lettered": dead,
        }

    def run_forever(self, interval: float = 0.5, limit: int = 100,
                    stop_event: threading.Event | None = None) -> None:
        """Long-running relay loop (same shape as orchestrator advance_forever)."""
        stop = stop_event if stop_event is not None else threading.Event()
        while not stop.is_set():
            try:
                self.poll_once(limit=limit)
            except Exception as e:  # relay must survive transient store/bus issues
                print(f"[{self.name}] sweep error: {e}", flush=True)
            stop.wait(interval)

    # ---- internals ----
    def _publish(self, row: dict) -> None:
        """Publish one outbox row. Raises whatever the bus raises."""
        event = self._event_of(row)
        event_type = event.get("event_type") or row["event_type"]
        # Re-emitting through the bus's emit(event_type, **fields) keeps the
        # stored event_id/timestamp: make_event() builds the envelope first and
        # lets the fields override it, so the audit trail keeps one identity.
        self.events.emit(event_type, **{k: v for k, v in event.items() if k != "event_type"})

    @staticmethod
    def _event_of(row: dict) -> dict:
        """The event dict out of a stored payload.

        Accepts both the OutboxEventBus envelope ({"topic", "event"}) and a bare
        event object. An unreadable or malformed payload is permanent - retrying
        will never parse it - so it raises NonRetryableError and is
        dead-lettered for inspection instead of being published as an empty
        event.
        """
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (TypeError, ValueError) as e:
            raise NonRetryableError(f"malformed outbox payload: {e}") from e
        if not isinstance(payload, dict):
            raise NonRetryableError(
                f"malformed outbox payload: expected a JSON object, got {type(payload).__name__}")
        event = payload.get("event", payload)  # envelope, or a bare event row
        if not isinstance(event, dict):
            raise NonRetryableError("malformed outbox payload: 'event' is not an object")
        return event

    def _record_failure(self, row: dict, error: Exception, now: float) -> bool:
        """Retry with backoff, or dead-letter. Returns True if dead-lettered."""
        event_id = int(row["id"])
        attempts = int(row.get("attempts") or 0) + 1
        reason = f"{type(error).__name__}: {error}"
        retryable = classify_failure(error) or getattr(error, "retryable", False)

        if retryable and self.policy.should_retry(attempts, self.max_attempts):
            delay = self.policy.backoff_seconds(attempts, self.max_attempts)
            self.store.mark_outbox_retry(event_id, attempts, reason, _iso(now + delay))
            self.retried += 1
            print(f"[{self.name}] outbox {event_id} publish failed (attempt "
                  f"{attempts}), retrying in {delay}s: {reason}", flush=True)
            return False

        self.store.mark_outbox_dead_lettered(event_id, attempts, reason)
        self.dead_lettered += 1
        self._announce_dead_letter(row, attempts, reason)
        print(f"[{self.name}] outbox {event_id} dead-lettered after {attempts} "
              f"attempt(s): {reason}", flush=True)
        return True

    def _announce_dead_letter(self, row: dict, attempts: int, reason: str) -> None:
        """Announce a terminal publish failure on the dead-letter bus (best effort)."""
        payload = {}
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else {}
        except (TypeError, ValueError):
            payload = {}
        emit_best_effort(
            self.dead_letter_events, EVENT_FAILED,
            relay=self.name,
            outbox_event_id=int(row["id"]),
            outbox_event_type=row["event_type"],
            topic=payload.get("topic", ""),
            event_id=(payload.get("event") or {}).get("event_id", ""),
            attempts=attempts,
            error=reason[:300],
        )
