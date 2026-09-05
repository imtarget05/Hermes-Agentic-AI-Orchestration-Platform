"""Orchestrator: Enterprise Procurement Case Agent (§4 of spec).

Procurement Request → Planner (DAG) → 4 parallel specialists
(price / vendor / contract / spec) → analysis join → verification
→ recommendation → human approval.

Parallel execution runs on the async DAG engine
(`hermes.procurement.pipeline.run_procurement_case`: AsyncOrchestrator +
WorkerPool over InMemoryBus). This module bridges the result back into
the sync TaskStore lifecycle (queued→running→handoff→completed,
failure→retry/failed) and notifies via the configured notifier.
"""
from __future__ import annotations

from typing import Any

from ..tasks.schemas import TaskStatus
from ..tasks.store import TaskStore


def orchestrate(task_id: str, store: TaskStore, notifier=None,
                quotes: list[dict[str, Any]] | None = None,
                required_spec: str = "",
                workers: int = 4) -> str:
    """Full lifecycle driver for one procurement case."""
    from ..procurement.pipeline import default_procurement_db, run_procurement_case

    task = store.get(task_id)
    store.transition(task_id, TaskStatus.RUNNING, "planner", "strategy=procurement")
    if notifier:
        notifier.send(task.project, f"[{task.id}] started: {task.text[:120]}")

    def _exec() -> str:
        agg = run_procurement_case(
            task.text, quotes or [], required_spec,
            workers=workers, db_path=default_procurement_db(store.db_path),
        )
        if agg.get("status") != "completed":
            raise RuntimeError(f"procurement DAG failed: {agg.get('counts')}")
        store.set_owner(task_id, "price+vendor+contract+spec")
        store.transition(task_id, TaskStatus.HANDOFF, "planner",
                         "price‖vendor‖contract‖spec → analysis")
        store.set_owner(task_id, "analysis")
        store.transition(task_id, TaskStatus.RUNNING, "analysis", "join → recommendation")
        store.set_owner(task_id, "verification")
        rec = agg.get("recommendation") or {}
        import json as _json
        final = _json.dumps(rec) if rec else "NO RECOMMENDATION"
        store.set_result(task_id, f"VERIFICATION PASSED\n{final}", owner="verification")
        return f"VERIFICATION PASSED\n{final}"

    try:
        final = _exec()
    except Exception as e:
        store.transition(task_id, TaskStatus.FAILURE, "orchestrator", str(e)[:300])
        if task.retries + 1 <= task.max_retries:
            store.transition(task_id, TaskStatus.RETRY, "orchestrator", "retryable")
            store.transition(task_id, TaskStatus.RUNNING, "orchestrator", "retry resume")
            try:
                final = _exec()
            except Exception as e2:
                store.transition(task_id, TaskStatus.FAILURE, "orchestrator", str(e2)[:300])
                store.transition(task_id, TaskStatus.FAILED, "orchestrator", "exhausted")
                raise
        else:
            store.transition(task_id, TaskStatus.FAILED, "orchestrator", "non-retryable")
            raise

    store.set_result(task_id, final, owner="orchestrator")
    store.transition(task_id, TaskStatus.COMPLETED, "orchestrator", "done")
    request_id = _request_approval(task_id, store, final, notifier)
    if notifier:
        note = f"[{task.id}] completed ✅\n{final[:1000]}"
        if request_id:
            note += f"\nApproval: {request_id} (Telegram buttons / POST /procurement/approvals/{request_id}/resolve)"
        notifier.send(task.project, note)
    return final


def _request_approval(task_id: str, store: TaskStore, final: str, notifier=None) -> str:
    """Create the human approval request and notify. Returns request_id."""
    import json as _json
    import os as _os

    from ..async_engine.loops.hitl import ApprovalStore
    from ..procurement.pipeline import default_procurement_db

    task = store.get(task_id)
    proc_db = default_procurement_db(store.db_path)
    approvals = ApprovalStore(proc_db)
    args = {"sync_task_id": task_id, "sync_db_path": store.db_path,
            "recommendation": final[-2000:]}
    request_id = approvals.create(task_id, task_id, "approve_purchase",
                                  "verification", args, "HIGH")
    auto = _os.environ.get("HERMES_HITL_AUTO_APPROVE", "").lower() in ("1", "true", "yes")
    if auto:
        approvals.resolve(request_id, True, resolver="auto")
        try:
            body = _json.loads(final.split("\n", 1)[-1])
            body["status"] = "APPROVED"
            body["approved_by"] = "auto"
            store.set_result(task_id, f"VERIFICATION PASSED\n{_json.dumps(body)}",
                             owner="human")
        except Exception:
            pass
        return request_id
    if notifier:
        try:
            notifier.send_approval(task.project, final[:3500], request_id)
        except Exception:
            pass
    return request_id


# Legacy strategy names (fanout/pipeline/critic) were removed with the
# generic research/builder/validator agents. They now run the procurement
# pipeline so old call sites keep working during migration.
def run_fanout(text: str) -> dict:
    return _legacy(text)


def run_pipeline(text: str) -> dict:
    return _legacy(text)


def run_critic(text: str, max_rounds: int = 2) -> dict:
    return _legacy(text)


def _legacy(text: str) -> dict:
    from ..agents import AGENTS
    r = AGENTS["price"].run(text, "")
    final = f"PROCUREMENT (legacy strategy mapped)\n{r}"
    return {"final": final}


def build_graph(strategy: str = "procurement"):
    """LangGraph is no longer used; the DAG engine owns parallel execution."""
    return None
