"""Human-in-the-Loop (HITL) — approval workflow for HIGH-risk tool calls.

When the PolicyEngine returns REQUIRE_APPROVAL, the platform pauses and asks
a human to approve/reject. The approval store is SQLite-backed so it survives
worker restarts and is shared across worker replicas (Railway layout).

    PolicyEngine → REQUIRE_APPROVAL
         ↓
    ApprovalStore.create(task_id, tool, args, risk) → request_id
         ↓
    Human approves/rejects (API / Telegram / auto-approve flag)
         ↓
    Task resumes or deadletters

Demo mode: HERMES_HITL_AUTO_APPROVE=true makes every request instant-approve
so the suite runs without a human in the loop.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS approvals (
    request_id TEXT PRIMARY KEY,
    task_id TEXT, workflow_id TEXT, tool_name TEXT, agent_role TEXT,
    args TEXT, risk TEXT, status TEXT, created_at TEXT, resolved_at TEXT, resolver TEXT
)
"""


class ApprovalStore:
    """SQLite-backed approval store (shared across workers)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    def _init(self) -> None:
        con = sqlite3.connect(self.db_path)
        con.executescript(_DDL)
        con.commit()
        con.close()

    def _exec(self, sql: str, params: tuple = (), fetch: str = ""):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(sql, params)
        out = None
        if fetch == "one":
            out = cur.fetchone()
        elif fetch == "all":
            out = cur.fetchall()
        con.commit()
        con.close()
        return out

    def create(self, task_id: str, workflow_id: str, tool_name: str,
               agent_role: str, args: dict, risk: str, timeout_s: float = 300.0) -> str:
        rid = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO approvals (request_id, task_id, workflow_id, tool_name, "
            "agent_role, args, risk, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, task_id, workflow_id, tool_name, agent_role,
             json.dumps(args or {}), risk, "PENDING",
             datetime.now(timezone.utc).isoformat()))
        return rid

    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM approvals WHERE request_id=?", (request_id,), fetch="one")
        return dict(row) if row else None

    def resolve(self, request_id: str, approved: bool, resolver: str = "human") -> dict[str, Any] | None:
        status = "APPROVED" if approved else "REJECTED"
        self._exec(
            "UPDATE approvals SET status=?, resolved_at=?, resolver=? WHERE request_id=? AND status='PENDING'",
            (status, datetime.now(timezone.utc).isoformat(), resolver, request_id))
        return self.get(request_id)

    def pending(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM approvals WHERE status='PENDING' ORDER BY created_at LIMIT ?",
                          (limit,), fetch="all") or []
        return [dict(r) for r in rows]


class HumanInTheLoop:
    """Bridges PolicyEngine.REQUIRE_APPROVAL → approval decision."""

    def __init__(self, store: ApprovalStore, auto_approve: bool = False,
                 approval_timeout_s: float = 300.0):
        self.store = store
        self.auto_approve = auto_approve
        self.approval_timeout_s = approval_timeout_s

    @property
    def requires_human(self) -> bool:
        return not self.auto_approve

    def request(self, task_id: str, workflow_id: str, tool_name: str,
                agent_role: str, args: dict, risk: str) -> str:
        return self.store.create(task_id, workflow_id, tool_name, agent_role, args, risk,
                                 self.approval_timeout_s)

    def await_decision(self, request_id: str, poll_interval: float = 0.5) -> bool:
        """Block until approved/rejected/expired. Returns True if approved.
        In auto-approve mode, resolves immediately."""
        if self.auto_approve:
            self.store.resolve(request_id, True, resolver="auto")
            return True
        deadline = time.time() + self.approval_timeout_s
        while time.time() < deadline:
            rec = self.store.get(request_id)
            if rec is None:
                return False
            if rec["status"] == "APPROVED":
                return True
            if rec["status"] == "REJECTED":
                return False
            time.sleep(poll_interval)
        return False  # expired

    @classmethod
    def from_env(cls, db_path: str) -> "HumanInTheLoop":
        auto = os.environ.get("HERMES_HITL_AUTO_APPROVE", "").lower() in ("1", "true", "yes")
        return cls(ApprovalStore(db_path), auto_approve=auto)