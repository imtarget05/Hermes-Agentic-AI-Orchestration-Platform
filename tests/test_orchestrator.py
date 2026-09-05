from hermes.messaging import MockNotifier
from hermes.orchestrator import orchestrate, run_critic, run_fanout, run_pipeline
from hermes.runtime import default_demo_quotes
from hermes.tasks import Task, TaskStatus, TaskStore


def test_legacy_strategies_map_to_procurement():
    assert "PROCUREMENT" in run_fanout("hello")["final"]
    assert "PROCUREMENT" in run_pipeline("hello")["final"]
    assert "PROCUREMENT" in run_critic("hello")["final"]


def test_e2e_full_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROCUREMENT_DB", str(tmp_path / "p.db"))
    monkeypatch.setenv("HERMES_HITL_AUTO_APPROVE", "true")
    db = str(tmp_path / "t.db")
    store = TaskStore(db)
    task = store.create(Task(text="mua 50 laptop cho team Engineering",
                             project="demo", strategy="procurement"))
    note = MockNotifier(log_path=str(tmp_path / "m.log"))
    final = orchestrate(task.id, store, note, quotes=default_demo_quotes())
    done = store.get(task.id)
    assert done.status == TaskStatus.COMPLETED
    assert "Lenovo" in final
    kinds = [e["to"] for e in store.events(task.id)]
    assert "completed" in kinds and "handoff" in kinds
    assert len(note.sent) >= 2
    assert done.project == "demo" and "Lenovo" in done.result
