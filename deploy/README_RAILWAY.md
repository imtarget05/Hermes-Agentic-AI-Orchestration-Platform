# Hermes — Railway Singapore deployment (APAC chính)

Region chính: **asia-southeast1-eqsg3a** (Singapore) — đã set trong `railway.json`.

## Service layout

| Service | Replicas | Start command | Service variables |
|---|---|---|---|
| `hermes-api` | 1 | `uvicorn hermes.async_api:app --host 0.0.0.0 --port $PORT` | `HERMES_ASYNC_MODE=rabbitmq` |
| `orchestrator` | 1 | `python -m hermes.async_engine.cli orchestrator` | `HERMES_ADVANCE_INTERVAL=0.5` |
| `research-worker` | 2 | `python -m hermes.async_engine.cli work` | `HERMES_WORKER_TASK_TYPES=research` |
| `analyze-worker` | 2 | `python -m hermes.async_engine.cli work` | `HERMES_WORKER_TASK_TYPES=analyze` |
| `report-worker` | 1 | `python -m hermes.async_engine.cli work` | `HERMES_WORKER_TASK_TYPES=report` |

Mọi service dùng chung repo + `Dockerfile` (`railway.json` build DOCKERFILE).

## Shared variables (mỗi service đều cần)

| Var | Value |
|---|---|
| `HERMES_ASYNC_MODE` | `rabbitmq` |
| `HERMES_RABBITMQ_URL` | `amqp://guest:guest@<rabbitmq-service>.railway.internal:5672/%2F` |
| `HERMES_DATABASE_URL` | `postgresql://...` (Railway Postgres, cùng region) |
| `KAFKA_BOOTSTRAP_SERVERS` | `<kafka-service>.railway.internal:9092` (template Kafka) |
| `HERMES_WORKER_TASK_TYPES` | *(chỉ worker services — xem bảng trên)* |

## Infra cùng region Singapore

- **Postgres** — Railway Postgres template (task state, execution_state, task_results)
- **RabbitMQ** — Railway template RabbitMQ (marketplace) — exchanges `hermes.tasks/retry/dlx`
- **Kafka** — Railway template Kafka hoặc Aiven (APAC) — topic `hermes.task.*`

> Railway internal networking: bật "Private Networking" giữa các service; URL dạng
> `<service>.railway.internal`. Không expose RabbitMQ/Kafka ra public domain.

## Scale khi benchmark

Benchmark 1/2/4/8 workers = tăng `numReplicas` của từng worker service
(Railway Settings → Replicas). Workers consume cùng queue nên scale ngang
không cần đổi code:

```bash
railway scale --service analyze-worker --replicas 8
```

## Render Free — không dùng cho hermes-api

Render Free sleep sau 15 phút không traffic, request sau đó spin-up ~1 phút —
trái với mục tiêu demo realtime. Railway service luôn chạy (không sleep).

## CI gate trước khi deploy

`.github/workflows/ci.yml` phải xanh (lint + unit + integration RabbitMQ +
docker build) trước khi `railway up`.
