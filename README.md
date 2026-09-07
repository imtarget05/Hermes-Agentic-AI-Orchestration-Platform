# Hermes — Agentic AI Orchestration Platform

Case chính đã chạy được: **mua 50 laptop → phân tích 3 báo giá (Dell/Lenovo/HP) → recommendation → duyệt mua trên Telegram 1:1**.

Luồng thật đang chạy (local-first):

```
Yêu cầu mua sắm (Telegram DM 1:1 / API / CLI)
        ↓
Router (rule-based + LLM classifier nếu có creds)
        ↓
Procurement DAG — 4 specialists song song → analysis join → verification
(price ‖ vendor ‖ contract ‖ spec → RAG evidence → recommendation)
        ↓
Human approval — nút Telegram ✅/❌ hoặc lệnh `approve <id>` / API resolve
        ↓
Task COMPLETED, lưu lifecycle events vào SQLite
```

## Làm được gì (đã verify)

- **Procurement DAG** (`src/hermes/procurement/`, `src/hermes/orchestrator/__init__.py`): chạy case 50 laptop → recommend **Lenovo, total 54000**, kèm reasons + evidence refs. Test `test_procurement_run_recommends_lenovo` xanh.
- **Chat Telegram 1:1** (`src/hermes/telegram_chat/`): DM với bot `@agentic09_bot` qua webhook + tunnel local. Hiểu `/start /help /new /tasks /task <id>`, lệnh `approve <id>` / `reject <id>`, nhận **PDF báo giá** gửi thẳng vào chat (parse → dùng cho lần chạy sau), hỏi bù spec khi yêu cầu quá ngắn (session multi-turn per `chat_id`, SQLite riêng `telegram_sessions.db`), allowlist theo Telegram id/@username (`TELEGRAM_ALLOWED_USERS`).
- **Nút duyệt inline** (`src/hermes/messaging/`): `✅ Approve / ❌ Reject` gắn `request_id` (`ApprovalStore`), bấm trên Telegram hoặc resolve qua API đều stamp task `APPROVED/REJECTED`. Không token → mock notifier ghi file, tests không cần mạng.
- **API chính** (`src/hermes/api.py` + `telegram_chat/webhook.py`, cùng port 8000):

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/health` | Health + mode (llm/notifier/projects) |
| `POST` | `/procurement/run` | Chạy case full DAG |
| `POST` | `/run` | Alias legacy → procurement pipeline |
| `GET` | `/procurement/approvals/pending` | Approval đang chờ |
| `POST` | `/procurement/approvals/{id}/resolve` | `{approved, resolver?}` |
| `GET` | `/tasks?limit=N` / `/tasks/{id}` | Inbox + lifecycle events |
| `POST` | `/telegram/webhook` | Nhận update Telegram (verify secret) |
| `POST` | `/telegram/webhook/set` | `{"url": "<tunnel>/telegram/webhook"}` |
| `GET` | `/docs` | Swagger |

- **Task lifecycle** (`src/hermes/tasks/`): `created→queued→running→handoff→completed`, nhánh lỗi `failure→retry/failed`, `validate_transition` chặn chuyển trạng thái sai; mỗi bước là 1 `TaskEvent` trong SQLite.
- **Multi-Agent RAG** (`src/hermes/rag/`): index BM25-lite per-case (quotes + vendors + spec), specialists trích `RAG-EVIDENCE [source=…]`.
- **LLM**: Cloudflare Workers AI mặc định, thiếu creds → stub deterministic (tests + dev offline vẫn chạy).
- **Tests**: `152 passed, 3 skipped` (`tests/test_telegram_chat.py`: session/auth/intent/**chống-bịa giá**/webhook-secret/dispatch; `test_api.py`, `test_procurement_pipeline.py`, `test_orchestrator.py` giữ contract).

## Chính sách giá (grounded — không bịa)

Sau sự cố bot tự bịa giá MacBook: bot **không bao giờ** nêu giá/spec/đời máy từ trí nhớ LLM.

- Câu hỏi giá thị trường → intent `price_question`, trả lời **deterministic từ báo giá có ngày**, 3 phần: (a) đang có dữ liệu gì + ngày nào, (b) verdict cũ/mới (quá 30 ngày = cũ), (c) cách lấy số tươi (gửi PDF).
- Mọi quote có `quote_date` (`parse_quote_pdf` gắn ngày ingest; demo gắn `DEMO` + `is_demo`).
- Recommendation có `data_as_of` (`DEMO` / ngày / khoảng ngày), Telegram hiện `⚠️ DEMO — không phải giá thị trường` hoặc `📅 Dựa trên báo giá ngày X`.
- Verification fail mọi claim giá cite nguồn chung chung (`quotes`) thay vì quote có ngày.
- Mọi prompt agent + chitchat đều nhúng luật `NO_FABRICATION_RULE`. Bot không tra giá live (tool `web_search` vẫn là stub theo chủ đích).

## Chạy local (cách đang dùng)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # điền TELEGRAM_BOT_TOKEN, CLOUDFLARE_*
make test              # pytest, token-free

# Term 1 — API + webhook (cùng port 8000)
PYTHONPATH=src .venv/bin/uvicorn hermes.api:app --host 127.0.0.1 --port 8000

# Term 2 — tunnel để Telegram gọi về local
cloudflared tunnel --url http://127.0.0.1:8000
curl -X POST http://127.0.0.1:8000/telegram/webhook/set \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://<tunnel>/telegram/webhook"}'

# CLI thay thế
PYTHONPATH=src .venv/bin/python -m hermes.gateway --once \
  --text "Công ty cần mua 50 laptop" --project demo
PYTHONPATH=src .venv/bin/python -m hermes.inbox_cli --limit 10
```

