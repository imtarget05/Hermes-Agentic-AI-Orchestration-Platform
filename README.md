# Hermes — Production Multi-Agent / Multi-Agent RAG Platform
## Enterprise Procurement Case Agent (50 laptops · 3 quotes → recommendation)

Agentic AI runtime: **Gateway/Router → Planner (DAG) → RAG-backed specialists
→ Analysis → Verification → Human Approval (Telegram) → Purchase request**.
Execution runs on the async engine: **RabbitMQ work distribution, worker pool,
DAG dependencies, retry/DLX, manual ACK, idempotency, Postgres task state,
Kafka audit events, Prometheus/Grafana** (see [`README_P1.md`](./README_P1.md)).

```
Procurement Request (+ quote PDFs)
        ↓
   Hermes Planner ──→ DAG (async_engine.dag.TaskDAG)
        ↓
     RabbitMQ (critical path) ──→ Worker Pool (1/2/4/8, manual ACK, retry→DLQ)
  ┌──────┼────────┬─────────┐
  ↓      ↓        ↓         ↓
Price  Vendor  Contract  Specification     ← RAG-backed (retrieve_evidence:
  │      │        │         │                 quotes + vendors.json + spec)
  └──────┴────────┴─────────┘
                ↓
        Analysis Agent (join: lowest approved + warranty/SLA)
                ↓
     Verification Agent (evidence-grounded: every claim cites a source)
                ↓
   Recommendation (PENDING_APPROVAL)
                ↓
   Human Approval ── Telegram ✅/❌ buttons (ApprovalStore) or API resolve
                ↓
        Purchase request        Kafka: task.created/started/completed/failed/retried
```

- **Task lifecycle** with explicit status transitions (`validate_transition`) — every step is persisted as a `TaskEvent` in SQLite.
- **Multi-Agent RAG** (`src/hermes/rag/`) — per-case BM25-lite index over quotes + vendor registry + spec; specialists cite `RAG-EVIDENCE [source=…]`; optional embedding hook upgrades scoring to cosine.
- **Parallel speedup** — `make procurement-bench` runs the case × {1,2,4} workers (measured 9.11s → 4.58s, **~2x**, with 1500ms simulated LLM latency per agent; 4 specialists fan out in parallel, analysis/verification join); engine-level 10/50/100/500-task load tests live in `README_P1.md`.
- **Notifier abstraction** — Telegram bot with Approve/Reject inline buttons when `TELEGRAM_BOT_TOKEN` is set, deterministic mock otherwise (tests stay token-free). Standalone poller: `make approval-bot`.
- **Dashboard API** — FastAPI over the task store.

## Project layout

```
src/hermes/
  gateway/     entrypoint + orchestration loop (--once, --quote PDFs)
  router/      intent classification, Project→channel→thread registry
  orchestrator/ procurement DAG bridge (legacy fanout/pipeline/critic mapped)
  agents/      price / vendor / contract / spec / analysis / verification
  rag/         BM25-lite case index + ingest (quotes/vendors/spec) + embed hook
  procurement/ graph builder, store-aware handlers, DAG runner, benchmark
  tools/       tool registry + executor (parse_quote_pdf, compare_prices,
               check_approved_vendor, extract_contract_terms, score_spec,
               retrieve_evidence, sandboxed file ops)
  tasks/       Pydantic schemas + SQLite TaskStore (lifecycle-safe)
  messaging/   notifier abstraction (Telegram approve buttons / mock) + approval_bot
  llm/         Cloudflare Workers AI client
  dashboard.py read-only FastAPI inbox
  inbox_cli.py CLI inbox viewer
  config/      pydantic-settings (env-driven)
tests/         unit + e2e (mock notifier, fake LLM)
```

## Quickstart (local, mock-first — no token needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # or: make install
cp .env.example .env
make test

