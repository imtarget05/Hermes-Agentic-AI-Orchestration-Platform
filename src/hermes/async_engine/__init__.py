"""Hermes Project 1 — Async Task Queue + Parallel Agent Workers + Event Audit.

A synchronous engine (thread-pool workers) over a pluggable message bus
(RabbitMQ in production, InMemory for tests) with:

- canonical Task contract (no worker-specific formats)
- manual-ACK worker lifecycle (crash-safe: unacked msg is requeued)
- retry policy w/ exponential backoff and a dead-letter queue
- idempotent execution via task_id -> execution_state
- DAG execution with parallel branches + dependency resolution
- Kafka lifecycle events (off the critical path) + Prometheus metrics
- load-test harness producing throughput / p95 / parallel-speedup numbers

Usage (see README_P1.md): pick a bus, a store, register task handlers,
start workers, then submit workflows through the AsyncOrchestrator.
"""
from __future__ import annotations

from .backends import InMemoryBus, MessageBus, RabbitMQBus
from .contract import (
    DEFAULT_MAX_ATTEMPTS,
    ROUTING,
    Task,
    TaskResult,
    TaskStatus,
    Workflow,
)
from .dag import TaskDAG, build_dag, resolve_ready
from .eventbus import InMemoryEventBus, JsonlEventBus, KafkaEventBus
from .loadtest import load_test_report, run_load_test
from .metrics import NoopMetrics, PrometheusMetrics
from .orchestrator import AsyncOrchestrator
from .retry import NonRetryableError, RetryableError, RetryPolicy
from .store import AsyncTaskStore, init_async_db
from .worker import Worker, WorkerPool

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "ROUTING",
    "AsyncOrchestrator",
    "AsyncTaskStore",
    "InMemoryBus",
    "InMemoryEventBus",
    "JsonlEventBus",
    "KafkaEventBus",
    "MessageBus",
    "NonRetryableError",
    "NoopMetrics",
    "PrometheusMetrics",
    "RabbitMQBus",
    "RetryPolicy",
    "RetryableError",
    "Task",
    "TaskDAG",
    "TaskResult",
    "TaskStatus",
    "Worker",
    "WorkerPool",
    "Workflow",
    "build_dag",
    "init_async_db",
    "load_test_report",
    "resolve_ready",
    "run_load_test",
]