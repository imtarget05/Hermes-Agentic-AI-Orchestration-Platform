"""HTTP API — deployment entrypoint (Render free web service).

Procurement endpoints: POST /procurement/run runs the full DAG
(price‖vendor‖contract‖spec → analysis → verification) and returns the
recommendation; approval endpoints resolve the Telegram/human decision.
Legacy POST /run is kept as an alias. Read endpoints expose the Dashboard/Inbox API.
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .auth import ApprovalPolicy, RBACEngine, User
from .runtime import HermesRuntime, get_runtime

app = FastAPI(title="Hermes Procurement API")

# Telegram 1:1 chat webhook shares the API process/port (local tunnel → :8000).
# The router is import-safe without python-telegram-bot installed: all Bot
# imports are lazy inside the route handlers.
from .telegram_chat import webhook as _telegram_webhook  # noqa: E402

app.include_router(_telegram_webhook.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("HERMES_CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

API_TOKEN = os.environ.get("HERMES_API_TOKEN", "")
_rbac_engine: RBACEngine | None = None


def _get_rbac_engine() -> RBACEngine:
    global _rbac_engine
    if _rbac_engine is None:
        _rbac_engine = RBACEngine()
        _seed_default_rbac(_rbac_engine)
    return _rbac_engine


def _seed_default_rbac(engine: RBACEngine) -> None:
    from .auth.models import Tenant
    tenant = Tenant.create("default", "Default Tenant")
    engine.add_tenant(tenant)
    engine.add_policy(ApprovalPolicy.default_for_tenant("default"))
    
    from .auth.models import User
    users = [
        User.create("user_1", "default", "alice", "alice@example.com", ["procurement_requester"]),
        User.create("user_2", "default", "bob", "bob@example.com", ["procurement_manager"]),
        User.create("user_3", "default", "carol", "carol@example.com", ["finance_approver"]),
        User.create("user_4", "default", "dave", "dave@example.com", ["admin"]),
    ]
    for user in users:
        engine.add_user(user)


def _extract_tenant_context(request: Request, x_api_token: str | None) -> str:
    """Extract tenant_id from request headers or API token."""
    tenant_header = request.headers.get("X-Tenant-ID")
    if tenant_header:
        return tenant_header
    
    if x_api_token and ":" in x_api_token:
        return x_api_token.split(":")[0]
    
    return "default"


def _get_user_from_request(request: Request, x_api_token: str | None) -> User | None:
    """Extract user from request context."""
    engine = _get_rbac_engine()
    
    user_header = request.headers.get("X-User-ID")
    if user_header:
        return engine.get_user(user_header)
    
    username_header = request.headers.get("X-Username")
    tenant_id = _extract_tenant_context(request, x_api_token)
    if username_header:
        return engine.get_user_by_username(tenant_id, username_header)
    
    if x_api_token and ":" in x_api_token:
        _, user_part = x_api_token.split(":", 1)
        return engine.get_user(user_part)
    
    return None


def runtime() -> HermesRuntime:
    return get_runtime()


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
def procurement_run(req: ProcurementRunRequest, x_api_token: str | None = Header(default=None),
                    request: Request = None):
    _check_auth(x_api_token)
    
    if request is not None:
        user = _get_user_from_request(request, x_api_token)
        if user and not _get_rbac_engine().check_permission(user, "procurement:create"):
            raise HTTPException(403, "User lacks procurement:create permission")
        tenant_id = _extract_tenant_context(request, x_api_token)
    else:
        tenant_id = "default"
    
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
            "recommendation": recommendation, "tenant_id": tenant_id}


@app.get("/procurement/approvals/pending")
def approvals_pending(x_api_token: str | None = Header(default=None),
                      request: Request = None):
    _check_auth(x_api_token)
    
    tenant_id = "default"
    if request is not None:
        tenant_id = _extract_tenant_context(request, x_api_token)
        user = _get_user_from_request(request, x_api_token)
        if user and not _get_rbac_engine().check_permission(user, "procurement:view"):
            raise HTTPException(403, "User lacks procurement:view permission")
    
    from .async_engine.loops.hitl import ApprovalStore
    from .procurement.pipeline import default_procurement_db
    store = ApprovalStore(default_procurement_db(runtime().settings.hermes_db_path))
    pending = store.pending()
    filtered = [p for p in pending if _extract_tenant_from_approval(p) == tenant_id]
    return {"pending": filtered, "tenant_id": tenant_id}


def _extract_tenant_from_approval(rec: dict) -> str:
    import json
    try:
        args = json.loads(rec.get("args") or "{}")
        return args.get("tenant_id", "default")
    except Exception:
        return "default"


@app.post("/procurement/approvals/{request_id}/resolve")
def approval_resolve(request_id: str, body: ApprovalResolve,
                     x_api_token: str | None = Header(default=None),
                     request: Request = None):
    _check_auth(x_api_token)
    
    tenant_id = "default"
    if request is not None:
        tenant_id = _extract_tenant_context(request, x_api_token)
        user = _get_user_from_request(request, x_api_token)
        
        from .async_engine.loops.hitl import ApprovalStore
        from .messaging.approval_bot import resolve_approval
        from .procurement.pipeline import default_procurement_db
        
        store = ApprovalStore(default_procurement_db(runtime().settings.hermes_db_path))
        rec = store.get(request_id)
        if rec is None:
            raise HTTPException(404, "approval request not found")
        
        amount = 0.0
        try:
            args = json.loads(rec.get("args") or "{}")
            amount = float(args.get("amount", 0) or args.get("total", 0) or 0)
        except Exception:
            pass
        
        if user:
            can_approve, reason = _get_rbac_engine().user_can_approve(user, amount)
            if not can_approve:
                raise HTTPException(403, f"User cannot approve: {reason}")
            
            policy = _get_rbac_engine().get_or_create_default_policy(tenant_id)
            requester_id = None
            try:
                args = json.loads(rec.get("args") or "{}")
                requester_id = args.get("requester_id") or args.get("user_id")
            except Exception:
                pass
            
            if requester_id and policy.separation_of_duties and requester_id == user.id:
                raise HTTPException(403, "Separation of duties violation: requester cannot approve own request")
    
    from .messaging.approval_bot import resolve_approval
    from .procurement.pipeline import default_procurement_db
    rec = resolve_approval(request_id, body.approved, resolver=body.resolver,
                           proc_db=default_procurement_db(runtime().settings.hermes_db_path),
                           sync_db=runtime().settings.hermes_db_path)
    if rec is None:
        raise HTTPException(404, "approval request not found")
    return {"approval": rec}


@app.get("/tasks")
def list_tasks(limit: int = 20, x_api_token: str | None = Header(default=None),
               request: Request = None):
    _check_auth(x_api_token)
    return runtime().store.list_tasks(limit)


@app.get("/tasks/{task_id}")
def get_task(task_id: str, x_api_token: str | None = Header(default=None),
             request: Request = None):
    _check_auth(x_api_token)
    r = runtime()
    try:
        return {"task": r.store.get(task_id).model_dump(), "events": r.store.events(task_id)}
    except KeyError:
        raise HTTPException(404, "task not found")


@app.get("/auth/tenants")
def list_tenants(x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    engine = _get_rbac_engine()
    return {"tenants": [t.to_dict() for t in engine._tenants.values()]}


@app.get("/auth/users")
def list_users(tenant_id: str = "default", x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    engine = _get_rbac_engine()
    users = [u.to_dict() for u in engine._users.values() if u.tenant_id == tenant_id]
    return {"users": users, "tenant_id": tenant_id}


@app.get("/auth/roles")
def list_roles(x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    engine = _get_rbac_engine()
    return {"roles": [r.to_dict() for r in engine._roles.values()]}


@app.get("/auth/policies/{tenant_id}")
def get_policy(tenant_id: str, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    engine = _get_rbac_engine()
    policy = engine.get_or_create_default_policy(tenant_id)
    return policy.to_dict()


# ---- Phase 1-4 domain endpoints (knowledge / advisor / ops / competitor) ----

class KnowledgeIngest(BaseModel):
    title: str = ""
    content: str
    scope: str = "team"          # team | personal
    category: str = "other"
    source_uri: str = ""


class AdvisorAsk(BaseModel):
    question: str
    personas: list[str] = Field(default_factory=list)
    context: str = ""


class OpsSourceAdd(BaseModel):
    kind: str = "crm"            # crm | invoicing | calendar | inbox | custom
    name: str = ""
    enabled: bool = True


class CompetitorWatch(BaseModel):
    competitor: str
    urls: list[str] = Field(default_factory=list)
    feeds: list[str] = Field(default_factory=list)


def _svc():
    return runtime().services()


@app.post("/knowledge/ingest")
def knowledge_ingest(req: KnowledgeIngest, request: Request = None,
                     x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    user = _get_user_from_request(request, x_api_token)
    user_id = user.id if user else ""
    if req.scope not in ("team", "personal"):
        raise HTTPException(422, "scope must be team|personal")
    if req.scope == "personal" and not user_id:
        raise HTTPException(422, "personal scope requires X-User-ID")
    doc_id = _svc()["knowledge"].ingest(
        content=req.content, title=req.title, scope=req.scope,
        tenant_id=_extract_tenant_context(request, x_api_token),
        user_id=user_id, category=req.category, source_uri=req.source_uri)
    return {"doc_id": doc_id, "scope": req.scope}


@app.get("/knowledge/query")
def knowledge_query(q: str, scope: str = "team", request: Request = None,
                    x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    user = _get_user_from_request(request, x_api_token)
    if scope not in ("team", "personal"):
        raise HTTPException(422, "scope must be team|personal")
    ans = _svc()["knowledge"].query(
        text=q, scope=scope, tenant_id=_extract_tenant_context(request, x_api_token),
        user_id=user.id if user else "")
    return ans.model_dump()


@app.post("/advisor/ask")
def advisor_ask(req: AdvisorAsk, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    report = _svc()["council"].ask(req.question, req.personas or None, req.context)
    return report.model_dump()


@app.get("/ops/sources")
def ops_sources(request: Request = None, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    return {"sources": _svc()["ops"].sources_json(
        _extract_tenant_context(request, x_api_token))}


@app.post("/ops/sources")
def ops_add_source(req: OpsSourceAdd, request: Request = None,
                   x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    src = _svc()["ops"].add_source(
        kind=req.kind, tenant_id=_extract_tenant_context(request, x_api_token),
        name=req.name, enabled=req.enabled)
    return src.model_dump()


@app.delete("/ops/sources/{source_id}")
def ops_remove_source(source_id: str, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    ok = _svc()["ops"].remove_source(source_id)
    if not ok:
        raise HTTPException(404, "source not found")
    return {"removed": source_id}


@app.get("/ops/attention")
def ops_attention(request: Request = None, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    rep = _svc()["ops"].collect_attention(_extract_tenant_context(request, x_api_token))
    return rep.model_dump()


@app.post("/competitor/watch")
def competitor_watch(req: CompetitorWatch, x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    from .competitor import CompetitorTarget
    target = CompetitorTarget(competitor=req.competitor, urls=req.urls, feeds=req.feeds)
    _svc()["competitor_targets"].append(target)
    return {"watching": req.competitor, "targets": len(_svc()["competitor_targets"])}


@app.get("/competitor/brief")
def competitor_brief(x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    from .competitor import build_weekly_brief
    svc = _svc()
    findings = svc["competitor"].collect(svc["competitor_targets"])
    brief = build_weekly_brief(findings)
    return brief.model_dump()


@app.get("/harness/metrics")
def harness_metrics(x_api_token: str | None = Header(default=None)):
    _check_auth(x_api_token)
    ev = _svc()["eval"]
    metrics = [m.model_dump() for m in ev.compute_metrics()]
    rate = 0.0
    try:
        from .harness.eval import lifecycle_success_rate
        rate = lifecycle_success_rate(runtime().store)
    except Exception:  # noqa: BLE001
        pass
    return {"metrics": metrics, "lifecycle_success_rate": rate}
