import pytest

from hermes.tasks.schemas import TaskStatus, validate_transition


def test_illegal_transition():
    with pytest.raises(ValueError):
        validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)


def test_failure_paths():
    validate_transition(TaskStatus.RUNNING, TaskStatus.FAILURE)
    validate_transition(TaskStatus.FAILURE, TaskStatus.RETRY)
    validate_transition(TaskStatus.RETRY, TaskStatus.RUNNING)
    validate_transition(TaskStatus.FAILURE, TaskStatus.FAILED)
