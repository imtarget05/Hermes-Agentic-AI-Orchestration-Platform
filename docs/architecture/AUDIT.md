# Architecture Audit — Hermes Agentic AI Orchestration Platform

## Baseline (Phase 0 — Established)

### Project Structure
- **Entry points**: `app.py`, `src/hermes/api.py`, `src/hermes/async_api.py`, `src/hermes/gateway/`, `src/hermes/inbox_cli.py`
- **Source root**: `src/hermes/` with 26 subdirectories
- **Key directories**: api.py, telegram_chat/, gateway/, router/, orchestrator/, procurement/, agents/, rag/, tools/, tasks/, messaging/, llm/, async_engine/, config/, dashboard.py, dashboard-ui/, ingestion/, scraper/

### Core Architecture (from README + code analysis)

#### 8-Loop Agentic Architecture (loops/)
1. **Context** — ContextBuilder (state + evidence)
2. **Planning** — Planner (LLM-hookable DAG generation)
3. **Dispatch** — AsyncOrchestrator + advance_forever/advance_once
4. **Execute** — WorkerPool (manual ACK, retry + backoff)
5. **Verify** — Verifier (validator chain, grounded checks)
6. **Reliability** — circuit breaker + timeout + DLQ
7. **Evaluate** — workflow_report, rolling_report
8. **Learn/Audit** — failure_pattern → policy suggestions

#### Data Flow (Input → Output)
```
User Request (Telegram DM / API / CLI)
    ↓
Router (rule-based + optional LLM classifier)
    ↓
Intent Classification → Routing Plan
    ↓
Procurement DAG (4 parallel specialists: price, vendor, contract, spec)
    ↓
Analysis (PolicyEngine deterministic scoring)
    ↓
Verification (RAG evidence grounding, price check)
    ↓
Human Approval (Telegram inline buttons / API)
    ↓
Task lifecycle: created→queued→running→handoff→completed
    ↓
Persisted to SQLite (hermes_tasks.db) with events
```

#### Key Modules & Responsibilities

| Module | Responsibility |
|--------|---------------|
| `api.py` | FastAPI entry point: health, procurement/run, webhook, tasks, telegram webhook |
| `async_engine/` | Core orchestration: DAG, state machine, task store, message bus, budgets |
| `orchestrator.py` | Validate → create workflow/tasks → dispatch → aggregate results |
| `eventbus.py` | Lifecycle event bus: InMemory/Jsonl/Kafka/Outbox with emit_best_effort |
| `store.py` (async_engine) | AsyncTaskStore: SQLite/Postgres CRUD, idempotency CAS, outbox pattern |
| `tasks/store.py` (legacy) | simpler TaskStore with transition validation |
| `router/` | Rule-based routing: RouterAgent + RoutingRegistry from routing.json |
| `intent_router.py` | Deterministic intent classifier (SIMPLE_TASK, COMPARE_TASK, PROCUREMENT_DECISION, etc.) |
| `procurement/` | Enterprise procurement DAG: build_graph, build_handlers, run_case, benchmark |
| `agents/` | price / vendor / contract / spec / analysis / verification agents |
| `rag/` | BM25-lite case index + ingest + embed hook |
| `tools/` | 15 registered tools with policy-aware execution, input denylist |
| `llm/` | Cloudflare Workers AI client + gateway + router classifier |
| `messaging/` | Notifier interface: Mock/Telegram/Safe wrappers, approval keyboards |
| `tasks/schemas.py` | TaskStatus enum, ALLOWED_TRANSITIONS, Task/TaskEvent Pydantic models |
| `config/` | Pydantic-settings from .env variables |
| `dashboard.py` | Read-only inbox API (port 8001) |
| `dashboard-ui/` | Web static files pointing to API base |

#### External Dependencies
- SQLite databases: `hermes_tasks.db`, `telegram_sessions.db`
- `.env` configuration (HERMES_DB_PATH, HERMES_ROUTING_PATH, HERMES_SANDBOX_DIR, etc.)
- Cloudflare Workers AI (LLM provider, stubbed when no creds)
- Telegram Bot API (optional, mock when no token)
- bitnami/kafka (docker-compose, optional - only for async stack)
- pika/rabbitmq (docker-compose, optional)

#### Configuration (.env defaults)
- `HERMES_DB_PATH` → `./hermes_tasks.db`
- `HERMES_ROUTING_PATH` → `./routing.json`
- `HERMES_SANDBOX_DIR` → `./sandbox`
- `HERMES_ASYNC_MODE` → `memory`
- `LLM_PROVIDER` → `cloudflare`
- `TELEGRAM_BOT_TOKEN` → *(empty = mock mode)*
- `TELEGRAM_ALLOWED_USERS` → *(empty = anyone can chat)*
- `HERMES_HITL_AUTO_APPROVE` → *(empty)*

#### API Contract (endpoints + methods)
| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health + mode (llm/notifier/projects) |
| POST | /procurement/run | Run full DAG case |
| POST | /run | Alias legacy → procurement pipeline |
| GET | /procurement/approvals/pending | Pending approvals |
| POST | /procurement/approvals/{id}/resolve | Resolve approval |
| GET | /tasks?limit=N / /tasks/{id} | Inbox + lifecycle events |
| POST | /telegram/webhook | Receive Telegram update (verify secret) |
| POST | /telegram/webhook/set | Set webhook URL |
| GET | /docs | Swagger documentation |

#### Existing Tests (from README)
- `tests/test_telegram_chat.py`: session/auth/intent/anti-fraud/webhook-secret/dispatch
- `test_api.py`, `test_procurement_pipeline.py`, `test_orchestrator.py`
- `152 passed, 3 skipped`
- Specialized tests: `test_hermes_04_queue_reliability.py` (race conditions), `test_hermes_10_audit_trace.py` (event bus down)

#### Business Workflows
1. **Procurement Purchase**: User requests → Router → DAG (4 specialists) → Analysis → Verification → Human approval → Task completed + events persisted
2. **Telegram 1:1 Chat**: `/start` / `/help` / `/new` / `/task <id>` / `approve <id>` / `reject <id>` → Session per chat_id → PDF quote handling → Allowlist
3. **API Inbox**: GET /tasks → view lifecycle events → POST /telegram/webhook/set → configure webhook

#### Error-Handling Behavior
- Event bus failures: `emit_best_effort()` swallows exceptions (never propagate to task execution)
- Task idempotency: Atomic CAS via `mark_started` SQL UPDATE (state NOT IN COMPLETED, RUNNING)
- Input denylist: Tools blocked from `rm -rf`, `shutdown`, `reboot`, etc.
- Guardrails: Input/output/tool-call safety checks
- Budget exceeded: NON-retryable BudgetExceededError → dead-letter + escalation

#### Golden Cases / Representative Scenarios
- **50-laptop procurement case**: runs DAG → recommends Lenovo at total 54000 with reasons + evidence refs
- **Telegram `/start` → `mua 50 laptop`**: full workflow end-to-end
- **PDF quote parsing**: deterministic price extraction, `quote_date` attachment, `is_demo` flag
- **Concurrent claim test**: 8 threads + Barrier claiming 1 task → exactly 1 wins
- **Kafka down test**: `_BrokenEventBus` raises on every emit → task still COMPLETED

#### Unknowns / Needs Verification
- Full async stack (RabbitMQ/Kafka/Postgres/Prometheus/Grafana) — not enabled by default
- Real broker integration test contracts — gated, not run in CI
- Circuit breaker Grafana dashboard metrics — exist but unverified in this repo
- Cross-worker idempotency under realistic load
