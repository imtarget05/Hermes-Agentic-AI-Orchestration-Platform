---
title: Hermes API
emoji: ⚡
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: "6.26.0"
python_version: "3.12"
app_file: app.py
hardware: cpu-basic
pinned: false
---

Hermes — Agentic AI Orchestration Platform (HTTP API mounted in a free CPU Gradio Space).

## Endpoints

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `POST` | `/run` | Chạy task (fanout/pipeline/critic) — header `X-API-Token` |
| `GET` | `/tasks` | Inbox |
| `GET` | `/tasks/{id}` | Chi tiết + lifecycle events |
| `GET` | `/docs` | Swagger |

Setup secrets (Settings → Variables and secrets): `DATABASE_URL`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_MODEL`, `TELEGRAM_BOT_TOKEN`, `HERMES_API_TOKEN`.
