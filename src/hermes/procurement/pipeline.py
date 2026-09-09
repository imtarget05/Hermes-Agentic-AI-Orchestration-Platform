"""Procurement case runner on the async DAG engine (true parallel fan-out).

Request → procurement DAG (4 parallel roots → analysis join → verification)
executed by AsyncOrchestrator + WorkerPool over InMemoryBus (or any bus),
with evidence-grounded verification wired into the workers.

Now accepts RoutingPlan for router-first architecture (P0-1).
"""
from __future__ import annotations

import os
from typing import Any

from ..async_engine.backends import InMemoryBus
from ..async_engine.contract import new_id, routing_for
from ..async_engine.loops.verify import PROCUREMENT_VALIDATORS, Verifier
from ..async_engine.orchestrator import AsyncOrchestrator
from ..async_engine.store import AsyncTaskStore
from ..router import FULL_PROCUREMENT_AGENTS, RoutingPlan
from .handlers import build_procurement_handlers


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


def _build_graph_for_plan(
    request: str,
    quotes: list[dict[str, Any]],
    required_spec: str,
    routing_plan: RoutingPlan,
    rag_index: str = "",
) -> list[dict[str, Any]]:
    """Build DAG graph based on routing plan's required agents."""
    payload = {"request": request, "quotes": quotes, "required_spec": required_spec,
               "rag_index": rag_index}
    
    agents = routing_plan.required_agents
    if not agents:
        # Default to full procurement if no agents specified
        agents = FULL_PROCUREMENT_AGENTS
    
    # Map agent names to task definitions
    agent_tasks = {
        "price": {"task_id": "price-1", "task_type": "price", "deps": [], "payload": dict(payload)},
        "vendor": {"task_id": "vendor-1", "task_type": "vendor", "deps": [], "payload": dict(payload)},
        "contract": {"task_id": "contract-1", "task_type": "contract", "deps": [], "payload": dict(payload)},
        "spec": {"task_id": "spec-1", "task_type": "spec", "deps": [], "payload": dict(payload)},
        "analysis": {"task_id": "analysis-1", "task_type": "analysis", "deps": [], "payload": dict(payload)},
        "verification": {"task_id": "verification-1", "task_type": "verification", "deps": [], "payload": dict(payload)},
    }
    
    # Build graph in topological order
    graph = []
    completed = set()
    
    # First pass: add leaf agents (no deps among themselves)
    for agent in agents:
        if agent in ("price", "vendor", "contract", "spec"):
            graph.append(agent_tasks[agent])
            completed.add(agent)
    
    # Second pass: add analysis if any leaf agents were included
    leaf_agents = {"price", "vendor", "contract", "spec"} & set(agents)
    if leaf_agents and "analysis" in agents:
        analysis_task = dict(agent_tasks["analysis"])
        analysis_task["deps"] = [f"{a}-1" for a in leaf_agents]
        graph.append(analysis_task)
        completed.add("analysis")
    
    # Third pass: add verification if analysis was included
    if "analysis" in completed and "verification" in agents:
        graph.append(agent_tasks["verification"])
    
    return graph


def run_procurement_case(
    request: str,
    quotes: list[dict[str, Any]],
    required_spec: str = "",
    workers: int = 4,
    timeout: float = 120.0,
    db_path: str = "",
    store: AsyncTaskStore | None = None,
    bus: Any | None = None,
    routing_plan: RoutingPlan | None = None,
) -> dict[str, Any]:
    """Execute the procurement DAG based on routing plan. Returns the aggregate report.

    The verification node output (agent text) is returned under
    `aggregate["results"]["verification-1"]`, and the parsed Recommendation
    JSON under `aggregate["recommendation"]`.

    Args:
        request: User request text
        quotes: List of quote dicts
        required_spec: Required specification string
        workers: Number of parallel workers
        timeout: Timeout in seconds
        db_path: Database path
        store: Optional AsyncTaskStore
        bus: Optional message bus
        routing_plan: Optional RoutingPlan for router-first architecture.
                     If not provided, defaults to full procurement (6 agents).
    """
    own_store = store or AsyncTaskStore(db_path or default_procurement_db())
    own_bus = bus if bus is not None else InMemoryBus()
    
    # Use provided routing plan or default to full procurement
    if routing_plan is None:
        from ..router import route
        routing_plan = route(request)
    
    # Multi-Agent RAG: index the case corpus (quotes + vendor registry + spec)
    # so every specialist retrieves cited evidence instead of reasoning blind.
    from ..rag import build_case_index
    rag_path = (db_path or default_procurement_db()) + ".rag.json"
    try:
        build_case_index(quotes, required_spec).save(rag_path)
    except Exception:
        rag_path = ""
    
    # Emit scrape trigger for external data enrichment (scraper_worker consumes this).
    try:
        from ..scraper.contract import ScrapeSource, ScrapeTask
        vendor_names = list({str(q.get("vendor", "")) for q in quotes if q.get("vendor")})
        keys = [request] + [f"{v} price list" for v in vendor_names[:5]]
        scrape_task = ScrapeTask(
            task_id=new_id("scrape-"),
            keys=keys,
            source_type=ScrapeSource.WEB,
            policy={},
        )
        ex, rk, q = routing_for("scrape")
        own_bus.publish(ex, rk, scrape_task.model_dump())
    except Exception:
        pass
    
    graph = _build_graph_for_plan(request, quotes, required_spec, routing_plan, rag_path)
    handlers = build_procurement_handlers(own_store)
    verifier = Verifier(by_task_type=dict(PROCUREMENT_VALIDATORS))
    orch = AsyncOrchestrator(own_store, own_bus)
    agg = orch.run_workflow(graph, handlers, workers=workers, timeout=timeout,
                            verifier=verifier)
    
    # surface the parsed recommendation for API/Telegram layers
    rec: dict[str, Any] = {}
    try:
        results = agg.get("results", {})

        def _rows_for(suffix: str) -> list:
            # results keys may carry the workflow prefix (T2.5), e.g.
            # "{wf}-verification-1" — resolve by exact id or suffix.
            for key, rows in results.items():
                if key == suffix or str(key).endswith(f"-{suffix}"):
                    return rows or []
            return []

        vrows = _rows_for("verification-1")
        arows = _rows_for("analysis-1")
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
    agg["routing_plan"] = routing_plan.model_dump() if routing_plan else None
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
        # Use full procurement graph for benchmark
        from ..router import route
        routing_plan = route(request)
        graph = _build_graph_for_plan(request, quotes, required_spec, routing_plan, rag_path)
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
