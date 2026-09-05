"""
Hermes on Hugging Face Spaces (Gradio SDK — free tier).

Mounts the full FastAPI app (hermes.api) into a Gradio wrapper so the
Space satisfies the Gradio SDK requirement while exposing every API
route unchanged: /health, /run, /tasks, /tasks/{id}, /docs.
"""
import os
import sys

# Make src/ importable without `pip install .` (avoids Space build backend issues)
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import gradio as gr

from hermes.api import app as fastapi_app

with gr.Blocks(title="Hermes — Agentic AI Platform") as demo:
    gr.Markdown(
        """
# ⚡ Hermes — Agentic AI Orchestration Platform

API đang chạy tại chính Space này:

| Endpoint | Mô tả |
|---|---|
| `GET /health` | Health check |
| `POST /procurement/run` | Chạy case (DAG + RAG + verification) — header `X-API-Token` |
| `POST /procurement/approvals/{id}/resolve` | Duyệt / từ chối (manager) |
| `GET /tasks` | Inbox |
| `GET /tasks/{id}` | Chi tiết task + lifecycle events |
| `GET /docs` | Swagger UI |

Dashboard UI: triển khai riêng trên Cloudflare Pages, trỏ API base về URL của Space.
"""
    )

# FastAPI routes take precedence; Gradio UI served under /gradio
app = gr.mount_gradio_app(fastapi_app, demo, path="/gradio")
