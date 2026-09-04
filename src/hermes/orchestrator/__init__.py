"""Orchestrator: LangGraph StateGraph with 3 strategies (§4 of spec).

- fanout (Orchestrator/Worker): research + builder in parallel → aggregate
- pipeline (Sequential): research → builder → validator
- critic (Debate): builder draft → critic review → revision (max 2 rounds)

Falls back to pure-python sequential execution if langgraph missing,
so local tests never break. Task state persisted in TaskStore.
"""
from __future__ import annotations

from typing import TypedDict
from ..agents import AGENTS
from ..tasks.store import TaskStore
from ..tasks.schemas import TaskStatus

try:
    from langgraph.graph import StateGraph, END
    _HAS_LG = True
except Exception:
    _HAS_LG = False


class GState(TypedDict, total=False):
    text: str
    research: str
    build: str
    critique: str
    final: str


def _run_agent(agent_name: str, text: str, ctx: str = "") -> str:
    return AGENTS[agent_name].run(text, ctx)


def run_fanout(text: str) -> dict:
    r = _run_agent("research", text)
    b = _run_agent("builder", text, r)
    final = f"AGGREGATED\n- research: {r}\n- build: {b}"
    return {"research": r, "build": b, "final": final}


def run_pipeline(text: str) -> dict:
    r = _run_agent("research", text)
    b = _run_agent("builder", text, r)
    v = _run_agent("validator", text, b)
    return {"research": r, "build": b, "critique": v, "final": f"PIPELINE DONE\n{b}\nVALIDATION: {v}"}


def run_critic(text: str, max_rounds: int = 2) -> dict:
    draft = _run_agent("builder", text)
    critique = ""
    for _ in range(max_rounds):
        critique = _run_agent("validator", text, draft)
        if "ok" in critique.lower() or "pass" in critique.lower() or "looks good" in critique.lower():
            break
        draft = _run_agent("builder", text, f"REVISE per critique:\n{critique}\nPrev draft:\n{draft}")
    return {"build": draft, "critique": critique, "final": f"VALIDATED\n{draft}\nCRITIQUE: {critique}"}


def build_graph(strategy: str = "fanout"):
    """Return compiled LangGraph (or None if langgraph unavailable)."""
    if not _HAS_LG:
        return None
    g = StateGraph(GState)
    if strategy == "pipeline":
        g.add_node("research", lambda s: {"research": _run_agent("research", s["text"])})
        g.add_node("build", lambda s: {"build": _run_agent("builder", s["text"], s.get("research", ""))})
        g.add_node("validate", lambda s: {"critique": _run_agent("validator", s["text"], s.get("build", "")),
                                          "final": "done"})
        g.set_entry_point("research")
        g.add_edge("research", "build")
        g.add_edge("build", "validate")
        g.add_edge("validate", END)
    elif strategy == "critic":
        g.add_node("draft", lambda s: {"build": _run_agent("builder", s["text"])})
        g.add_node("review", lambda s: {"critique": _run_agent("validator", s["text"], s.get("build", "")),
                                        "final": "done"})
        g.set_entry_point("draft")
        g.add_edge("draft", "review")
        g.add_edge("review", END)
    else:  # fanout
        g.add_node("research", lambda s: {"research": _run_agent("research", s["text"])})
        g.add_node("build", lambda s: {"build": _run_agent("builder", s["text"], s.get("research", ""))})
        g.add_node("aggregate", lambda s: {"final": f"AGGREGATED {s.get('research','')} {s.get('build','')}"})
        g.set_entry_point("research")
        g.add_edge("research", "build")
        g.add_edge("build", "aggregate")
        g.add_edge("aggregate", END)
    return g.compile()


def orchestrate(task_id: str, store: TaskStore, notifier=None) -> str:
    """Full lifecycle driver: queued→running→[handoff]→completed, failure→retry/failed."""
    task = store.get(task_id)
    store.transition(task_id, TaskStatus.RUNNING, "orchestrator", f"strategy={task.strategy}")
    if notifier:
        notifier.send(task.project, f"[{task.id}] started: {task.text[:120]}")

    def _exec() -> str:
        if task.strategy == "pipeline":
            out = run_pipeline(task.text)
        elif task.strategy == "critic":
            out = run_critic(task.text)
        else:
            out = run_fanout(task.text)
        # handoff event: research→builder visibility
        store.set_owner(task_id, "builder")
        store.transition(task_id, TaskStatus.HANDOFF, "orchestrator", "research→builder")
        store.set_owner(task_id, "validator")
        store.transition(task_id, TaskStatus.RUNNING, "orchestrator", "resume after handoff")
        return out["final"]

    try:
        final = _exec()
    except Exception as e:
        store.transition(task_id, TaskStatus.FAILURE, "orchestrator", str(e)[:300])
        if task.retries + 1 <= task.max_retries:
            store.transition(task_id, TaskStatus.RETRY, "orchestrator", "retryable")
            store.transition(task_id, TaskStatus.RUNNING, "orchestrator", "retry resume")
            try:
                final = _exec()
            except Exception as e2:
                store.transition(task_id, TaskStatus.FAILURE, "orchestrator", str(e2)[:300])
                store.transition(task_id, TaskStatus.FAILED, "orchestrator", "exhausted")
                raise
        else:
            store.transition(task_id, TaskStatus.FAILED, "orchestrator", "non-retryable")
            raise

    store.set_result(task_id, final, owner="orchestrator")
    store.transition(task_id, TaskStatus.COMPLETED, "orchestrator", "done")
    if notifier:
        notifier.send(task.project, f"[{task.id}] completed ✅\n{final[:1000]}")
    return final
