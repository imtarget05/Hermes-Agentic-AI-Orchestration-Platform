# Hermes — Agentic AI Orchestration Platform

Agentic AI runtime: **Gateway/Router → Orchestrator → specialized agents → tools → task-state → Notifier (Telegram / mock / API)**.

Spec: [`02_AGENTIC_MULTI_AGENT_PLATFORM.md`](./02_AGENTIC_MULTI_AGENT_PLATFORM.md)

> **Project 1 — Async Task Queue + Parallel Agent Workers + Event Audit** is here:
> [`README_P1.md`](./README_P1.md) — RabbitMQ work distribution, manual-ACK worker
> pool, retry/DLX, idempotency, DAG execution, Kafka lifecycle events,
> Prometheus/Grafana, load tests with parallel speedup.

## Architecture

```
                 ┌─────────────┐
 message ──────► │   Gateway   │  CLI entry, orchestration loop
                 └──────┬──────┘
                        ▼
                 ┌─────────────┐
                 │Router Agent │  classify intent → pick agents + strategy
                 └──────┬──────┘
                        ▼
                 ┌───────────────┐
                 │  Orchestrator │  fanout / pipeline / critic (LangGraph
                 └──────┬────────┘  optional; pure-python fallback)
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
      Research       Builder      Validator/Critic     ← agents (LLM-backed)
          └─────────────┼─────────────┘
                        ▼
                 ┌─────────────┐
                 │ToolExecutor │  web search / file sandbox / registry
                 └──────┬──────┘
                        ▼
              TaskStore (SQLite) ──► Notifier (Telegram / mock) / Dashboard API
```

- **Task lifecycle** with explicit status transitions (`validate_transition`) — every step is persisted as a `TaskEvent` in SQLite.
- **Notifier abstraction** — Telegram bot when `TELEGRAM_BOT_TOKEN` is set, deterministic mock otherwise (tests stay token-free).
- **Dashboard API** — read-only FastAPI over the task store.

## Project layout

```
src/hermes/
  gateway/     entrypoint + orchestration loop (--once / strategies)
  router/      intent classification, Project→channel→thread registry
  orchestrator/ fanout · pipeline · critic strategies
  agents/      research / builder / validator agents
  tools/       tool registry + executor (web search, sandboxed file ops)
  tasks/       Pydantic schemas + SQLite TaskStore (lifecycle-safe)
  messaging/   notifier abstraction (Telegram / mock)
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

python -m hermes.gateway --once --text "research python 3.12" --project demo --strategy fanout
python -m hermes.gateway --once --text "build hello" --project demo --strategy pipeline
python -m hermes.inbox_cli --limit 10
uvicorn hermes.dashboard:app --port 8001
```

### Makefile targets

| Target | Mô tả |
|---|---|
| `make install` | Tạo `.venv` + install `.[dev]` |
| `make test` | Chạy pytest |
| `make demo-fanout` / `demo-pipeline` / `demo-critic` | Demo từng strategy |
| `make inbox` | Xem inbox qua CLI |
| `make api` | Chạy Dashboard API (port 8001) |
| `make verify` | test + toàn bộ demo |

Optional extras: `pip install -e ".[langgraph]"` (LangGraph orchestrator), `pip install -e ".[telegram]"` (Telegram delivery).

## LLM — Cloudflare Workers AI (default)

```bash
# .env
LLM_PROVIDER=cloudflare
CLOUDFLARE_ACCOUNT_ID=<account-id>     # Dashboard → Workers AI
CLOUDFLARE_API_TOKEN=<token>           # Workers AI Run permission
CLOUDFLARE_MODEL=@cf/meta/llama-3.1-8b-instruct
```

Without creds, the gateway runs in stub mode (deterministic, tests pass). With creds, all agents (research/builder/validator) call Workers AI via `POST /accounts/{id}/ai/run/{model}` — see [`src/hermes/llm/cloudflare.py`](src/hermes/llm/cloudflare.py).

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
| `POST` | `/run` | Chạy task full pipeline: `{text, project?, strategy? (fanout/pipeline/critic), user?}` — header `X-API-Token` |
| `GET` | `/tasks?limit=N` | Inbox (danh sách task) |
| `GET` | `/tasks/{id}` | Chi tiết task + events (lifecycle audit) |

### Giới hạn free tier
- HF Space **ngủ sau 48h** không traffic (wake ~1-2 phút)
- Container filesystem ephemeral — task data an toàn trong Neon; `sandbox/` reset khi restart (chỉ ảnh hưởng file demo)
- Neon free: 0.5GB storage, idle-suspend (wake <1s)

## Secrets

Never commit `.env` or `*.db` (both are in `.gitignore`). Tokens via env only: `CLOUDFLARE_API_TOKEN`, `TELEGRAM_BOT_TOKEN`.

