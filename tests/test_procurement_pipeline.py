"""Procurement pipeline tests: PDF parsing + full DAG + approval loop."""
from __future__ import annotations

import json

from hermes.async_engine.loops.hitl import ApprovalStore
from hermes.messaging.approval_bot import resolve_approval
from hermes.procurement import run_procurement_case
from hermes.runtime import (
    default_demo_quotes,
    ensure_demo_quote_pdfs,
    parse_quote_files,
)


def _quotes():
    return default_demo_quotes()


def test_pdf_parse_extracts_vendors_and_totals(tmp_path):
    sandbox = str(tmp_path / "sandbox")
    paths = ensure_demo_quote_pdfs(sandbox)
    assert len(paths) == 3
    quotes = parse_quote_files(paths, sandbox)
    by_vendor = {q["vendor"]: q for q in quotes}
    assert by_vendor["Lenovo"]["total"] == 54000.0
    assert by_vendor["Dell"]["quantity"] == 50
    assert by_vendor["HP"]["unit_price"] == 1150.0


def test_full_dag_recommends_lenovo(tmp_path):
    agg = run_procurement_case(
        "Công ty cần mua 50 laptop cho team Engineering",
        _quotes(), workers=4, timeout=60,
        db_path=str(tmp_path / "p.db"),
    )
    assert agg["status"] == "completed"
    assert agg["counts"].get("failed", 0) == 0
    rec = agg["recommendation"]
    assert rec["vendor"] == "Lenovo"
    assert rec["total_cost"] == 54000.0
    assert rec["status"] == "PENDING_APPROVAL"
    # Multi-Agent RAG: case index built + specialists cite retrieved evidence
    import os as _os
    assert agg.get("rag_index") and _os.path.exists(agg["rag_index"])
    from hermes.async_engine.store import AsyncTaskStore
    store = AsyncTaskStore(str(tmp_path / "p.db"))
    price_out = store.task_results("price-1")[-1]["result_uri"]
    assert "RAG-EVIDENCE" in price_out
    verify_out = store.task_results("verification-1")[-1]["result_uri"]
    assert "VERIFICATION PASSED" in verify_out and "RAG-EVIDENCE" in verify_out


def test_parallel_speedup_benchmark(tmp_path):
    from hermes.procurement import run_procurement_benchmark
    report = run_procurement_benchmark(
        "mua 50 laptop", _quotes(), workers_list=(1, 2),
        timeout=60, db_dir=str(tmp_path), handler_delay_ms=200,
    )
    assert report["seconds"][1] >= report["seconds"][2]
    assert report["speedup"][1] == 1.0
    assert report["speedup"][2] >= 1.0


def test_approval_pending_then_manager_approves(tmp_path, monkeypatch):
    from hermes.messaging import MockNotifier
    from hermes.orchestrator import orchestrate
    from hermes.tasks import Task, TaskStore

    monkeypatch.setenv("HERMES_PROCUREMENT_DB", str(tmp_path / "p.db"))
    monkeypatch.delenv("HERMES_HITL_AUTO_APPROVE", raising=False)
    sync_db = str(tmp_path / "t.db")
    proc_db = str(tmp_path / "p.db")
    store = TaskStore(sync_db)
    task = store.create(Task(text="mua 50 laptop", project="demo", strategy="procurement"))
    note = MockNotifier(log_path=str(tmp_path / "m.log"))
    final = orchestrate(task.id, store, note, quotes=_quotes())
    assert "Lenovo" in final
    approvals = ApprovalStore(proc_db)
    pend = approvals.pending()
    assert len(pend) == 1
    rid = pend[0]["request_id"]
    assert pend[0]["tool_name"] == "approve_purchase"
    rec = resolve_approval(rid, True, resolver="manager", proc_db=proc_db, sync_db=sync_db)
    assert rec["status"] == "APPROVED"
    body = json.loads(store.get(task.id).result.split("\n", 1)[-1])
    assert body["status"] == "APPROVED"
    assert approvals.pending() == []
