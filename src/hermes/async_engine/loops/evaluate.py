"""Loop 7 — EVALUATION LOOP.

Latency / success / quality / parallel speedup / worker efficiency — computed
from the persisted store (not just Prometheus counters), per workflow and
rolling across recent workflows. Feeds the Learning loop (loop 8).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _ts(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, int(0.95 * len(s)) - 1)]


def workflow_report(store, workflow_id: str) -> dict[str, Any]:
    """Evaluation for one workflow: success, latency, retries, worker efficiency."""
    tasks = store.list_workflow_tasks(workflow_id)
    total = len(tasks)
    completed = [t for t in tasks if t.status.value == "completed"]
    failed = [t for t in tasks if t.status.value == "failed"]
    retries = sum(max(0, t.attempt - 1) for t in tasks)

    latencies = []
    for t in completed:
        results = store.task_results(t.task_id)
        done_at = next((r["created_at"] for r in results if r["status"] == "completed"), "")
        if done_at and t.created_at:
            latencies.append(_ts(done_at) - _ts(t.created_at))

    # worker efficiency straight from the tasks table (worker_id column)
    per_worker: dict[str, int] = {}
    for r in (store._exec("SELECT worker_id, COUNT(*) AS c FROM tasks "
                          "WHERE workflow_id=? AND status='completed' GROUP BY worker_id",
                          (workflow_id,), fetch="all") or []):
        d = dict(r)
        per_worker[d["worker_id"] or "unknown"] = d["c"]

    return {
        "workflow_id": workflow_id,
        "total_tasks": total,
        "completed": len(completed),
        "failed": len(failed),
        "success_rate": round(len(completed) / total, 3) if total else 0.0,
        "retry_count": retries,
        "retry_rate": round(retries / total, 3) if total else 0.0,
        "latency": {
            "avg_seconds": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
            "p95_seconds": round(_p95(latencies), 4),
        },
        "worker_efficiency": per_worker,
        "quality": "pass" if failed == [] and total else "degraded",
    }


def rolling_report(store, limit: int = 20) -> dict[str, Any]:
    """Rolling evaluation across the most recent workflows (loop 7 output)."""
    rows = store._exec(
        "SELECT w.id AS id, w.status AS status, w.created_at AS created_at, "
        "w.completed_at AS completed_at, COUNT(t.task_id) AS total, "
        "SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed "
        "FROM workflows w LEFT JOIN tasks t ON t.workflow_id = w.id "
        "GROUP BY w.id ORDER BY w.created_at DESC LIMIT ?", (limit,), fetch="all",
    ) or []
    workflows = []
    for r in rows:
        d = dict(r)
        total = d["total"] or 0
        workflows.append({
            "workflow_id": d["id"], "status": d["status"],
            "total_tasks": total,
            "success_rate": round((d["done"] or 0) / total, 3) if total else 0.0,
        })
    total_tasks = sum(w["total_tasks"] for w in workflows)
    return {
        "workflows_evaluated": len(workflows),
        "total_tasks": total_tasks,
        "overall_success_rate": round(
            sum(w["success_rate"] * w["total_tasks"] for w in workflows) / total_tasks, 3
        ) if total_tasks else 0.0,
        "workflows": workflows,
    }