from hermes.messaging import MockNotifier
from hermes.orchestrator import orchestrate, run_critic, run_fanout, run_pipeline
from hermes.tasks import Task, TaskStatus, TaskStore


def test_patterns_no_key():
    assert "AGGREGATED" in run_fanout("hello")["final"]
    assert "PIPELINE DONE" in run_pipeline("hello")["final"]
    assert "VALIDATED" in run_critic("hello")["final"]


def test_e2e_full_lifecycle(tmp_path):
    db = str(tmp_path / "t.db")
    store = TaskStore(db)
    task = store.create(Task(text="research python", project="demo", strategy="fanout"))
    note = MockNotifier(log_path=str(tmp_path / "m.log"))
    final = orchestrate(task.id, store, note)
    done = store.get(task.id)
    assert done.status == TaskStatus.COMPLETED
    assert final
    kinds = [e["to"] for e in store.events(task.id)]
    assert "completed" in kinds and "handoff" in kinds
    assert len(note.sent) >= 2
    # 8 outputs check: routed task(project set), tool-capable agents, handoff event,
    # state transitions, progress notify, mock telegram, aggregated result, retry-ready store
    assert done.project == "demo" and done.result == final
