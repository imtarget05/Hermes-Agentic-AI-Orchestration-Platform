
from hermes.async_engine.backends import InMemoryBus, _queue_for
from hermes.async_engine.contract import EVENT_COMPLETED, ROUTING, Task
from hermes.async_engine.eventbus import InMemoryEventBus, JsonlEventBus


def test_queue_for_maps_routing_to_spec_queues():
    for tt, (ex, rk, q) in ROUTING.items():
        assert _queue_for(ex, rk) == q


def test_inmemory_bus_manual_ack_semantics():
    bus = InMemoryBus()
    ex, rk, q = ROUTING["research"]
    bus.declare(ex, rk, q)
    bus.publish(ex, rk, {"task_type": "research"})
    assert bus.queue_depth(q) == 1
    dlv = bus.get(q)
    assert dlv is not None
    assert bus.queue_depth(q) == 0  # fetched (in-flight, removed from queue)
    dlv.ack()  # manual ack


def test_inmemory_bus_requeue_respects_delay():
    import time
    bus = InMemoryBus()
    ex, rk, q = ROUTING["analyze"]
    bus.declare(ex, rk, q)
    bus.publish(ex, rk, {"task_type": "analyze"})
    raw = bus.get(q).message
    bus.requeue(q, raw, delay_seconds=0.25)
    # not due yet -> get returns None
    assert bus.get(q) is None
    time.sleep(0.30)
    assert bus.get(q) is not None


def test_deadletter_queue_target():
    bus = InMemoryBus()
    from hermes.async_engine.backends import DEAD_LETTER_QUEUE
    bus.publish_deadletter({"x": 1})
    assert bus.queue_depth(DEAD_LETTER_QUEUE) == 1


def test_jsonl_event_bus(tmp_path):
    path = tmp_path / "events.jsonl"
    bus = JsonlEventBus(path)
    bus.emit(EVENT_COMPLETED, task_id="t1", workflow_id="w1", worker_id="w-01", attempt=1)
    bus.emit(EVENT_COMPLETED, task_id="t2", workflow_id="w1", worker_id="w-01", attempt=1)
    records = bus.read()
    assert len(records) == 2
    assert records[0]["event_type"] == EVENT_COMPLETED
    assert records[0]["task_id"] == "t1"
    assert records[0]["event_id"]  # uuid present


def test_inmemory_event_bus_filters():
    bus = InMemoryEventBus()
    bus.emit(EVENT_COMPLETED, task_id="a")
    bus.emit("task.started", task_id="a")
    assert len(bus.of(EVENT_COMPLETED)) == 1


def test_task_roundtrip_through_message():
    t = Task(task_type="report", workflow_id="wf", payload={"k": "v"}, metadata={"m": 1})
    t2 = Task.from_message(t.to_message())
    assert t2.task_id == t.task_id
    assert t2.payload == {"k": "v"}
    assert t2.metadata == {"m": 1}
    assert t2.task_type == "report"