HERMES_HITL_AUTO_APPROVE=true make demo-procurement   # 50 laptops → Lenovo (RAG + DAG + verify)
make procurement-bench                                # parallel speedup 1/2/4 workers
python -m hermes.inbox_cli --limit 10
uvicorn hermes.dashboard:app --port 8001
```

### Makefile targets

| Target | Mô tả |
|---|---|
| `make install` | Tạo `.venv` + install `.[dev]` |
| `make test` | Chạy pytest |
| `make demo-procurement` | Demo case mua 50 laptop (DAG + RAG + verification + approval) |
| `make procurement-bench` | Benchmark speedup 1/2/4 workers |
| `make approval-bot` | Chạy Telegram poller duyệt Approve/Reject |
| `make inbox` | Xem inbox qua CLI |
| `make api` | Chạy Dashboard API (port 8001) |
| `make verify` | test + demo + inbox |

Optional extras: `pip install -e ".[langgraph]"` (LangGraph orchestrator), `pip install -e ".[telegram]"` (Telegram delivery).

## LLM — Cloudflare Workers AI (default)

```bash
# .env
LLM_PROVIDER=cloudflare
CLOUDFLARE_ACCOUNT_ID=<account-id>     # Dashboard → Workers AI
CLOUDFLARE_API_TOKEN=<token>           # Workers AI Run permission
CLOUDFLARE_MODEL=@cf/meta/llama-3.1-8b-instruct
```

Without creds, the gateway runs in stub mode (deterministic, tests pass). With creds, all agents (price/vendor/contract/spec/analysis/verification) call Workers AI via `POST /accounts/{id}/ai/run/{model}` — see [`src/hermes/llm/cloudflare.py`](src/hermes/llm/cloudflare.py).

## Configuration

All via env / `.env` (see `.env.example`):

| Var | Mặc định | Ý nghĩa |
|---|---|---|
| `HERMES_DB_PATH` | `./hermes_tasks.db` | SQLite task store (dùng khi không có Postgres) |
| `HERMES_DATABASE_URL` | *(empty)* | Postgres DSN (psycopg3) — set trên Render → dùng Postgres thay SQLite |
| `HERMES_ROUTING_PATH` | `./routing.json` | Project→channel→thread registry |
| `HERMES_SANDBOX_DIR` | `./sandbox` | Working dir cho file tools |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Bật Telegram notifier khi có token |
| `LLM_PROVIDER` | `cloudflare` | Provider cho agents |

## Deployment — Hugging Face Spaces (Gradio SDK) + Neon Postgres — miễn phí, không cần thẻ

### Kiến trúc
- **HF Space (Gradio SDK, free)** → `app.py` mount toàn bộ FastAPI (`hermes.api`) vào Gradio — **giữ 100% feature**: subprocess (`run_shell`), file sandbox, uvicorn port 7860
- **Neon Postgres free** → task store qua `DATABASE_URL` (psycopg3 — hỗ trợ sẵn trong `TaskStore`)
- **Cloudflare Workers AI** → LLM cho agents (free tier)
- **Cloudflare Pages** → dashboard UI, gọi API qua CORS

### Triển khai (5 phút)
1. Tạo Space tại huggingface.co/new-space → SDK chọn **Gradio** (Blank)
2. Upload các file này vào tab **Files** (Add file → Upload files):
   `app.py`, `requirements.txt`, `space/README.md` (đổi tên thành `README.md`, thay thế README gốc của Space), thư mục `src/`, `routing.json`
3. Space → **Settings → Variables and secrets**:
   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Neon connection string (`postgresql://...?sslmode=require`) |
   | `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Workers AI credentials |
   | `CLOUDFLARE_MODEL` | `@cf/meta/llama-3.1-8b-instruct` |
   | `TELEGRAM_BOT_TOKEN` | (bỏ trống → mock notifier) |
   | `HERMES_API_TOKEN` | token bảo vệ `POST /run` |
4. Space tự build → API live tại `https://<user>-<space>.hf.space` — check `/health`, UI nhỏ tại `/gradio`, Swagger tại `/docs`

> API endpoints không đổi so với thiết kế: `/health`, `/run`, `/tasks`, `/tasks/{id}`, `/docs`, `/gradio` (UI nhỏ).
> `Dockerfile` + `render.yaml` giữ trong repo làm phương án thay thế (Render/Docker-host khác).

> Cấu hình Render Blueprint (`render.yaml`) vẫn giữ nguyên như phương án thay thế — cùng một image logic, chỉ đổi hạ tầng.

### API endpoints
| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check + mode (llm/notifier/projects) |
| `POST` | `/procurement/run` | Chạy case full DAG: `{text, project?, user?, quotes?[], quote_paths?[], required_spec?}` — header `X-API-Token` |
| `POST` | `/run` | Alias legacy → procurement pipeline |
| `GET` | `/procurement/approvals/pending` | Approval requests đang chờ manager |
| `POST` | `/procurement/approvals/{id}/resolve` | `{approved, resolver?}` — duyệt/từ chối |
| `GET` | `/tasks?limit=N` | Inbox (danh sách task) |
| `GET` | `/tasks/{id}` | Chi tiết task + events (lifecycle audit) |

### Giới hạn free tier
- HF Space **ngủ sau 48h** không traffic (wake ~1-2 phút)
- Container filesystem ephemeral — task data an toàn trong Neon; `sandbox/` reset khi restart (chỉ ảnh hưởng file demo)
- Neon free: 0.5GB storage, idle-suspend (wake <1s)

## Secrets

Never commit `.env` or `*.db` (both are in `.gitignore`). Tokens via env only: `CLOUDFLARE_API_TOKEN`, `TELEGRAM_BOT_TOKEN`.

