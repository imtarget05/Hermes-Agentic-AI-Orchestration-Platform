"""Loop 1 — CONTEXT LOOP.

Retrieve state/evidence, understand the task, build the execution context
that every downstream loop reads. This is what makes Hermes agentic rather
than a plain task queue: workers receive *context*, not just payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Execution context attached to every task in a workflow."""

    request: str = ""
    source: str = "api"
    user_id: str = ""
    workflow_state: dict[str, Any] = field(default_factory=dict)
    prior_evidence: list[dict[str, Any]] = field(default_factory=list)
    failure_patterns: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "source": self.source,
            "user_id": self.user_id,
            "workflow_state": self.workflow_state,
            "prior_evidence": self.prior_evidence,
            "failure_patterns": self.failure_patterns,
            "policy": self.policy,
        }


class ContextBuilder:
    """Loop 1: builds ExecutionContext from the store (state + evidence)."""

    def __init__(self, store, audit=None):
        self.store = store
        self.audit = audit  # LearningLoop / audit module (optional)

    def build(self, request: str, source: str = "api", user_id: str = "",
              workflow_id: str = "", recent_limit: int = 5) -> ExecutionContext:
        ctx = ExecutionContext(request=request, source=source, user_id=user_id)

        # in-flight workflow state (if resuming/inspecting an existing workflow)
        if workflow_id:
            try:
                tasks = self.store.list_workflow_tasks(workflow_id)
                ctx.workflow_state = {
                    "workflow_id": workflow_id,
                    "tasks": {t.task_id: t.status.value for t in tasks},
                }
            except KeyError:
                pass

        # prior evidence — results of recently completed tasks (any workflow)
        evidence = []
        for row in reversed(self.store.list_tasks(limit=recent_limit * 3)):
            if row["status"] != "completed":
                continue
            results = self.store.task_results(row["task_id"])
            if results:
                evidence.append({
                    "task_id": row["task_id"], "task_type": row["task_type"],
                    "result_uri": results[0]["result_uri"],
                    "result_hash": results[0]["result_hash"],
                })
            if len(evidence) >= recent_limit:
                break
        ctx.prior_evidence = evidence

        # failure patterns mined by the Learning/Audit loop (loop 8 feedback)
        if self.audit is not None:
            try:
                ctx.failure_patterns = self.audit.failure_patterns()
                ctx.policy = self.audit.load_policy()
            except Exception:
                pass
        return ctx

    @staticmethod
    def attach(graph: list[dict[str, Any]], ctx: ExecutionContext) -> list[dict[str, Any]]:
        """Attach the context to every node payload (workers read it)."""
        for node in graph:
            payload = node.setdefault("payload", {})
            payload["context"] = ctx.to_dict()
        return graph