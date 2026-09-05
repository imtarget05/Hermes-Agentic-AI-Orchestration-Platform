# Hermes — Project 1 · Async Task Queue + Parallel Agent Workers + Event Audit

> Orchestration engine built so the **orchestrator never executes worker logic**.
> It validates requests, creates tasks, builds a DAG, publishes to **RabbitMQ**,
> tracks state, and lets a **parallel worker pool** do the work — with **manual ACK**,
> **retry + backoff**, a **dead-letter queue**, **idempotency**, **Kafka** lifecycle
> events, and **Prometheus/Grafana** observability.

This is the "Project 1" layer of the repo, layered on top of the Project 2
multi-agent runtime. It lives in `src/hermes/async_engine/`.

---

## 1. Why the async architecture

The old flow was synchronous: `Task 1 → Agent A → Agent B → Agent C`, and
Task 2/3 waited. A slow/down agent blocks the orchestrator's request.

The new flow is:

```
 Task ─> Orchestrator ─> RabbitMQ ─┼─ Worker A ─┐
                                   ┼─ Worker B ─┼─> Result Aggregator ─> Evidence Store
                                   └─ Worker C ─┘
```

The orchestrator only **dispatches**. Workers are separate processes pulling
from the queue. If one worker dies, an un-ACKed message is requeued and another
worker takes over.

---

## 2. Package layout

```
src/hermes/async_engine/
  contract.py      canonical Task contract, statuses, routing (agent.<type> → q.agent.<type>)
  dag.py           DAG build + dependency resolution (parallel branches, join)
  retry.py         retryable vs non-retryable classification + backoff (1s/5s/30s)
  store.py         Postgres/SQLite store: workflows, tasks, task_results, execution_state
  backends.py      MessageBus: RabbitMQBus (pika) + InMemoryBus (tests / no-broker)
  eventbus.py      KafkaEventBus + JsonlEventBus + InMemoryEventBus (off-critical-path)
  worker.py        Worker lifecycle (RECEIVE→VALIDATE→STARTED→EXECUTE→…), WorkerPool
  orchestrator.py  AsyncOrchestrator: validate → create → build DAG → dispatch → aggregate
  metrics.py       Prometheus metrics (+ Noop fallback)
  loadtest.py      N × workers load test → throughput / p95 / speedup
  cli.py           `ready` · `work` (long-running worker) · `loadtest`
---

## 4. Worker lifecycle with manual ACK (§5)

```
 RECEIVE (basic_get, auto_ack=False)
   → VALIDATE contract
   → MARK STARTED (idempotency claim)
   → EXECUTE
        success ───────────────────> COMPLETED (ack)
        failure → retryable & tries → RETRY (requeue + backoff)
        failure → exhausted/bad    → DEAD-LETTER (ack)
```

**Manual ACK is the reliability core.** A worker that crashes before ACK leaves
the message unacknowledged → RabbitMQ requeues it → another worker retries.

---

## 5. Retry policy (§6)

Classification is split, so bad messages never spin:

| Retryable (transient)             | Non-retryable (dead-letter)  |
|-----------------------------------|------------------------------|
| timeout, connection reset         | invalid payload             |
| provider temporarily unavailable  | invalid task type           |
| HTTP 429 / 502 / 503 / 504        | authentication failure      |
| rate limit, overloaded            | schema error                |

Backoff: attempt 1 → 1s, 2 → 5s, 3 → 30s, then `q.agent.deadletter`.
Unknown failures default to **non-retryable** to avoid infinite loops.

---

## 6. Idempotency (§7)

RabbitMQ gives *at-least-once*, not *exactly-once*. A worker can complete a task
then crash before ACK → the message is redelivered. The engine tracks
`task_id → execution_state`:

- if state is `completed`, the duplicate is **ACKed and skipped** (never re-executed)
- a live `started` claim is refused to a second concurrent worker

Verified by `test_idempotency_skips_reredelivered_completed_task`.

---

## 7. DAG execution (§9)

```
 Research
   ├──▶ Analyze
   └──▶ Analyze 2
             │
             ▼
           Report        (dispatched only after BOTH analyses complete)
