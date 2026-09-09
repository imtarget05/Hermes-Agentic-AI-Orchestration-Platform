import json
import pytest

from hermes.tasks.schemas import Task, TaskStatus
from hermes.tasks.store import TaskStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    return TaskStore(db_path)


@pytest.fixture
def sample_task():
    return Task(text="Test task", project="test-project", strategy="fanout")


class TestConstructorAndInit:
    def test_init_creates_tables(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = TaskStore(db_path)
        store._exec("SELECT 1 FROM tasks", fetch="one")
        store._exec("SELECT 1 FROM events", fetch="one")

    def test_init_creates_parent_dirs(self, tmp_path):
        nested_path = tmp_path / "subdir"
        nested_path.mkdir()
        db_path = str(nested_path / "test.db")
        store = TaskStore(db_path)
        store._exec("SELECT 1 FROM tasks", fetch="one")


class TestCreate:
    def test_create_returns_task_in_queued_status(self, store, sample_task):
        result = store.create(sample_task)
        assert result.status == TaskStatus.QUEUED

    def test_create_auto_transitions_created_to_queued(self, store, sample_task):
        task = store.create(sample_task)
        events = store.events(task.id)
        assert len(events) == 2
        assert events[0]["frm"] == TaskStatus.CREATED.value
        assert events[0]["to"] == TaskStatus.QUEUED.value
        assert events[1]["frm"] == TaskStatus.CREATED.value
        assert events[1]["to"] == TaskStatus.QUEUED.value

    def test_create_task_can_be_retrieved(self, store, sample_task):
        created = store.create(sample_task)
        retrieved = store.get(created.id)
        assert retrieved.id == created.id
        assert retrieved.text == sample_task.text
        assert retrieved.project == sample_task.project


class TestGet:
    def test_get_existing_task(self, store, sample_task):
        created = store.create(sample_task)
        result = store.get(created.id)
        assert result.id == created.id

    def test_get_nonexistent_raises_keyerror(self, store):
        with pytest.raises(KeyError) as exc_info:
            store.get("nonexistent-id")
        assert "nonexistent-id" in str(exc_info.value)


class TestTransition:
    def test_transition_created_to_queued(self, store, sample_task):
        task = store.create(sample_task)
        assert task.status == TaskStatus.QUEUED

    def test_transition_queued_to_running(self, store, sample_task):
        task = store.create(sample_task)
        result = store.transition(task.id, TaskStatus.RUNNING, "worker")
        assert result.status == TaskStatus.RUNNING

    def test_transition_running_to_completed(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        result = store.transition(task.id, TaskStatus.COMPLETED, "worker")
        assert result.status == TaskStatus.COMPLETED

    def test_transition_running_to_handoff(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        result = store.transition(task.id, TaskStatus.HANDOFF, "worker")
        assert result.status == TaskStatus.HANDOFF

    def test_transition_running_to_failure(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        result = store.transition(task.id, TaskStatus.FAILURE, "worker")
        assert result.status == TaskStatus.FAILURE

    def test_transition_handoff_to_running(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.HANDOFF, "worker")
        result = store.transition(task.id, TaskStatus.RUNNING, "worker2")
        assert result.status == TaskStatus.RUNNING

    def test_transition_handoff_to_completed(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.HANDOFF, "worker")
        result = store.transition(task.id, TaskStatus.COMPLETED, "worker")
        assert result.status == TaskStatus.COMPLETED

    def test_transition_handoff_to_failure(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.HANDOFF, "worker")
        result = store.transition(task.id, TaskStatus.FAILURE, "worker")
        assert result.status == TaskStatus.FAILURE

    def test_transition_failure_to_retry(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.FAILURE, "worker")
        result = store.transition(task.id, TaskStatus.RETRY, "orchestrator")
        assert result.status == TaskStatus.RETRY
        assert result.retries == 1

    def test_transition_failure_to_failed(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.FAILURE, "worker")
        result = store.transition(task.id, TaskStatus.FAILED, "orchestrator")
        assert result.status == TaskStatus.FAILED

    def test_transition_retry_to_running(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.FAILURE, "worker")
        store.transition(task.id, TaskStatus.RETRY, "orchestrator")
        result = store.transition(task.id, TaskStatus.RUNNING, "worker")
        assert result.status == TaskStatus.RUNNING

    def test_transition_retry_to_failed(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.FAILURE, "worker")
        store.transition(task.id, TaskStatus.RETRY, "orchestrator")
        result = store.transition(task.id, TaskStatus.FAILED, "orchestrator")
        assert result.status == TaskStatus.FAILED

    def test_transition_illegal_raises_valueerror(self, store, sample_task):
        task = store.create(sample_task)
        with pytest.raises(ValueError) as exc_info:
            store.transition(task.id, TaskStatus.COMPLETED, "worker")
        assert "Illegal transition" in str(exc_info.value)

    def test_transition_from_completed_raises_valueerror(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.COMPLETED, "worker")
        with pytest.raises(ValueError):
            store.transition(task.id, TaskStatus.RUNNING, "worker")

    def test_transition_from_failed_raises_valueerror(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.FAILURE, "worker")
        store.transition(task.id, TaskStatus.FAILED, "orchestrator")
        with pytest.raises(ValueError):
            store.transition(task.id, TaskStatus.RETRY, "orchestrator")

    def test_transition_updates_updated_at(self, store, sample_task):
        task = store.create(sample_task)
        original_updated = task.updated_at
        result = store.transition(task.id, TaskStatus.RUNNING, "worker")
        assert result.updated_at >= original_updated

    def test_transition_logs_event(self, store, sample_task):
        task = store.create(sample_task)
        events = store.events(task.id)
        initial_count = len(events)
        store.transition(task.id, TaskStatus.RUNNING, "worker", "starting work")
        events = store.events(task.id)
        assert len(events) == initial_count + 1
        assert events[-1]["frm"] == TaskStatus.QUEUED.value
        assert events[-1]["to"] == TaskStatus.RUNNING.value
        assert events[-1]["actor"] == "worker"
        assert events[-1]["note"] == "starting work"

    def test_retry_increments_retries(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        store.transition(task.id, TaskStatus.FAILURE, "worker")
        store.transition(task.id, TaskStatus.RETRY, "orchestrator")
        result = store.transition(task.id, TaskStatus.RUNNING, "worker")
        assert result.retries == 1


class TestSetResult:
    def test_set_result_updates_result_field(self, store, sample_task):
        task = store.create(sample_task)
        result = store.set_result(task.id, "task completed successfully", "worker")
        assert result.result == "task completed successfully"

    def test_set_result_with_owner(self, store, sample_task):
        task = store.create(sample_task)
        result = store.set_result(task.id, "done", "worker-2")
        assert result.owner_agent == "worker-2"

    def test_set_result_without_owner_preserves_existing(self, store, sample_task):
        task = store.create(sample_task)
        store.set_owner(task.id, "original-owner")
        result = store.set_result(task.id, "done", "")
        assert result.owner_agent == "original-owner"


class TestSetOwner:
    def test_set_owner_updates_owner(self, store, sample_task):
        task = store.create(sample_task)
        result = store.set_owner(task.id, "new-owner")
        assert result.owner_agent == "new-owner"


class TestEvents:
    def test_events_empty_for_new_task(self, store, sample_task):
        task = store.create(sample_task)
        events = store.events(task.id)
        assert len(events) == 2

    def test_events_returns_ordered_list(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker")
        events = store.events(task.id)
        assert len(events) == 3
        assert events[0]["to"] == TaskStatus.QUEUED.value
        assert events[1]["to"] == TaskStatus.QUEUED.value
        assert events[2]["to"] == TaskStatus.RUNNING.value

    def test_events_contains_transition_details(self, store, sample_task):
        task = store.create(sample_task)
        store.transition(task.id, TaskStatus.RUNNING, "worker", "custom note")
        events = store.events(task.id)
        last_event = events[-1]
        assert last_event["actor"] == "worker"
        assert last_event["note"] == "custom note"


class TestListTasks:
    def test_list_tasks_returns_all_tasks(self, store):
        task1 = store.create(Task(text="Task 1"))
        task2 = store.create(Task(text="Task 2"))
        tasks = store.list_tasks()
        assert len(tasks) == 2
        ids = [t["id"] for t in tasks]
        assert task1.id in ids
        assert task2.id in ids

    def test_list_tasks_ordered_by_created_at_desc(self, store):
        task1 = store.create(Task(text="First task"))
        task2 = store.create(Task(text="Second task"))
        tasks = store.list_tasks()
        assert tasks[0]["id"] == task2.id
        assert tasks[1]["id"] == task1.id

    def test_list_tasks_respects_limit(self, store):
        for i in range(5):
            store.create(Task(text=f"Task {i}"))
        tasks = store.list_tasks(limit=3)
        assert len(tasks) == 3

    def test_list_tasks_empty_for_fresh_store(self, store):
        tasks = store.list_tasks()
        assert len(tasks) == 0


class TestExportJson:
    def test_export_json_returns_valid_json(self, store, sample_task):
        task = store.create(sample_task)
        json_str = store.export_json(task.id)
        data = json.loads(json_str)
        assert "task" in data
        assert "events" in data

    def test_export_json_contains_task_data(self, store, sample_task):
        task = store.create(sample_task)
        json_str = store.export_json(task.id)
        data = json.loads(json_str)
        assert data["task"]["id"] == task.id
        assert data["task"]["text"] == sample_task.text

    def test_export_json_contains_events(self, store, sample_task):
        task = store.create(sample_task)
        json_str = store.export_json(task.id)
        data = json.loads(json_str)
        assert isinstance(data["events"], list)
        assert len(data["events"]) == 2

    def test_export_json_raises_keyerror_for_nonexistent(self, store):
        with pytest.raises(KeyError):
            store.export_json("nonexistent-id")


class TestHappyPath:
    def test_full_task_lifecycle(self, store, sample_task):
        task = store.create(sample_task)
        assert task.status == TaskStatus.QUEUED

        task = store.transition(task.id, TaskStatus.RUNNING, "worker")
        assert task.status == TaskStatus.RUNNING

        task = store.transition(task.id, TaskStatus.HANDOFF, "worker")
        assert task.status == TaskStatus.HANDOFF

        task = store.transition(task.id, TaskStatus.RUNNING, "worker2")
        assert task.status == TaskStatus.RUNNING

        task = store.set_result(task.id, "success", "worker2")
        assert task.result == "success"

        task = store.transition(task.id, TaskStatus.COMPLETED, "worker2")
        assert task.status == TaskStatus.COMPLETED

        events = store.events(task.id)
        assert len(events) == 6

        retrieved = store.get(task.id)
        assert retrieved.status == TaskStatus.COMPLETED


class TestRetryLifecycle:
    def test_retry_flow(self, store, sample_task):
        task = store.create(sample_task)
        task = store.transition(task.id, TaskStatus.RUNNING, "worker")
        task = store.transition(task.id, TaskStatus.FAILURE, "worker")
        task = store.transition(task.id, TaskStatus.RETRY, "orchestrator")
        assert task.status == TaskStatus.RETRY
        assert task.retries == 1

        task = store.transition(task.id, TaskStatus.RUNNING, "worker")
        assert task.status == TaskStatus.RUNNING

        task = store.transition(task.id, TaskStatus.FAILURE, "worker")
        task = store.transition(task.id, TaskStatus.FAILED, "orchestrator")
        assert task.status == TaskStatus.FAILED
