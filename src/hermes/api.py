"""HTTP API — deployment entrypoint (Render free web service).

Exposes every gateway feature over HTTP so the deployed runtime has full
feature parity with the CLI: POST /run covers all 3 orchestrator strategies,
routing registry, task lifecycle, notifier (Telegram/mock) and sandbox tools.
Read endpoints expose the Dashboard/Inbox API.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import settings
from .runtime import HermesRuntime

app = FastAPI(title="Hermes Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("HERMES_CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

API_TOKEN = os.environ.get("HERMES_API_TOKEN", "")
_runtime: HermesRuntime | None = None


def runtime() -> HermesRuntime:
    global _runtime
    if _runtime is None:
        _runtime = HermesRuntime()
    return _runtime


def _check_auth(x_api_token: str | None) -> None:
    if API_TOKEN and x_api_token != API_TOKEN:
        raise HTTPException(401, "invalid or missing X-API-Token")


class RunRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    project: str = ""
    strategy: str = "fanout"
    user: str = "local"


@app.get("/")
def root():
    return {"status": "ok", "service": "hermes-api", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    r = runtime()
    return {
        "status": "ok",
        "llm": r.llm_mode,
        "notifier": r.notifier_mode,
        "projects": r.registry.projects(),
    }


@app.post("/run")
def run(req: RunRequest, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    r = runtime()
    try:
        task = r.run_task(req.text, req.project, req.strategy, req.user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"task failed: {str(e)[:300]}")
    return {"task": task.model_dump(), "events": r.store.events(task.id)}


@app.get("/tasks")
def list_tasks(limit: int = 20, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    return runtime().store.list_tasks(limit)


@app.get("/tasks/{task_id}")
def get_task(task_id: str, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    r = runtime()
    try:
        return {"task": r.store.get(task_id).model_dump(), "events": r.store.events(task_id)}
    except KeyError:
        raise HTTPException(404, "task not found")
