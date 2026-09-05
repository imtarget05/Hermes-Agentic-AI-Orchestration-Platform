"""Procurement case runner on the async DAG engine (true parallel fan-out).

Request → procurement DAG (4 parallel roots → analysis join → verification)
executed by AsyncOrchestrator + WorkerPool over InMemoryBus (or any bus),
with evidence-grounded verification wired into the workers.
"""
from __future__ import annotations

import os
from typing import Any

from ..async_engine.backends import InMemoryBus
from ..async_engine.loops.verify import PROCUREMENT_VALIDATORS, Verifier
from ..async_engine.orchestrator import AsyncOrchestrator
from ..async_engine.store import AsyncTaskStore
from .handlers import build_procurement_graph, build_procurement_handlers


def default_procurement_db(sync_db_path: str = "./hermes_tasks.db") -> str:
    if os.environ.get("HERMES_PROCUREMENT_DB"):
        return str(os.environ["HERMES_PROCUREMENT_DB"])
    import tempfile
    base = sync_db_path or "./hermes_tasks.db"
    if base == ":memory:":
        return os.path.join(tempfile.gettempdir(), "hermes_procurement.db")
    if base.endswith(".db"):
        return base[: -len(".db")] + "_procurement.db"
    return base + "_procurement.db"


def run_procurement_case(
    request: str,
    quotes: list[dict[str, Any]],
    required_spec: str = "",
    workers: int = 4,
    timeout: float = 120.0,
    db_path: str = "",
    store: AsyncTaskStore | None = None,
    bus: Any | None = None,
) -> dict[str, Any]:
    """Execute the full procurement DAG. Returns the aggregate report.

    The verification node output (agent text) is returned under
    `aggregate["results"]["verification-1"]`, and the parsed Recommendation
    JSON under `aggregate["recommendation"]`.
    """
    own_store = store or AsyncTaskStore(db_path or default_procurement_db())
    own_bus = bus if bus is not None else InMemoryBus()
    graph = build_procurement_graph(request, quotes, required_spec)
    handlers = build_procurement_handlers(own_store)
    verifier = Verifier(by_task_type=dict(PROCUREMENT_VALIDATORS))
    orch = AsyncOrchestrator(own_store, own_bus)
    agg = orch.run_workflow(graph, handlers, workers=workers, timeout=timeout,
                            verifier=verifier)
    # surface the parsed recommendation for API/Telegram layers
    rec: dict[str, Any] = {}
    try:
        results = agg.get("results", {})
        vrows = results.get("verification-1", [])
        arows = results.get("analysis-1", [])
        raw = ""
        if vrows:
            raw = str(vrows[-1].get("result_uri", ""))
        if "VERIFICATION PASSED" in raw and arows:
            raw = str(arows[-1].get("result_uri", ""))
        import json as _json
        start = raw.find("{")
        if start != -1:
            depth, end = 0, -1
            for i in range(start, len(raw)):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                rec = _json.loads(raw[start:end])
    except Exception:
        rec = {}
    agg["recommendation"] = rec
    return agg
