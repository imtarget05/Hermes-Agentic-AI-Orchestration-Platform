# Hermes — Project 1 · Agentic AI Orchestration Platform

> Hermes is an **agentic orchestration engine** — not just a distributed task
> queue. The queue is only one loop. The real architecture is **8 loops** that
> turn a user request into verified, evaluated, continuously-improving work.

```
 USER REQUEST
      │
      ▼
┌──────────────────────────────┐
│ 1. CONTEXT LOOP              │   retrieve state · prior evidence · failure patterns
│    loops/context.py          │   → build ExecutionContext
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 2. PLANNING / REASONING LOOP │   task decomposition · DAG generation
│    loops/planner.py          │   (LLM-hookable, policy-aware)
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 3. TASK DISPATCH LOOP        │   priority · routing · queue assignment
│    orchestrator.py +         │   ┌─────────────┐
│    loops/advance (advancer)  │──▶│   RabbitMQ  │  ← implementation of THIS loop only
└──────────────┬───────────────┘   └──────┬──────┘
               │                         │
               │            ┌────────────┼────────────┐
               │            ▼            ▼            ▼
               │         Worker       Worker       Worker
               │            │            │            │
               │            └────────────┼────────────┘
               ▼                         ▼
┌──────────────────────────────┐
│ 4. TOOL / AGENT EXECUTION    │   LLM · API · DB · shell
│    worker.py (handler)       │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 5. VERIFICATION LOOP         │   schema · evidence · quality check
│    loops/verify.py           │   PASS → aggregate │ FAIL → retry/fallback
└──────────────┬───────────────┘
               │
          ┌────┴─────┐
          ▼          ▼
        PASS       FAIL
          │          │
          ▼          ▼
      Aggregate    Retry ──▶ (exhausted) ──▶ fallback / escalate
          │
          ▼
┌──────────────────────────────┐
│ 6. RELIABILITY LOOP          │   retry · timeout · circuit breaker
│    loops/reliability.py      │   · DLQ · escalation
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 7. EVALUATION LOOP           │   latency · success · quality
│    loops/evaluate.py         │   · worker efficiency · speedup
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 8. LEARNING / AUDIT LOOP     │   Kafka events · failure patterns
│    loops/audit.py            │   · routing/planning feedback ──▶ loop 1 & 2
└──────────────────────────────┘
```

**RabbitMQ is not the architecture** — it is the *implementation of loop 3* (Task
Dispatch). The 8-loop flow is transport-agnostic: swap RabbitMQ for any
`MessageBus` and the agentic behavior (plan → verify → recover → evaluate →
learn) is unchanged. `run_agent_workflow()` in `loops/pipeline.py` runs all 8
loops end-to-end.

This is the "Project 1" layer of the repo, layered on top of the Project 2
multi-agent runtime. It lives in `src/hermes/async_engine/`.

The **loops/** package is the agentic core (loops 1,2,5,6,7,8). The files
outside loops/ are the dispatch/execute substrate (loops 3,4) that the loops run
on top of.

```
src/hermes/async_engine/
  contract.py      canonical Task contract, statuses, routing (agent.<type> → q.agent.<type>)
  dag.py           DAG build + dependency resolution (parallel branches, join)
  retry.py         retryable vs non-retryable classification + backoff (1s/5s/30s)
  store.py         Postgres/SQLite store: workflows, tasks, task_results, execution_state,
                   + task_dependencies (DAG edges for cross-process dispatch)
  backends.py      MessageBus: RabbitMQBus (pika) + InMemoryBus (tests / no-broker)
  eventbus.py      KafkaEventBus + JsonlEventBus + InMemoryEventBus (off-critical-path)
  worker.py        Worker lifecycle (RECEIVE→VALIDATE→STARTED→EXECUTE→VERIFY→…), WorkerPool
  orchestrator.py  AsyncOrchestrator: validate → create → build DAG → dispatch → aggregate
  metrics.py       Prometheus metrics (+ Noop fallback)
  loadtest.py      N × workers load test → throughput / p95 / speedup
  cli.py           `ready` · `work` (long-running worker) · `orchestrator` · `loadtest`
  loops/
    __init__.py    re-exports all loops
    context.py     Loop 1 — retrieve state/evidence/failure patterns → ExecutionContext
    planner.py     Loop 2 — LLM-hookable decomposition + DAG gen + dependency resolution
    verify.py      Loop 5 — schema/evidence/quality check (PASS/FAIL → retry or DLQ)
    reliability.py Loop 6 — timeout · circuit breaker · escalation
    evaluate.py    Loop 7 — latency/success/quality/worker efficiency
    audit.py       Loop 8 — failure-pattern mining → policy feedback into loop 2
    pipeline.py    run_agent_workflow() — all 8 loops end-to-end
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
| 8-loop agentic architecture      | `loops/` (context/plan/verify/relia/eval/audit) + `pipeline` | ✅   |
| Context loop (state + evidence)  | `loops/context.py` ContextBuilder           | ✅   |
| Planning loop (LLM-hookable DAG) | `loops/planner.py` Planner                  | ✅   |
| Dispatch loop                    | `orchestrator.py` + `loops` advancer        | ✅   |
| Execute loop (workers)           | `worker.py` WorkerPool                      | ✅   |
| Verification loop                | `loops/verify.py` Verifier (wired in worker)| ✅   |
| Reliability loop (timeout/breaker)| `loops/reliability.py`                     | ✅   |
| Evaluation loop                  | `loops/evaluate.py` workflow_report         | ✅   |
| Learning/Audit loop              | `loops/audit.py` failure-pattern → policy   | ✅   |
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
