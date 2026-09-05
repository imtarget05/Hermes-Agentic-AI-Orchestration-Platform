"""Loop 2 — PLANNING / REASONING LOOP.

Task decomposition + DAG generation + dependency resolution.

The planner is LLM-hookable but degrades to deterministic templates, so the
platform plans reliably without an API key (same convention as Project 2
agents). Output is always the canonical graph spec the dispatch loop consumes.
"""
from __future__ import annotations

import json
import re
from typing import Any

VALID_TASK_TYPES = ("research", "analyze", "report", "notify",
                          "price", "vendor", "contract", "spec", "analysis", "verification")

PROCUREMENT_KEYWORDS = ("procurement", "laptop", "quote", "báo giá", "vendor",
                        "purchase", "mua sắm", "supplier", "warranty", "slas", "sla")

# Learning-loop (loop 8) feedback: per-task-type retry budget adjustments.
DEFAULT_MAX_ATTEMPTS: dict[str, int] = {
    "research": 3, "analyze": 3, "report": 3, "notify": 2,
    "price": 3, "vendor": 2, "contract": 3, "spec": 3,
    "analysis": 2, "verification": 2,
}


def procurement_dag() -> list[dict[str, Any]]:
    """Enterprise Procurement Case Agent DAG: 4 parallel → analysis → verification."""
    return [
        {"task_id": "price-1", "task_type": "price", "deps": []},
        {"task_id": "vendor-1", "task_type": "vendor", "deps": []},
        {"task_id": "contract-1", "task_type": "contract", "deps": []},
        {"task_id": "spec-1", "task_type": "spec", "deps": []},
        {"task_id": "analysis-1", "task_type": "analysis",
         "deps": ["price-1", "vendor-1", "contract-1", "spec-1"]},
        {"task_id": "verification-1", "task_type": "verification",
         "deps": ["analysis-1"]},
    ]


class Planner:
    """Loop 2: request -> DAG spec (nodes with task_id/task_type/deps)."""

    def __init__(self, llm=None, policy: dict[str, Any] | None = None):
        self.llm = llm  # optional callable(str) -> str (LLM JSON plan)
        self.policy = policy or {}

    # -- deterministic templates (fallback + tests) ---------------------- #
    def _template(self, request: str) -> list[dict[str, Any]]:
        low = request.lower()
        if any(k in low for k in PROCUREMENT_KEYWORDS):
            return procurement_dag()
        if any(k in low for k in ("compare", "versus", " vs ", "multiple", "diverse")):
            # fan-out: two parallel analyze branches then join (spec §9 shape)
            return [
                {"task_id": "research-1", "task_type": "research", "deps": []},
                {"task_id": "analyze-1", "task_type": "analyze", "deps": ["research-1"]},
                {"task_id": "analyze-2", "task_type": "analyze", "deps": ["research-1"]},
                {"task_id": "report-1", "task_type": "report",
                 "deps": ["analyze-1", "analyze-2"]},
            ]
        if any(k in low for k in ("notify", "alert", "telegram", "send")):
            return [
                {"task_id": "research-1", "task_type": "research", "deps": []},
                {"task_id": "report-1", "task_type": "report", "deps": ["research-1"]},
                {"task_id": "notify-1", "task_type": "notify", "deps": ["report-1"]},
            ]
        # default pipeline: research -> analyze -> report
        return [
            {"task_id": "research-1", "task_type": "research", "deps": []},
            {"task_id": "analyze-1", "task_type": "analyze", "deps": ["research-1"]},
            {"task_id": "report-1", "task_type": "report", "deps": ["analyze-1"]},
        ]

    # -- LLM path (optional) --------------------------------------------- #
    def _llm_plan(self, request: str) -> list[dict[str, Any]] | None:
        if self.llm is None:
            return None
        prompt = (
            "Decompose this request into a task DAG. Reply ONLY with JSON: "
            '[{"task_id": str, "task_type": research|analyze|report|notify|price|vendor|contract|spec|analysis|verification, '
            '"deps": [task_id]}]. For procurement/purchase/quote requests use: '
            "price-1, vendor-1, contract-1, spec-1 (no deps) → analysis-1 "
            "(deps: all four) → verification-1 (dep: analysis-1). Request: " + request
        )
        try:
            raw = str(self.llm(prompt))
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not match:
                return None
            nodes = json.loads(match.group(0))
            if not self._validate_nodes(nodes):
                return None
            return nodes
        except Exception:
            return None  # planner never fails the request — degrade to template

    @staticmethod
    def _validate_nodes(nodes: Any) -> bool:
        if not isinstance(nodes, list) or not nodes:
            return False
        ids = set()
        for n in nodes:
            if not isinstance(n, dict) or not n.get("task_id"):
                return False
            if n.get("task_type") not in VALID_TASK_TYPES:
                return False
            if n["task_id"] in ids:
                return False
            ids.add(n["task_id"])
        # every dep must reference an existing task and be acyclic-enough
        return all(d in ids for n in nodes for d in (n.get("deps") or []))

    # -- public API -------------------------------------------------------- #
    def plan(self, request: str) -> list[dict[str, Any]]:
        """Return a validated DAG spec. LLM plan preferred, template fallback."""
        nodes = self._llm_plan(request) or self._template(request)
        # apply learned retry budgets (loop 8 -> loop 2 feedback)
        attempts = dict(DEFAULT_MAX_ATTEMPTS)
        attempts.update(self.policy.get("max_attempts", {}))
        for n in nodes:
            n["max_attempts"] = attempts.get(n["task_type"], 3)
        return nodes

    def plan_with_context(self, request: str, context) -> tuple[list[dict[str, Any]], Any]:
        """Convenience: plan and attach the execution context to each node."""
        from .context import (  # local import avoids cycle
            ContextBuilder,
            ExecutionContext,
        )

        nodes = self.plan(request)
        if isinstance(context, ContextBuilder):
            ctx = context.build(request)
        elif isinstance(context, ExecutionContext):
            ctx = context
        else:
            raise TypeError("context must be ContextBuilder or ExecutionContext")
        ContextBuilder.attach(nodes, ctx)
        return nodes, ctx