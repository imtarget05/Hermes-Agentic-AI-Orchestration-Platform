"""Loop 8 — LEARNING / AUDIT LOOP.

Kafka events + execution traces + failure patterns -> improve routing/planning.

This closes the agentic loop: mined failure patterns become a policy the
Planning loop (loop 2) reads on the next request (retry budgets per task type).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

POLICY_PATH_DEFAULT = "./hermes_policy.json"


class LearningLoop:
    """Mines execution history into (a) failure patterns, (b) a planning policy."""

    def __init__(self, store, policy_path: str = POLICY_PATH_DEFAULT):
        self.store = store
        self.policy_path = Path(policy_path)

    # ---- failure pattern mining ----------------------------------------- #
    def failure_patterns(self, limit: int = 200) -> dict[str, Any]:
        """Group terminal failures by task_type + error class -> counts/rates."""
        rows = self.store._exec(
            "SELECT task_type, COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed "
            "FROM tasks GROUP BY task_type", fetch="all") or []
        patterns: dict[str, Any] = {}
        for r in rows:
            d = dict(r)
            if not d["failed"]:
                continue
            errors = self.store._exec(
                "SELECT error, COUNT(*) AS c FROM tasks "
                "WHERE task_type = ? AND status = 'failed' AND error != '' "
                "GROUP BY error ORDER BY c DESC LIMIT 3", (d["task_type"],),
                fetch="all") or []
            patterns[d["task_type"]] = {
                "failure_rate": round(d["failed"] / d["total"], 3) if d["total"] else 0.0,
                "total": d["total"],
                "top_errors": [dict(e) for e in errors],
            }
        return patterns

    # ---- policy feedback (loop 8 -> loop 2) ------------------------------ #
    def policy_suggestions(self) -> dict[str, Any]:
        """Turn failure patterns into concrete planning adjustments."""
        suggestions: dict[str, Any] = {"max_attempts": {}, "notes": []}
        for task_type, p in self.failure_patterns().items():
            if p["failure_rate"] >= 0.5:
                # chronically failing type -> more retry budget + note for routing
                suggestions["max_attempts"][task_type] = 4
                suggestions["notes"].append(
                    f"{task_type}: failure_rate={p['failure_rate']} — raised retry budget, "
                    f"top error: {p['top_errors'][0]['error'][:80] if p['top_errors'] else 'n/a'}")
            elif p["failure_rate"] <= 0.05:
                suggestions["max_attempts"].setdefault(task_type, 2)
                suggestions["notes"].append(
                    f"{task_type}: healthy ({p['failure_rate']}) — trimmed retry budget")
        return suggestions

    def refresh_policy(self) -> dict[str, Any]:
        """Re-mine + persist the policy the Planner will consult."""
        policy = self.policy_suggestions()
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
        return policy

    def load_policy(self) -> dict[str, Any]:
        try:
            return json.loads(self.policy_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ---- execution traces ------------------------------------------------- #
    def traces(self, workflow_id: str) -> list[dict[str, Any]]:
        """Full execution trace for one workflow (tasks + results + transitions)."""
        out = []
        for t in self.store.list_workflow_tasks(workflow_id):
            out.append({
                "task_id": t.task_id, "task_type": t.task_type,
                "status": t.status.value, "attempt": t.attempt,
                "results": self.store.task_results(t.task_id),
            })
        return out