"""Transactional outbox: write, relay, retry, dead-letter.

Regression cover for the defects that made the outbox unusable:
  * OutboxEventBus.emit() raised ImportError on every call (it imported
    EVENT_TOPICS from contract.py, where the name did not exist), so nothing
    was ever written to the outbox table;
  * nothing ever read the table back (no caller of get_unpublished_outbox_events
    or mark_outbox_published), so the outbox was write-only;
  * _exec() committed per statement, so write_outbox_event()'s docstring
    claiming the event is written "within the current transaction" was false.
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from hermes.async_engine.contract import EVENT_COMPLETED, EVENT_CREATED, EVENT_TOPICS
from hermes.async_engine.eventbus import (
    EVENT_TOPIC_DEFAULT,
    InMemoryEventBus,
    OutboxEventBus,
)
from hermes.async_engine.outbox import OutboxRelay
from hermes.async_engine.retry import RetryableError
from hermes.async_engine.store import AsyncTaskStore


@pytest.fixture
def store(tmp_path):
    return AsyncTaskStore(str(tmp_path / "async.db"))


def raw_rows(db_path):
    """Read outbox_events straight from SQLite - no store API in the way."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM outbox_events ORDER BY id")]
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 1. the ImportError regression
# --------------------------------------------------------------------------- #
def test_outbox_emit_writes_a_row_and_never_raises_import_error(store):
    """The exact call that raised ImportError on every invocation."""
    bus = OutboxEventBus(store)

    ev = bus.emit(EVENT_CREATED, task_id="t-1", workflow_id="wf-1")

    assert ev["event_type"] == EVENT_CREATED
    rows = raw_rows(store.db_path)
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_CREATED
    assert rows[0]["published_at"] is None


def test_event_topics_contract_is_importable_and_shared():
    """EVENT_TOPICS is a real contract symbol, not a per-module copy."""
    from hermes.async_engine import contract

    assert contract.EVENT_TOPICS[EVENT_CREATED] == "hermes.task.created"
    assert contract.EVENT_TOPICS[EVENT_COMPLETED] == "hermes.task.completed"
    # the bus module uses the contract's mapping, not a private duplicate
    from hermes.async_engine import eventbus

    assert eventbus.EVENT_TOPICS is contract.EVENT_TOPICS


def test_outbox_payload_carries_contract_topic(store):
    bus = OutboxEventBus(store)
    bus.emit(EVENT_CREATED, task_id="t-1")
    payload = json.loads(raw_rows(store.db_path)[0]["payload"])

    assert payload["topic"] == EVENT_TOPICS[EVENT_CREATED]
    assert payload["event"]["event_type"] == EVENT_CREATED


def test_outbox_unknown_event_type_falls_back_to_default_topic(store):
    bus = OutboxEventBus(store)
    bus.emit("task.something_new", task_id="t-1")
    payload = json.loads(raw_rows(store.db_path)[0]["payload"])

    assert payload["topic"] == EVENT_TOPIC_DEFAULT


# --------------------------------------------------------------------------- #
# 2. end to end: emit -> outbox row -> relay publishes -> marked published
# --------------------------------------------------------------------------- #
def test_relay_publishes_emit_and_marks_row_published(store):
    """Full path: emit, row lands in the table, relay publishes, row is marked."""
    bus = OutboxEventBus(store)
    events = InMemoryEventBus()
    emitted = bus.emit(EVENT_CREATED, task_id="t-1", workflow_id="wf-1")
    bus.emit(EVENT_COMPLETED, task_id="t-2", workflow_id="wf-1")

    # before the relay: two rows, neither published
    assert [r["id"] for r in store.get_unpublished_outbox_events()] == [1, 2]
    assert all(r["published_at"] is None for r in raw_rows(store.db_path))
    assert events.events == []

    result = OutboxRelay(store, events).poll_once()

    assert result == {"considered": 2, "published": [1, 2], "retried": [], "dead_lettered": []}

    # the relay really published them, preserving the stored event identity
    assert [e["event_type"] for e in events.events] == [EVENT_CREATED, EVENT_COMPLETED]
    assert events.events[0]["event_id"] == emitted["event_id"]
    assert events.events[0]["task_id"] == "t-1"
    assert events.events[0]["timestamp"] == emitted["timestamp"]

    # and the rows are durably marked published, not just reported as such
    rows = raw_rows(store.db_path)
    assert all(r["published_at"] is not None for r in rows)
    assert store.get_unpublished_outbox_events() == []
    assert store.get_outbox_event(1)["published_at"] is not None


def test_relay_second_sweep_publishes_nothing_twice(store):
    bus = OutboxEventBus(store)
    events = InMemoryEventBus()
    bus.emit(EVENT_CREATED, task_id="t-1")
    relay = OutboxRelay(store, events)

    assert relay.poll_once()["published"] == [1]
    assert relay.poll_once() == {"considered": 0, "published": [], "retried": [],
                                 "dead_lettered": []}
    assert len(events.events) == 1