Test trên Telegram: chat 1:1 với bot → `/start` → `mua 50 laptop` → bấm ✅/❌ (hoặc gửi PDF báo giá trước rồi mới gửi yêu cầu).

> Tunnel `trycloudflare` free không đảm bảo uptime (sập → tạo URL mới + `setWebhook` lại). Muốn ổn định: named tunnel có tài khoản, hoặc chuyển bot sang polling (`TELEGRAM_MODE=polling`, `make approval-bot` làm nền).

## Cấu hình (`.env`, xem `.env.example`)

| Var | Mặc định | Ý nghĩa |
|---|---|---|
| `HERMES_DB_PATH` | `./hermes_tasks.db` | SQLite task store (local) |
| `HERMES_ROUTING_PATH` | `./routing.json` | Project→channel→thread (broadcast cũ; DM reply thẳng `chat_id`) |
| `HERMES_SANDBOX_DIR` | `./sandbox` | File tools + PDF Telegram (`telegram_<chat_id>/`) |
| `HERMES_ASYNC_MODE` | `memory` | Engine local không cần RabbitMQ/Kafka |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Có → Telegram, trống → mock |
| `TELEGRAM_ALLOWED_USERS` | *(empty)* | `id,@username,...` (trống = ai cũng chat được) |
| `TELEGRAM_WEBHOOK_SECRET/URL` | *(empty)* | Verify + set webhook |
| `TELEGRAM_SESSION_DB` | `./telegram_sessions.db` | Session 1:1 riêng |
| `LLM_PROVIDER` + `CLOUDFLARE_*` | `cloudflare` | Workers AI; thiếu → stub |
| `HERMES_HITL_AUTO_APPROVE` | *(empty)* | `true` để auto-duyệt khi demo/CI |

## Layout

```
src/hermes/
  api.py            FastAPI chính (procurement + inbox + mount webhook)
  telegram_chat/    1:1 chat: webhook/handler/intent/session/auth/service
  gateway/          CLI --once --text --project --quote PDFs
  router/           RouterAgent + RoutingRegistry
  orchestrator/     cầu DAG → TaskStore lifecycle + approval
  procurement/      DAG graph/handlers/runner/benchmark
  agents/           price / vendor / contract / spec / analysis / verification
  rag/              case index + ingest + embed hook
  tools/            parse_quote_pdf, compare_prices, check_approved_vendor, ...
  tasks/            schemas + TaskStore (SQLite/Postgres)
  messaging/        Telegram/Mock/Safe notifier + approval_bot (resolve + polling fallback)
  llm/              Cloudflare client + gateway + router classifier
  async_engine/     DAG/worker/retry/HITL/metrics (dùng ở memory mode local)
  config/           pydantic-settings (.env)
  dashboard.py      inbox API read-only (port 8001) · inbox_cli.py CLI viewer
dashboard-ui/       web tĩnh trỏ về API base
tests/              unit + e2e, token-free (test_telegram_chat.py mới)
```

## Chưa bật mặc định (giữ trong repo)

