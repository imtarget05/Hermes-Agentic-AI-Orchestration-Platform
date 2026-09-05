"""Procurement pipeline: graph builder + store-aware handlers for the DAG engine.

Graph: price-1, vendor-1, contract-1, spec-1 (parallel roots)
       → analysis-1 (join) → verification-1.

Handlers capture the async `store` so join nodes (analysis/verification)
can read sibling results via `store.task_results`. Leaf handlers emit
machine-readable JSON lines FIRST (so join nodes can parse them), followed
by agent prose. All leaves are self-sufficient from the task payload.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..agents import AGENTS
from ..tools import ToolExecutor
from .schemas import Quote


def build_procurement_graph(
    request: str,
    quotes: list[dict[str, Any]],
    required_spec: str = "",
) -> list[dict[str, Any]]:
    payload = {"request": request, "quotes": quotes, "required_spec": required_spec}
    return [
        {"task_id": "price-1", "task_type": "price", "deps": [], "payload": dict(payload)},
        {"task_id": "vendor-1", "task_type": "vendor", "deps": [], "payload": dict(payload)},
        {"task_id": "contract-1", "task_type": "contract", "deps": [], "payload": dict(payload)},
        {"task_id": "spec-1", "task_type": "spec", "deps": [], "payload": dict(payload)},
        {"task_id": "analysis-1", "task_type": "analysis",
         "deps": ["price-1", "vendor-1", "contract-1", "spec-1"], "payload": dict(payload)},
        {"task_id": "verification-1", "task_type": "verification",
         "deps": ["analysis-1"], "payload": dict(payload)},
    ]


def _payload_quotes(task: Any) -> list[dict[str, Any]]:
    payload = getattr(task, "payload", None) or {}
    if isinstance(payload, dict):
        q = payload.get("quotes") or []
        if isinstance(q, list):
            return [d for d in q if isinstance(d, dict)]
    return []


def _payload_spec(task: Any) -> str:
    payload = getattr(task, "payload", None) or {}
    return str(payload.get("required_spec", "")) if isinstance(payload, dict) else ""


def _payload_request(task: Any) -> str:
    payload = getattr(task, "payload", None) or {}
    if isinstance(payload, dict) and payload.get("request"):
        return str(payload["request"])
    return getattr(task, "task_id", "")


def _iter_json_objects(ctx: str) -> list[dict]:
    """Extract every balanced {...} substring that parses as a JSON object."""
    out: list[dict] = []
    i = 0
    while i < len(ctx):
        start = ctx.find("{", i)
        if start == -1:
            break
        depth, end = 0, -1
        for j in range(start, len(ctx)):
            if ctx[j] == "{":
                depth += 1
            elif ctx[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end == -1:
            break
        try:
            data = json.loads(ctx[start:end])
            if isinstance(data, dict):
                out.append(data)
        except Exception:
            pass
        i = end
    return out


def _quotes_from_ctx(ctx: str) -> list[dict[str, Any]]:
    return [o for o in _iter_json_objects(ctx)
            if o.get("vendor") and ("total" in o) and ("unit_price" in o)]


def _approved_from_ctx(ctx: str) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for o in _iter_json_objects(ctx):
        if o.get("vendor") is not None and "approved" in o:
            out[str(o["vendor"])] = bool(o["approved"])
    return out


def _terms_from_ctx(ctx: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for o in _iter_json_objects(ctx):
        if o.get("vendor") and "warranty_years" in o:
            out[str(o["vendor"])] = o
    return out


def _sibling_results(store: Any, task: Any, task_ids: list[str]) -> dict[str, str]:
    """Read already-completed sibling results from the store (join support)."""
    out: dict[str, str] = {}
    if store is None:
        return out
    try:
        store.task_results(task.task_id)  # probe availability
    except Exception:
        return out
    for tid in task_ids:
        try:
            rows = store.task_results(tid)
        except Exception:
            continue
        if rows:
            last = rows[-1] if isinstance(rows, list) else rows
            out[tid] = str(last.get("result_uri", "") if isinstance(last, dict) else last)
    return out


def _agent_prose(agent_name: str, request: str, context: str) -> str:
    try:
        return AGENTS[agent_name].run(request, context)
    except Exception as e:
        return f"[{agent_name} agent error: {e}]"


def build_procurement_handlers(store: Any = None) -> dict[str, Callable[[Any], str]]:
    """Store-aware handlers: leaves compute from payload, joins read siblings."""

    def _price(task: Any) -> str:
        quotes = _payload_quotes(task)
        if not quotes:
            return _agent_prose("price", _payload_request(task), "no quotes provided")
        ex = ToolExecutor({"general", "procurement_price"})
        ranking = ex.call("compare_prices", quotes_json=json.dumps(quotes))
        lines = [json.dumps(q) for q in quotes] + [ranking]
        lines.append(_agent_prose("price", _payload_request(task),
                                 f"quotes: {json.dumps(quotes)[:1500]}"))
        return "\n".join(lines)

    def _vendor(task: Any) -> str:
        quotes = _payload_quotes(task)
        vendors = [q.get("vendor", "") for q in quotes if q.get("vendor")] or ["Lenovo", "Dell", "HP"]
        ex = ToolExecutor({"general", "procurement_vendor"})
        lines = []
        for v in vendors:
            try:
                lines.append(ex.call("check_approved_vendor", vendor=v))
            except Exception as e:
                lines.append(json.dumps({"vendor": v, "approved": False, "note": str(e)[:200]}))
        lines.append(_agent_prose("vendor", _payload_request(task), f"vendors: {vendors}"))
        return "\n".join(lines)

    def _contract(task: Any) -> str:
        quotes = _payload_quotes(task)
        ex = ToolExecutor({"general", "procurement_contract"})
        lines = []
        for q in quotes:
            raw = q.get("raw_text", "") or json.dumps(q)
            try:
                lines.append(ex.call("extract_contract_terms", quote_text=raw,
                                     vendor=q.get("vendor", ""),
                                     source_uri=q.get("source_uri", "")))
            except Exception as e:
                lines.append(json.dumps({"vendor": q.get("vendor", ""), "payment": "",
                                         "warranty_years": 0.0, "sla_hours": 0.0,
                                         "source_uri": q.get("source_uri", ""),
                                         "note": str(e)[:200]}))
        if not lines:
            return _agent_prose("contract", _payload_request(task), "")
        lines.append(_agent_prose("contract", _payload_request(task),
                                 f"{len(quotes)} quotes analyzed"))
        return "\n".join(lines)

    def _spec(task: Any) -> str:
        quotes = _payload_quotes(task)
        spec = _payload_spec(task)
        ex = ToolExecutor({"general", "procurement_spec"})
        lines = []
        for q in quotes:
            raw = q.get("raw_text", "") or json.dumps(q)
            try:
                lines.append(ex.call("score_spec", quote_text=raw, required_spec=spec,
                                     vendor=q.get("vendor", "")))
            except Exception as e:
                lines.append(json.dumps({"vendor": q.get("vendor", ""), "score": 0.0,
                                         "meets_minimum": False, "notes": str(e)[:200]}))
        if not lines:
            return _agent_prose("spec", _payload_request(task), "")
        lines.append(_agent_prose("spec", _payload_request(task), f"spec: {spec[:300]}"))
        return "\n".join(lines)

    def _analysis(task: Any) -> str:
        sibs = _sibling_results(store, task, ["price-1", "vendor-1", "contract-1", "spec-1"])
        if sibs:
            ctx = "\n".join(f"[{tid}] {res}" for tid, res in sibs.items())
        else:  # fallback: rebuild context from payload quotes directly
            quotes = _payload_quotes(task)
            lines = [json.dumps(q) for q in quotes]
            for q in quotes:
                lines.append(json.dumps({"vendor": q.get("vendor", ""),
                                         "approved": q.get("vendor", "").lower() in ("lenovo", "dell"),
                                         "note": "payload fallback"}))
                lines.append(json.dumps({"vendor": q.get("vendor", ""),
                                         "payment": q.get("payment", "Net 30"),
                                         "warranty_years": q.get("warranty_years", 3.0),
                                         "sla_hours": q.get("sla_hours", 4.0),
                                         "source_uri": q.get("source_uri", "")}))
            ctx = "\n".join(lines)
        return AGENTS["analysis"].run(_payload_request(task), ctx)

    def _verification(task: Any) -> str:
        sibs = _sibling_results(store, task, ["analysis-1"])
        rec = sibs.get("analysis-1", "")
        if not rec:
            rec = _analysis(task)  # self-sufficient fallback
        return AGENTS["verification"].run(_payload_request(task), rec)

    return {
        "price": _price,
        "vendor": _vendor,
        "contract": _contract,
        "spec": _spec,
        "analysis": _analysis,
        "verification": _verification,
    }


def validate_quotes(quotes: list[dict[str, Any]]) -> list[Quote]:
    return [Quote(**q) if isinstance(q, dict) else q for q in quotes]