def test_relay_leaves_published_rows_out_of_later_sweeps(store):
    bus = OutboxEventBus(store)
    events = InMemoryEventBus()
    bus.emit(EVENT_CREATED, task_id="t-1")
    relay = OutboxRelay(store, events)
    relay.poll_once()

    bus.emit(EVENT_COMPLETED, task_id="t-1")
    result = relay.poll_once()

    assert result["published"] == [2]
    assert len(events.events) == 2


# --------------------------------------------------------------------------- #
# 3. retry with backoff, then dead-letter
# --------------------------------------------------------------------------- #
class _FlakyBus:
    """Fails the first `failures` emits with a retryable error, then succeeds."""

    def __init__(self, failures: int):
        self.failures = failures
        self.attempts = 0
        self.events: list[dict] = []

    def emit(self, event_type, **fields):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RetryableError("connection reset by peer")
        self.events.append({"event_type": event_type, **fields})
        return self.events[-1]


def test_relay_retries_then_publishes_after_backoff(store):
    bus = OutboxEventBus(store)
    live = _FlakyBus(failures=1)
    clock = [1_000_000.0]
    relay = OutboxRelay(store, live, clock=lambda: clock[0])
    bus.emit(EVENT_CREATED, task_id="t-1")

    # sweep 1: publish fails -> row must NOT be marked published
    first = relay.poll_once()
    assert first == {"considered": 1, "published": [], "retried": [1], "dead_lettered": []}
    row = store.get_outbox_event(1)
    assert row["published_at"] is None
    assert row["attempts"] == 1
    assert "connection reset" in row["last_error"]
    assert row["next_attempt_at"] is not None
    assert row["dead_lettered_at"] is None

    # the backoff is respected: a sweep before next_attempt_at does nothing
    assert OutboxRelay(store, live, clock=lambda: clock[0]).poll_once()["considered"] == 0

    # ... and once the backoff has elapsed the same event publishes
    clock[0] += 3600
    second = OutboxRelay(store, live, clock=lambda: clock[0]).poll_once()
    assert second["published"] == [1]
    assert len(live.events) == 1
    assert store.get_outbox_event(1)["published_at"] is not None


def test_relay_dead_letters_after_attempts_are_exhausted(store):
    bus = OutboxEventBus(store)
    live = _FlakyBus(failures=99)
    clock = [1_000_000.0]
    relay = OutboxRelay(store, live, max_attempts=3, clock=lambda: clock[0])
    bus.emit(EVENT_CREATED, task_id="t-1")

    outcomes = []
    for _ in range(3):
        outcomes.append(relay.poll_once())
        clock[0] += 3600  # backoff always elapsed

    # attempts 1 and 2 are retried, the third exhausts the budget
    assert [o["retried"] for o in outcomes] == [[1], [1], []]
    assert [o["dead_lettered"] for o in outcomes] == [[], [], [1]]

    row = store.get_outbox_event(1)
    assert row["attempts"] == 3
    assert row["dead_lettered_at"] is not None
    assert row["published_at"] is None
    assert "connection reset" in row["last_error"]

    # a dead-lettered event is never retried and never published
    before = live.attempts
    assert relay.poll_once()["considered"] == 0
    assert live.attempts == before


def test_dead_letter_is_announced_on_the_dead_letter_bus(store):
    bus = OutboxEventBus(store)
    live = _FlakyBus(failures=99)
    dlq = InMemoryEventBus()
    relay = OutboxRelay(store, live, max_attempts=1, dead_letter_events=dlq)
    bus.emit(EVENT_CREATED, task_id="t-1")

    relay.poll_once()

    assert len(dlq.events) == 1
    assert dlq.events[0]["event_type"] == "task.failed"
    assert dlq.events[0]["outbox_event_id"] == 1
    assert "connection reset" in dlq.events[0]["error"]


def test_non_retryable_failure_dead_letters_immediately(store):
    class _HardFail:
        def emit(self, event_type, **fields):
            raise ValueError("invalid payload")

    bus = OutboxEventBus(store)
    relay = OutboxRelay(store, _HardFail(), max_attempts=5)
    bus.emit(EVENT_CREATED, task_id="t-1")

    result = relay.poll_once()

    assert result["dead_lettered"] == [1]
    assert result["retried"] == []
    assert store.get_outbox_event(1)["attempts"] == 1


def test_one_poisoned_event_does_not_stall_the_outbox(store):
    """A dead event must not prevent healthy events from being published."""
    class _Poison:
        def emit(self, event_type, **fields):
            if fields.get("task_id") == "bad":
                raise ValueError("schema error")
            self.ok = True
            return {"event_type": event_type, **fields}

    bus = OutboxEventBus(store)
    live = _Poison()
    bus.emit(EVENT_CREATED, task_id="bad")
    bus.emit(EVENT_CREATED, task_id="good")

    result = OutboxRelay(store, live).poll_once()

    assert result["published"] == [2]
    assert result["dead_lettered"] == [1]
    assert getattr(live, "ok", False)


