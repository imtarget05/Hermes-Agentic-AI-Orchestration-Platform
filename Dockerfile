FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY routing.json ./

# async extras: pika (RabbitMQ) + prometheus-client; confluent-kafka optional at runtime
RUN pip install --no-cache-dir -e ".[dev,mq,metrics]"

RUN mkdir -p sandbox

ENV HERMES_DB_PATH=/app/hermes_tasks.db \
    HERMES_ROUTING_PATH=/app/routing.json \
    HERMES_SANDBOX_DIR=/app/sandbox \
    HERMES_ASYNC_MODE=rabbitmq \
    LLM_PROVIDER=cloudflare \
    PYTHONUNBUFFERED=1

EXPOSE 7860

CMD ["uvicorn", "hermes.api:app", "--host", "0.0.0.0", "--port", "7860"]