- Full async stack RabbitMQ/Kafka/Postgres/Prometheus/Grafana (`docker-compose.yml`, chi tiết phần "Async Task Queue Engine" bên dưới) — chỉ khi cần loadtest/scale workers.
- Deploy cloud Render / Railway / HF Spaces (`render.yaml`, `railway.json`, `Dockerfile`, `app.py`, `space/`, `deploy/`) — local-first hiện tại để vòng update nhanh.

## Async Task Queue Engine (RabbitMQ + Kafka)

8 loops: context → planning → dispatch → execute → verify → reliability → evaluation → learning/audit. **RabbitMQ không phải kiến trúc** — nó chỉ là *implementation của loop 3* (Task Dispatch); luồng 8-loop transport-agnostic, swap bất kỳ `MessageBus` nào behavior agentic (plan → verify → recover → evaluate → learn) vẫn giữ nguyên. Kiến trúc + DoD checklist ở cuối phần này.

**Problem:** Một hệ thống xử lý task qua hàng đợi phải đảm bảo hai điều: (1) một task không bao giờ bị thực thi hai lần, và (2) một lỗi ở thành phần phụ (ví dụ ghi audit log) không được phép làm sập cả task chính.

### Architecture

Consumer pool đọc task từ hàng đợi (RabbitMQ, DLQ + ACK). Có nhánh song song ghi sự kiện ra event bus phục vụ audit log, tách khỏi luồng xử lý task chính.

**Event bus: Kafka** (`bitnami/kafka:3.8` trong `docker-compose.yml`; implementation `KafkaEventBus` trong `src/hermes/async_engine/eventbus.py`). Topic pattern: `hermes.task.created / .started / .completed / .failed / .retried`. RabbitMQ = "execute this task"; Kafka = "what happened to this task".

### Bug 1 — Race condition gây double execution (HERMES-04-FU1)

**Phát hiện:** 2 consumer có thể cùng lúc claim một task, dẫn đến task bị thực thi 2 lần — nghiêm trọng với các hành động có side effect thật (ví dụ tạo ticket 2 lần, gửi thông báo 2 lần).

**Fix:** Dùng atomic compare-and-set khi claim task (`store.mark_started` trong `src/hermes/async_engine/store.py`):

```python
# Single conditional UPDATE — CAS happens inside the SQL statement
n = self._exec_count(
    "UPDATE execution_state SET state=?, updated_at=? "
    "WHERE task_id=? AND state NOT IN (?,?)",
    (TaskStatus.RUNNING.value, _now(), task_id,
     TaskStatus.COMPLETED.value, TaskStatus.RUNNING.value),
)
# Exactly one consumer gets rowcount==1; the loser gets 0 → must not execute
```

Đảm bảo chỉ một consumer duy nhất "thắng" quyền xử lý. Worker kiểm tra kết quả `mark_started` — nếu `False` thì ACK và bỏ qua (không thực thi handler).

### Bug 2 — Event bus lỗi làm sập cả task (HERMES-10)

**Phát hiện:** Khi event bus (audit log) gặp sự cố, toàn bộ task bị đánh fail — dù về logic, lỗi ghi log không nên ảnh hưởng tới việc task chính đã hoàn thành đúng hay chưa.

**Fix:** Chuyển ghi audit log sang mô hình fire-and-forget, tách khỏi critical path của task. Hai lớp bảo vệ:

1. `KafkaEventBus.emit()` — swallow exceptions bằng `try/except` + `pass` (line 116-117 trong `eventbus.py`)
2. `emit_best_effort()` trong `eventbus.py` — single chokepoint cho mọi call site, đảm bảo invariant: audit bus failure không bao giờ propagate lên task execution

```python
def emit_best_effort(bus, event_type, **fields):
    try:
        return bus.emit(event_type, **fields)
    except Exception:
        return None  # observability only — never propagate
```

Verify bằng cách giả lập event bus chết hoàn toàn (`_BrokenEventBus` — mỗi `emit()` đều raise) và xác nhận task chính vẫn xử lý bình thường, COMPLETED như bình thường.

### Test coverage / metrics

**Có — cả hai test case đều có:**

