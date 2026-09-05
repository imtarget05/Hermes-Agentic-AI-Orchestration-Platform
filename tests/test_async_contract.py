from hermes.async_engine.contract import (
    EXCHANGE_TASKS,
    Task,
    TaskStatus,
    Workflow,
    routing_for,
)


def test_task_contract_has_canonical_fields():
    t = Task(task_type="research", workflow_id="wf-1")
    msg = t.to_message()
    for key in ("task_id", "workflow_id", "parent_task_id", "task_type",
                "priority", "attempt", "max_attempts", "created_at", "deadline",
                "payload", "metadata"):
        assert key in msg, f"missing canonical field {key}"
    assert t.status == TaskStatus.CREATED


def test_routing_registry_covers_spec_queues():
    assert routing_for("research")[2] == "q.agent.research"
    assert routing_for("analyze")[2] == "q.agent.analyze"
    assert routing_for("report")[2] == "q.agent.report"
    assert routing_for("notify")[2] == "q.agent.notify"
    for tt in ("research", "analyze", "report", "notify"):
        ex, rk, q = routing_for(tt)
        assert ex == EXCHANGE_TASKS and ex in ("hermes.tasks",)
        assert rk.startswith("agent.")


def test_unknown_routing_key_raises():
    import pytest
    with pytest.raises(KeyError):
        routing_for("bogus")


def test_validate_transition():
    from hermes.async_engine.contract import validate_transition
    validate_transition(TaskStatus.CREATED, TaskStatus.QUEUED)
    validate_transition(TaskStatus.STARTED, TaskStatus.RETRY)
    try:
        validate_transition(TaskStatus.COMPLETED, TaskStatus.STARTED)
        raise AssertionError("expected illegal transition error")
    except ValueError:
        pass


def test_workflow_default_status():
    wf = Workflow()
    assert wf.status == "running"
    wf.done("completed")
    assert wf.status == "completed" and wf.completed_at