def test_relay_dead_letters_an_unreadable_payload(store):
    """A corrupt payload is dead-lettered, not published empty and not a crash."""
    store.write_outbox_event(EVENT_CREATED, {"topic": "x", "event": {"a": 1}})
    con = sqlite3.connect(store.db_path)
    con.execute("UPDATE outbox_events SET payload=? WHERE id=1", ("{not json",))
    con.commit()
    con.close()
    events = InMemoryEventBus()

    result = OutboxRelay(store, events).poll_once()

    assert result == {"considered": 1, "published": [], "retried": [], "dead_lettered": [1]}
    # the lifecycle event was never published; only the dead-letter notice was
    assert [e["event_type"] for e in events.events] == ["task.failed"]
    assert "malformed outbox payload" in store.get_outbox_event(1)["last_error"]


def test_relay_accepts_a_bare_event_payload(store):
    """A payload that is the event itself (no envelope) still publishes."""
    store.write_outbox_event(EVENT_CREATED, {"event_id": "e-1", "task_id": "t-1"})
    events = InMemoryEventBus()

    result = OutboxRelay(store, events).poll_once()

    assert result["published"] == [1]
    assert events.events[0]["event_id"] == "e-1"


# --------------------------------------------------------------------------- #
# 4. the transaction claim in write_outbox_event's docstring
# --------------------------------------------------------------------------- #
def test_outbox_row_and_state_change_commit_together(tmp_path):
    """Both writes land, or neither does - that is what the docstring claims."""
    from hermes.async_engine.contract import Task, TaskStatus

    store = AsyncTaskStore(str(tmp_path / "tx.db"))
    store.create_workflow("wf-1")
    store.create_task(Task(task_id="t-1", task_type="research", workflow_id="wf-1"))

    with store.transaction():
        store.set_status("t-1", TaskStatus.RUNNING)
        OutboxEventBus(store).emit(EVENT_CREATED, task_id="t-1")

    assert store.get_task("t-1").status is TaskStatus.RUNNING
    assert len(raw_rows(store.db_path)) == 1


def test_rolled_back_transaction_persists_neither_the_event_nor_the_change(tmp_path):
    from hermes.async_engine.contract import Task, TaskStatus

    store = AsyncTaskStore(str(tmp_path / "tx.db"))
    store.create_workflow("wf-1")
    store.create_task(Task(task_id="t-1", task_type="research", workflow_id="wf-1"))

    with pytest.raises(RuntimeError):
        with store.transaction():
            store.set_status("t-1", TaskStatus.RUNNING)
            OutboxEventBus(store).emit(EVENT_CREATED, task_id="t-1")
            raise RuntimeError("business rule violation downstream")

    # the state change was rolled back...
    assert store.get_task("t-1").status is TaskStatus.CREATED
    # ... and so was the event that claimed it happened
    assert raw_rows(store.db_path) == []
    assert store.get_unpublished_outbox_events() == []


def test_transaction_is_reentrant(tmp_path):
    store = AsyncTaskStore(str(tmp_path / "tx.db"))
    with store.transaction():
        store.write_outbox_event(EVENT_CREATED, {"topic": "t", "event": {}})
        with store.transaction():  # joins the outer transaction
            store.write_outbox_event(EVENT_COMPLETED, {"topic": "t", "event": {}})

    assert len(raw_rows(store.db_path)) == 2


def test_transaction_is_per_thread(tmp_path):
    """A worker pool shares one store; one thread's rollback must not affect
    another thread's committed work."""
    store = AsyncTaskStore(str(tmp_path / "tx.db"))
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            with store.transaction():
                store.write_outbox_event(EVENT_CREATED,
                                         {"topic": "t", "event": {"i": index}})
                if index == 0:
                    raise RuntimeError("roll this one back")
        except RuntimeError as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 1
    payloads = sorted(r["payload"] for r in raw_rows(store.db_path))
    assert len(payloads) == 1
    assert json.loads(payloads[0])["event"]["i"] == 1


def test_outbox_event_audit_columns_exist_on_a_pre_existing_table(tmp_path):
    """init() adds the relay columns to an outbox table created before them."""
    db_path = str(tmp_path / "legacy.db")
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE outbox_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, "
        "published_at TEXT)")
    con.execute(
        "INSERT INTO outbox_events (event_type, payload, created_at) "
        "VALUES ('task.created', '{}', '2020-01-01T00:00:00+00:00')")
    con.commit()
    con.close()

    store = AsyncTaskStore(db_path)  # runs the additive migration

    row = store.get_outbox_event(1)
    assert row is not None
    assert row["attempts"] == 0
    assert row["dead_lettered_at"] is None
    assert store.get_unpublished_outbox_events()[0]["id"] == 1


def test_get_unpublished_outbox_events_respects_limit(store):
    bus = OutboxEventBus(store)
    for i in range(5):
        bus.emit(EVENT_CREATED, task_id=f"t-{i}")

    assert len(store.get_unpublished_outbox_events(limit=2)) == 2
    assert len(store.get_unpublished_outbox_events()) == 5