| Test | File | Mô tả |
|------|------|-------|
| Race condition (concurrent claim) | `tests/test_hermes_04_queue_reliability.py::test_mark_started_atomic_under_concurrency` | 8 threads cùng lúc claim 1 task qua `threading.Barrier` — assert đúng 1 thread thắng, 7 thread thua |
| Race condition (duplicate messages) | `tests/test_hermes_04_queue_reliability.py::test_concurrent_duplicate_messages_execute_once` | 2 consumers, 2 bản copy cùng task — assert handler chạy ĐÚNG 1 lần |
| Event bus down hoàn toàn | `tests/test_hermes_10_audit_trace.py::test_main_pipeline_completes_when_kafka_down` | `_BrokenEventBus` raise mỗi emit — assert workflow vẫn COMPLETED |
| NoopEvents path | `tests/test_hermes_10_audit_trace.py::test_noop_events_also_lets_pipeline_complete` | Không có bus — pipeline vẫn chạy bình thường |
| Idempotency guard | `tests/test_async_store.py::test_idempotency_never_reexecutes_completed` | Task đã COMPLETED → `mark_started` trả về False |
| Sequential claim guard | `tests/test_async_store.py::test_second_claim_refused` | Task đang RUNNING → claim thứ 2 bị từ chối |

**Đây là điểm đáng nêu:** Test race condition thật dùng `threading.Barrier` để maximize contention (không dùng mock hay sleep-based heuristic). Test Kafka-down dùng `_BrokenEventBus` raise exception thật — không chỉ check "không crash" mà còn verify task state đúng COMPLETED và audit trail bị mất như thiết kế.

### What I'd do next

1. **DLQ monitoring & alerting** — Hiện DLQ (`q.agent.deadletter`) đã hoạt động nhưng chưa có cảnh báo khi message rơi vào. Thêm Prometheus metric `tasks_deadlettered_total` + Grafana alert → on-call biết ngay khi có poison message.
2. **Worker auto-scaling** — Dựa trên queue depth (`bus.queue_depth`) để scale worker pool động. Hiện `WorkerPool` fixed-size; có thể thêm `Autoscaler` loop scale up khi depth > threshold, scale down khi idle.
3. **Gated integration tests với real brokers** — `test_async_integration.py` đã có sẵn nhưng gated (chỉ chạy khi có RabbitMQ/Kafka thật). CI pipeline nên có job riêng chạy full stack qua `docker-compose.yml` để verify contract với real infrastructure.
4. **Circuit breaker metrics dashboard** — `loops/reliability.py` có circuit breaker nhưng chưa có Grafana panel cho breaker state (open/closed/half-open). Thêm metric `circuit_breaker_state{agent_type="..."}`.
5. **Exactly-once delivery với idempotent producer** — Hiện idempotency ở DB level (CAS). Có thể bổ sung Kafka idempotent producer (`enable.idempotence=true`) để tránh duplicate events khi Kafka retry.
6. **Audit log replay từ Kafka** — Khi audit trail quan trường (compliance), cần khả năng replay events từ Kafka topic để reconstruct task history. Thêm `AuditReader` consumer group.

## Async Task Queue Engine — Definition of Done (status)

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
| Guardrails (input/output/tool)   | `loops/guardrails.py`                       | ✅   |
| Policy Engine (ALLOW/DENY/APPR)  | `loops/policy.py`                           | ✅   |
| Human-in-the-loop approval       | `loops/hitl.py` (SQLite + auto-approve)     | ✅   |
| Model Gateway (multi-provider)    | `llm/gateway.py`                            | ✅   |
| HERMES-01 spawn limits           | `budgets.validate_graph_budget` + planner cap | ✅ |
| HERMES-02 agent×worker matrix    | `loadtest.run_agent_worker_matrix`          | ✅   |
| HERMES-03 independent verifier   | `verify.TaskAwareValidator` (recompute from payload) | ✅ |
| HERMES-04 prefetch               | `backends.RabbitMQBus(prefetch_count=…)`    | ✅   |
| HERMES-05 DB-level idempotency   | `store` unique idx + upsert on task_results | ✅   |
| HERMES-06 DAG deadlock guard     | failure cascade + stuck-task sweep          | ✅   |
| HERMES-08 full lifecycle states  | `TIMEOUT/CANCELLED/DEADLETTER` transitions  | ✅   |
| HERMES-09 cost budget STOP       | `budgets.CostTracker` (non-retryable stop)  | ✅   |
| Audit tests (risk matrix)        | `tests/test_hermes_audit.py`                | ✅   |
| HERMES-07 retry taxonomy         | `retry.py` classify_failure + tests        | ✅   |
| HERMES-11 memory trust boundary  | `auth/trust.py` + `auth/memory.py` (scaffold) | ✅ |

## Secrets

Không commit `.env`, `*.db`. Tokens chỉ qua env: `CLOUDFLARE_API_TOKEN`, `TELEGRAM_BOT_TOKEN`.
