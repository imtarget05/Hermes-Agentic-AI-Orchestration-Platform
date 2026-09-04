FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY routing.json ./

RUN pip install --no-cache-dir -e .

RUN mkdir -p sandbox

ENV HERMES_DB_PATH=/app/hermes_tasks.db \
    HERMES_ROUTING_PATH=/app/routing.json \
    HERMES_SANDBOX_DIR=/app/sandbox \
    LLM_PROVIDER=cloudflare \
    PYTHONUNBUFFERED=1

EXPOSE 7860

CMD ["uvicorn", "hermes.api:app", "--host", "0.0.0.0", "--port", "7860"]
