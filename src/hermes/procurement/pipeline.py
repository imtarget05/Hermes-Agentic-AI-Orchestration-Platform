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
    # Multi-Agent RAG: index the case corpus (quotes + vendor registry + spec)
    # so every specialist retrieves cited evidence instead of reasoning blind.
    from ..rag import build_case_index
    rag_path = (db_path or default_procurement_db()) + ".rag.json"
    try:
        build_case_index(quotes, required_spec).save(rag_path)
    except Exception:
        rag_path = ""
    graph = build_procurement_graph(request, quotes, required_spec, rag_index=rag_path)
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
    agg["rag_index"] = rag_path
    return agg


def run_procurement_benchmark(
    request: str,
    quotes: list[dict[str, Any]],
    required_spec: str = "",
    workers_list: tuple[int, ...] = (1, 2, 4),
    timeout: float = 120.0,
    db_dir: str = "",
    handler_delay_ms: float = 0.0,
) -> dict[str, Any]:
    """Parallel speedup benchmark: same case × {1,2,4} workers → timings.

    `handler_delay_ms` simulates per-agent LLM latency so the DAG's parallel
    fan-out shows measurable speedup (stub handlers are otherwise instant).
    Reports wall seconds per worker count + speedup vs the 1-worker baseline
    (mirrors `loadtest` evidence: parallelism is engineered, not claimed).
    """
    import tempfile
    import time

    from .handlers import build_procurement_handlers

    db_dir = db_dir or tempfile.mkdtemp(prefix="hermes_proc_bench_")
    rows: dict[int, float] = {}
    for w in workers_list:
        db = f"{db_dir}/bench_{w}.db"
        store = AsyncTaskStore(db)
        bus = InMemoryBus()
        from ..rag import build_case_index
        rag_path = db + ".rag.json"
        try:
            build_case_index(quotes, required_spec).save(rag_path)
        except Exception:
            rag_path = ""
        graph = build_procurement_graph(request, quotes, required_spec, rag_index=rag_path)
        handlers = build_procurement_handlers(store)
        if handler_delay_ms > 0:
            delay_s = handler_delay_ms / 1000.0

            def _wrap(fn):
                def _inner(task):
                    time.sleep(delay_s)
                    return fn(task)
                return _inner

            handlers = {k: _wrap(fn) for k, fn in handlers.items()}
        verifier = Verifier(by_task_type=dict(PROCUREMENT_VALIDATORS))
        orch = AsyncOrchestrator(store, bus)
        start = time.time()
        agg = orch.run_workflow(graph, handlers, workers=w, timeout=timeout,
                                verifier=verifier)
        assert agg["status"] == "completed", f"workers={w} failed: {agg.get('counts')}"
        rows[w] = round(time.time() - start, 2)
    base = rows.get(1, 0.0) or 0.01
    return {
        "seconds": rows,
        "speedup": {w: round(base / max(0.01, s), 2) for w, s in rows.items()},
        "baseline_workers": 1,
        "handler_delay_ms": handler_delay_ms,
    }
