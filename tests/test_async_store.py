from hermes.async_engine.contract import Task, TaskStatus
from hermes.async_engine.store import AsyncTaskStore


def _store(tmp_path):
    return AsyncTaskStore(str(tmp_path / "t.db"))


def _task(**kw):
    base = dict(task_type="analyze", workflow_id="wf1", task_id=kw.pop("task_id", "t1"))
    base.update(kw)
    return Task(**base)


def test_create_and_get(tmp_path):
    s = _store(tmp_path)
    s.create_task(_task())
    t = s.get_task("t1")
    assert t.task_type == "analyze" and t.workflow_id == "wf1"
    assert t.status == TaskStatus.CREATED


def test_workflow_lifecycle(tmp_path):
    s = _store(tmp_path)
    s.create_workflow("wf-x")
    assert s.workflow_status("wf-x") == "running"
    s.complete_workflow("wf-x", "completed")
    assert s.workflow_status("wf-x") == "completed"


def test_mark_started_and_results(tmp_path):
    s = _store(tmp_path)
    s.create_task(_task())
    assert s.mark_started("t1", "worker-01") is True
    assert s.get_task("t1").status == TaskStatus.STARTED
    s.mark_completed("t1", result_uri="s3://b/1", worker_id="worker-01")
    assert s.is_completed("t1") is True
    results = s.task_results("t1")
    assert results[0]["result_uri"] == "s3://b/1"
    assert results[0]["result_hash"]


def test_idempotency_never_reexecutes_completed(tmp_path):
    s = _store(tmp_path)
    s.create_task(_task())
    s.mark_completed("t1")
    # after completion, mark_started must refuse (idempotency guard)
    assert s.mark_started("t1", "worker-02") is False


def test_second_claim_refused(tmp_path):
    s = _store(tmp_path)
    s.create_task(_task())
    assert s.mark_started("t1", "worker-01") is True
    # a second concurrent worker cannot claim the same in-flight task
    assert s.mark_started("t1", "worker-02") is False


def test_mark_retried_increments_attempt(tmp_path):
    s = _store(tmp_path)
    s.create_task(_task())
    s.mark_retried("t1", attempt=2, worker_id="worker-01")
    t = s.get_task("t1")
    assert t.attempt == 2 and t.status == TaskStatus.RETRY


def test_mark_failed_records_error(tmp_path):
    s = _store(tmp_path)
    s.create_task(_task())
    s.mark_failed("t1", "authentication failed", worker_id="worker-01")
    t = s.get_task("t1")
    assert t.status == TaskStatus.FAILED
    assert s.execution_state("t1") == TaskStatus.FAILED.value
    assert s.task_counts().get("failed") == 1


def test_list_and_counts(tmp_path):
    s = _store(tmp_path)
    for i in range(3):
        s.create_task(_task(task_id=f"t{i}"))
    s.mark_completed("t0")
    assert s.task_counts().get("completed") == 1
    assert len(s.list_tasks()) == 3