```

`dag.ready_tasks()` / `resolve_ready()` return tasks whose deps are all
completed — never a naive `for task in tasks: run(task)`.

---

## 8. Kafka lifecycle events (§10)

Kafka is **off the critical path** (`emit` is fire-and-forget). Topics:
`hermes.task.created/.started/.completed/.failed/.retried`.

```json
{"event_type":"task.completed","task_id":"…","workflow_id":"…",
 "worker_id":"worker-03","attempt":1,"duration_ms":823}
```

The `JsonlEventBus` produces the same events without a broker.
src/hermes/async_api.py   FastAPI: submit workflow, query state, /metrics
```

---

## 3. Canonical task contract (§4)

Every task is one shape; no worker defines its own format.

```json
{
  "task_id": "…", "workflow_id": "…", "parent_task_id": null,
  "task_type": "research", "priority": 5, "attempt": 1, "max_attempts": 3,
  "created_at": "ISO-8601", "deadline": "ISO-8601",
  "payload": {}, "metadata": {"source": "telegram", "user_id": "…"}
}
```

Routing registry (`contract.ROUTING`):
`research → q.agent.research`, `analyze → q.agent.analyze`,
`report → q.agent.report`, `notify → q.agent.notify`.
---

## 9. Metrics & monitoring (§11)

`hermes_tasks_total`, `…_completed_total`, `…_failed_total`, `…_retried_total`,
`hermes_task_duration_seconds` (histogram), `hermes_task_queue_depth`,
`hermes_worker_active`, `hermes_worker_utilization`, `hermes_task_latency_seconds`.
Grafana dashboard is auto-provisioned in `grafana/provisioning/`.

---

## 10. Load test & parallel speedup (§12)

`make async-loadtest` runs **10 / 50 / 100 / 500** tasks × **1 / 2 / 4 / 8**
workers and reports throughput, p95, queue depth, retry rate, and speedup vs the
1-worker baseline.

---

## 11. Run it

```bash
# 0) no infra needed — in-memory smoke of the whole stack
make async-ready

# 1) unit + worker + orchestrator + api tests
make async-test

# 2) full stack via docker compose (rabbitmq, kafka, postgres, prometheus, grafana)
make compose-up          # infra
docker compose up worker api   # app services
# Grafana: http://localhost:3000  (admin/admin)   Prometheus: http://localhost:9090
# RabbitMQ mgmt: http://localhost:15672 (guest/guest)

# 3) API (memory mode)
make async-api           # http://localhost:8000/docs
curl -X POST localhost:8000/async/run -H 'content-type: application/json' -d '{
  "nodes":[
    {"task_id":"r1","task_type":"research","deps":[]},
    {"task_id":"a1","task_type":"analyze","deps":["r1"]}
  ]}'

# 4) load test
make async-loadtest
```

Optional extras: `pip install -e ".[async-all]"` (pika, confluent-kafka,
prometheus-client) for the real broker path.

---

## 12. Definition of Done — status

| Requirement                      | Where                                       | Done |
|----------------------------------|---------------------------------------------|:----:|
| Orchestrator doesn't execute     | `orchestrator.py` dispatch-only             | ✅   |
| RabbitMQ used                    | `backends.RabbitMQBus` + compose            | ✅   |
| Parallel worker pool             | `worker.WorkerPool`                         | ✅   |
| Manual ACK                       | `Worker` `auto_ack=False`                   | ✅   |
| Retry + backoff                  | `retry.RetryPolicy` + requeue-with-TTL      | ✅   |
| Dead-letter queue                | `q.agent.deadletter`                        | ✅   |
| Idempotency                      | `store.execution_state`                     | ✅   |
| Task status persisted            | `store` (tasks/task_results/workflows)      | ✅   |
| DAG dependency support           | `dag.py` + parallel join test               | ✅   |
| Kafka lifecycle events           | `eventbus.KafkaEventBus` (guarded)          | ✅   |
| Prometheus metrics               | `metrics.PrometheusMetrics`                 | ✅   |
| Grafana dashboard                | `grafana/provisioning/…`                    | ✅   |
| Load test 10/50/100/500          | `loadtest.run_load_test`                    | ✅   |
| Parallel speedup numbers         | `load_test_report`                          | ✅   |
| Docker Compose full stack        | `docker-compose.yml`                        | ✅   |
| Unit test                        | `tests/test_async_*.py`                     | ✅   |
| Integration test                 | `tests/test_async_integration.py` (gated)   | ✅   |
