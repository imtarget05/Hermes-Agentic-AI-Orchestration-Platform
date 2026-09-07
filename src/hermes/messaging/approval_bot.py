"""Telegram approval poller: resolves Approve/Reject button taps with RBAC enforcement.

Run alongside the API/gateway when TELEGRAM_BOT_TOKEN is set:

    PYTHONPATH=src python -m hermes.messaging.approval_bot

Callback data `approve:<request_id>` / `reject:<request_id>` resolves the
matching row in ApprovalStore (procurement DB) and stamps the sync task
result (sync DB) to APPROVED / REJECTED so the inbox reflects the decision.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from ..async_engine.loops.hitl import ApprovalStore
from ..auth import ApprovalPolicy, RBACEngine, User
from ..telegram_chat.auth import _get_rbac_engine as get_shared_rbac_engine


def _get_rbac_engine() -> RBACEngine:
    """Get the shared RBAC engine instance."""
    return get_shared_rbac_engine()


def _load_tenant_policy(tenant_id: str) -> ApprovalPolicy:
    engine = _get_rbac_engine()
    return engine.get_or_create_default_policy(tenant_id)


def _extract_tenant_from_request(rec: dict) -> str:
    """Extract tenant_id from approval record args or metadata."""
    try:
        args = json.loads(rec.get("args") or "{}")
        return args.get("tenant_id", "default")
    except Exception:
        return "default"


def _get_approver_user(resolver: str) -> User | None:
    """Resolve a resolver string to an RBAC user.

    Supported formats:
      "telegram:<username_or_id>" | "user:<user_id>" | "api:<user_id>"

    Plus legacy bare labels for backward compatibility: an unprefixed value is
    resolved as username → user_id → role name. Role resolution keeps RBAC
    enforced (a real user with real roles is returned, so the approval policy
    and separation-of-duties checks still apply) while legacy callers that pass
    e.g. "manager" keep working instead of being silently rejected.
    """
    engine = _get_rbac_engine()
    label = (resolver or "").strip()
    if label.startswith("telegram:"):
        return engine.get_user_by_username("default", label[len("telegram:"):])
    if label.startswith("user:") or label.startswith("api:"):
        return engine.get_user(label.split(":", 1)[1])
    if not label:
        return None
    # Legacy bare resolver: username → user_id → role name.
    user = engine.get_user_by_username("default", label) or engine.get_user(label)
    if user is not None:
        return user
    low = label.lower()
    roles = [r.name for r in engine.list_roles()
             if low == r.name.lower() or low in r.name.lower()]
    # Prefer an exact role-name match, then the most specific (shortest) one.
    roles.sort(key=lambda name: (name.lower() != low, len(name)))
    for role_name in roles:
        users = engine.find_users_by_role(role_name)
        if users:
            return users[0]
    return None


def _get_requester_user(rec: dict) -> User | None:
    """Extract requester from approval record."""
    engine = _get_rbac_engine()
    try:
        args = json.loads(rec.get("args") or "{}")
        requester_id = args.get("requester_id") or args.get("user_id")
        if requester_id:
            return engine.get_user(requester_id)
    except Exception:
        pass
    return None


def _extract_amount_from_request(rec: dict) -> float:
    """Extract request amount from approval record args."""
    try:
        args = json.loads(rec.get("args") or "{}")
        return float(args.get("amount", 0) or args.get("total", 0) or 0)
    except Exception:
        return 0.0


def _generate_idempotency_key(request_id: str, decision: str, approver_id: str) -> str:
    """Generate idempotency key for approval resolution."""
    data = f"approval:{request_id}:{decision}:{approver_id}"
    return hashlib.sha256(data.encode()).hexdigest()[:32]


def resolve_approval(
    request_id: str,
    approved: bool,
    resolver: str = "telegram",
    proc_db: str = "",
    sync_db: str = "",
) -> dict | None:
    """Resolve an approval with full RBAC + approval policy enforcement.
    
    Returns the approval record with audit fields, or None if not found.
    Includes idempotency key to prevent duplicate processing.
    """
    proc_db = proc_db or os.environ.get("HERMES_PROCUREMENT_DB", "./hermes_procurement.db")
    store = ApprovalStore(proc_db)
    
    rec = store.get(request_id)
    if rec is None:
        return None
    
    if rec.get("status") != "PENDING":
        return rec
    
    # Generate idempotency key for this resolution
    decision = "approve" if approved else "reject"
    idempotency_key = _generate_idempotency_key(request_id, decision, resolver)
    
    # Check if this approval was already processed (idempotency)
    existing = store.check_idempotency_key(idempotency_key)
    if existing:
        # Return existing result without re-processing
        return store.get(request_id)
    
    tenant_id = _extract_tenant_from_request(rec)
    policy = _load_tenant_policy(tenant_id)
    amount = _extract_amount_from_request(rec)
    requester = _get_requester_user(rec)
    approver = _get_approver_user(resolver)
    
    allowed = True
    reason = ""
    
    if approved:
        if requester and approver:
            allowed, reason = policy.evaluate_approval(
                request_amount=amount,
                requester_id=requester.id,
                approver_id=approver.id,
                approver_roles=approver.roles,
            )
        elif approver:
            can_approve, reason = _get_rbac_engine().user_can_approve(approver, amount, policy)
            allowed = can_approve
        else:
            allowed = False
            reason = "Approver not found in RBAC system"
    
    if not allowed and approved:
        rec["status"] = "REJECTED"
        rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
        rec["resolver"] = resolver
        rec["rejection_reason"] = f"RBAC policy violation: {reason}"
        
        store._exec(
            "UPDATE approvals SET status=?, resolved_at=?, resolver=?, rejection_reason=? WHERE request_id=?",
            (rec["status"], rec["resolved_at"], rec["resolver"], rec["rejection_reason"], request_id)
        )
        # Store idempotency key for the rejection
        store.store_idempotency_key(idempotency_key, request_id)
        return store.get(request_id)
    
    rec = store.resolve(request_id, approved, resolver=resolver)
    if rec is None:
        return None
    
    # Store idempotency key after successful resolution
    store.store_idempotency_key(idempotency_key, request_id)
    
    rec["audit"] = {
        "request_id": request_id,
        "approver_id": approver.id if approver else resolver,
        "approver_roles": approver.roles if approver else [],
        "requester_id": requester.id if requester else None,
        "amount": amount,
        "decision": "APPROVED" if approved else "REJECTED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "policy_thresholds": [t.to_dict() for t in policy.thresholds],
        "separation_of_duties": policy.separation_of_duties,
        "rbac_reason": reason if not approved and reason else "Approval allowed" if approved else "Rejected by user",
        "idempotency_key": idempotency_key,
    }
    
    try:
        args = json.loads(rec.get("args") or "{}")
        task_id = args.get("sync_task_id", "")
        sync_db = sync_db or args.get("sync_db_path", "") or os.environ.get("HERMES_DB_PATH", "./hermes_tasks.db")
        if task_id and sync_db:
            from ..tasks import TaskStore
            ts = TaskStore(sync_db)
            task = ts.get(task_id)
            try:
                body = json.loads(task.result.split("\n", 1)[-1])
            except Exception:
                body = {"raw": task.result[:1000]}
            body["status"] = "APPROVED" if approved else "REJECTED"
            body["approved_by"] = resolver
            body["audit"] = rec["audit"]
            prefix = "VERIFICATION PASSED" if "VERIFICATION" in task.result else ""
            ts.set_result(task_id, f"{prefix}\n{json.dumps(body)}" if prefix else json.dumps(body),
                          owner="human")
    except Exception as e:
        print(f"[approval] task stamp failed: {e}")
    
    return rec


async def _on_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    data = (query.data or "")
    action, _, request_id = data.partition(":")
    approved = action == "approve"
    rec = resolve_approval(request_id, approved)
    if rec is None:
        await query.edit_message_text(f"⚠️ Approval {request_id} not found / already resolved.")
        return
    verdict = "✅ APPROVED — purchase request may proceed." if approved else "❌ REJECTED."
    await query.edit_message_text(f"🛒 Purchase approval [{request_id}]\n{verdict}")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")
    from telegram.ext import Application, CallbackQueryHandler
    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(_on_callback, pattern=r"^(approve|reject):"))
    print("[approval-bot] polling for Approve/Reject taps…")
    app.run_polling()


if __name__ == "__main__":
    main()
