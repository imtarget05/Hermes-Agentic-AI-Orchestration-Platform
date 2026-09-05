from hermes.async_engine.dag import build_dag, resolve_ready


def test_parallel_branches_then_join():
    # Research -> {Analyze, Analyze2} -> Report
    dag = build_dag([
        {"task_id": "research", "task": {"task_type": "research"}, "deps": []},
        {"task_id": "analyze-1", "task": {"task_type": "analyze"}, "deps": ["research"]},
        {"task_id": "analyze-2", "task": {"task_type": "analyze"}, "deps": ["research"]},
        {"task_id": "report", "task": {"task_type": "report"},
         "deps": ["analyze-1", "analyze-2"]},
    ])

    # only the root is ready at first
    assert dag.ready_tasks() == ["research"]

    # research done -> both analyze branches become ready (parallel)
    newly = dag.mark_completed("research")
    assert set(newly) == {"analyze-1", "analyze-2"}
    assert set(dag.ready_tasks()) == {"analyze-1", "analyze-2"}

    # report must wait for BOTH analyze tasks
    dag.mark_completed("analyze-1")
    assert "report" not in dag.ready_tasks()
    dag.mark_completed("analyze-2")
    assert dag.ready_tasks() == ["report"]
    assert dag.is_leaf("report")


def test_roots_and_sequential_chain():
    dag = build_dag([
        {"task_id": "a", "task": {}, "deps": []},
        {"task_id": "b", "task": {}, "deps": ["a"]},
        {"task_id": "c", "task": {}, "deps": ["b"]},
    ])
    assert dag.roots() == ["a"]
    assert dag.ready_tasks() == ["a"]
    dag.mark_completed("a")
    assert dag.ready_tasks() == ["b"]
    dag.mark_completed("b")
    assert dag.ready_tasks() == ["c"]


def test_resolve_ready_returns_payloads():
    dag = build_dag([{"task_id": "x", "task": {"hello": 1}, "deps": []}])
    assert resolve_ready(dag)[0]["hello"] == 1


def test_failed_dep_does_not_unblock():
    dag = build_dag([
        {"task_id": "a", "task": {}, "deps": []},
        {"task_id": "b", "task": {}, "deps": ["a"]},
    ])
    dag.mark_failed("a")
    assert dag.ready_tasks() == []  # b stays blocked