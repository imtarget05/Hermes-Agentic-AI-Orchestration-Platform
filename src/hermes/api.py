"""HTTP API — deployment entrypoint (Render free web service).

Procurement endpoints: POST /procurement/run runs the full DAG
(price‖vendor‖contract‖spec → analysis → verification) and returns the
recommendation; approval endpoints resolve the Telegram/human decision.
Legacy POST /run is kept as an alias. Read endpoints expose the Dashboard/Inbox API.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .runtime import HermesRuntime

app = FastAPI(title="Hermes Procurement API")

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
    strategy: str = "procurement"
    user: str = "local"


class ProcurementRunRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    project: str = ""
    user: str = "local"
    quotes: list[dict] = Field(default_factory=list)
    quote_paths: list[str] = Field(default_factory=list)
    required_spec: str = ""


class ApprovalResolve(BaseModel):
    approved: bool
    resolver: str = "human"


@app.get("/")
def root():
    return {"status": "ok", "service": "hermes-procurement-api", "docs": "/docs", "health": "/health"}


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
    """Legacy alias → procurement pipeline."""
    _check_auth(x_api_token)
    return procurement_run(
        ProcurementRunRequest(text=req.text, project=req.project, user=req.user),
        x_api_token,
    )


@app.post("/procurement/run")
def procurement_run(req: ProcurementRunRequest, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    r = runtime()
    try:
        task = r.run_procurement(req.text, req.project, req.user,
                                 quotes=req.quotes or None,
                                 quote_paths=req.quote_paths or None,
                                 required_spec=req.required_spec)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"task failed: {str(e)[:300]}")
    import json as _json
    try:
        recommendation = _json.loads(task.result.split("\n", 1)[-1]) if task.result else {}
    except Exception:
        recommendation = {"raw": task.result[:2000]}
    return {"task": task.model_dump(), "events": r.store.events(task.id),
            "recommendation": recommendation}


@app.get("/procurement/approvals/pending")
def approvals_pending(x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    from .async_engine.loops.hitl import ApprovalStore
    from .procurement.pipeline import default_procurement_db
    store = ApprovalStore(default_procurement_db(runtime().settings.hermes_db_path))
    return {"pending": store.pending()}


@app.post("/procurement/approvals/{request_id}/resolve")
def approval_resolve(request_id: str, body: ApprovalResolve,
                     x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    from .messaging.approval_bot import resolve_approval
    from .procurement.pipeline import default_procurement_db
    rec = resolve_approval(request_id, body.approved, resolver=body.resolver,
                           proc_db=default_procurement_db(runtime().settings.hermes_db_path),
                           sync_db=runtime().settings.hermes_db_path)
    if rec is None:
        raise HTTPException(404, "approval request not found")
    return {"approval": rec}


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
