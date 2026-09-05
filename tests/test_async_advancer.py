from hermes.async_engine.backends import InMemoryBus
from hermes.async_engine.contract import Task, TaskStatus
from hermes.async_engine.orchestrator import advance_once
from hermes.async_engine.store import AsyncTaskStore


def _store(tmp_path):
    return AsyncTaskStore(str(tmp_path / "t.db"))


def _chain(store, workflow_id="wf1"):
    """research -> analyze -> report (linear chain, persisted deps)."""
    for tid, tt in [("r", "research"), ("a", "analyze"), ("p", "report")]:
        store.create_task(Task(task_id=tid, workflow_id=workflow_id, task_type=tt,
                               status=TaskStatus.QUEUED))
    store.add_dependencies("a", ["r"])
    store.add_dependencies("p", ["a"])


def _run(store, task_id):
    store.mark_started(task_id, "w-01")
    store.mark_completed(task_id, result_uri=f"s3://x/{task_id}")


def test_advance_once_dispatches_only_root(tmp_path):
    store, bus = _store(tmp_path), InMemoryBus()
    _chain(store)
    assert advance_once(store, bus) == ["r"]  # only the root is dispatchable


def test_advance_never_republishes(tmp_path):
    store, bus = _store(tmp_path), InMemoryBus()
    _chain(store)
    assert advance_once(store, bus) == ["r"]
    assert advance_once(store, bus) == []  # marked queued -> no re-publish
    from hermes.async_engine.backends import ROUTING
    ex, rk, q = ROUTING["research"]
    assert bus.queue_depth(q) == 1


def test_advancer_walks_chain_as_tasks_complete(tmp_path):
    store, bus = _store(tmp_path), InMemoryBus()
    _chain(store)
    assert advance_once(store, bus) == ["r"]
    _run(store, "r")                       # worker completes research
    assert advance_once(store, bus) == ["a"]   # analyze unblocked
    _run(store, "a")
    assert advance_once(store, bus) == ["p"]   # report unblocked
    _run(store, "p")
    assert advance_once(store, bus) == []


def test_advancer_finalizes_completed_workflow(tmp_path):
    store = _store(tmp_path)
    store.create_workflow("wf-ok")
    for tid in ("t1", "t2"):
        store.create_task(Task(task_id=tid, workflow_id="wf-ok", task_type="analyze",
                               status=TaskStatus.QUEUED))
    assert store.finalize_workflows() == []    # still running
    _run(store, "t1")
    assert store.finalize_workflows() == []
    _run(store, "t2")
    assert store.finalize_workflows() == ["wf-ok"]
    assert store.workflow_status("wf-ok") == "completed"


def test_advancer_marks_failed_workflow(tmp_path):
    store = _store(tmp_path)
    store.create_workflow("wf-bad")
    for tid in ("t1", "t2"):
        store.create_task(Task(task_id=tid, workflow_id="wf-bad", task_type="analyze",
                               status=TaskStatus.QUEUED))
    store.mark_completed("t1")
    store.mark_failed("t2", "authentication failed")
    assert store.finalize_workflows() == ["wf-bad"]
    assert store.workflow_status("wf-bad") == "failed"


def test_blocked_task_stays_dispatchable_after_dep_failed(tmp_path):
    store, bus = _store(tmp_path), InMemoryBus()
    store.create_task(Task(task_id="root", workflow_id="wf", task_type="research",
                           status=TaskStatus.QUEUED))
    store.create_task(Task(task_id="child", workflow_id="wf", task_type="analyze",
                           status=TaskStatus.QUEUED))
    store.add_dependencies("child", ["root"])
    assert advance_once(store, bus) == ["root"]
    store.mark_completed("root")
    assert advance_once(store, bus) == ["child"]
    assert store.get_task("child").status == TaskStatus.QUEUED