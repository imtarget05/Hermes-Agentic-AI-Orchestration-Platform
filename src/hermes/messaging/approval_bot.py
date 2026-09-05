"""Telegram approval poller: resolves Approve/Reject button taps.

Run alongside the API/gateway when TELEGRAM_BOT_TOKEN is set:

    PYTHONPATH=src python -m hermes.messaging.approval_bot

Callback data `approve:<request_id>` / `reject:<request_id>` resolves the
matching row in ApprovalStore (procurement DB) and stamps the sync task
result (sync DB) to APPROVED / REJECTED so the inbox reflects the decision.
"""
from __future__ import annotations

import json
import os


def resolve_approval(request_id: str, approved: bool, resolver: str = "telegram",
                     proc_db: str = "", sync_db: str = "") -> dict | None:
    from ..async_engine.loops.hitl import ApprovalStore

    proc_db = proc_db or os.environ.get("HERMES_PROCUREMENT_DB", "./hermes_procurement.db")
    store = ApprovalStore(proc_db)
    rec = store.resolve(request_id, approved, resolver=resolver)
    if rec is None:
        return None
    # stamp the originating sync task so GET /tasks/{id} shows the decision
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
