"""Tests for task queue."""

import pytest
from executor.task_queue import TaskQueue, TaskQueueError


@pytest.fixture
def queue():
    """Create empty task queue."""
    return TaskQueue()


@pytest.fixture
def sample_validated_action():
    """Sample ValidatedAction."""
    return {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test.txt"}
    }


def test_queue_starts_empty(queue):
    """Test queue starts empty."""
    assert queue.is_empty() is True
    assert queue.size() == 0


def test_enqueue_single_task(queue, sample_validated_action):
    """Test enqueuing single task."""
    task_id = "a" * 64

    result = queue.enqueue(sample_validated_action, task_id)

    assert result is True
    assert queue.size() == 1
    assert queue.is_empty() is False
    assert queue.contains(task_id) is True


def test_enqueue_duplicate_returns_false(queue, sample_validated_action):
    """Test enqueueing duplicate task_id returns False."""
    task_id = "a" * 64

    result1 = queue.enqueue(sample_validated_action, task_id)
    result2 = queue.enqueue(sample_validated_action, task_id)

    assert result1 is True
    assert result2 is False
    assert queue.size() == 1


def test_enqueue_invalid_task_id_raises_error(queue, sample_validated_action):
    """Test enqueueing with invalid task_id raises error."""
    invalid_task_id = "short"

    with pytest.raises(TaskQueueError, match="Invalid task_id"):
        queue.enqueue(sample_validated_action, invalid_task_id)


def test_enqueue_invalid_validated_action_raises_error(queue):
    """Test enqueueing non-dict raises error."""
    task_id = "a" * 64

    with pytest.raises(TaskQueueError, match="must be a dict"):
        queue.enqueue("not_a_dict", task_id)


def test_dequeue_fifo_order(queue, sample_validated_action):
    """Test dequeue returns tasks in FIFO order."""
    task_id1 = "a" * 64
    task_id2 = "b" * 64
    task_id3 = "c" * 64

    queue.enqueue(sample_validated_action, task_id1)
    queue.enqueue(sample_validated_action, task_id2)
    queue.enqueue(sample_validated_action, task_id3)

    entry1 = queue.dequeue()
    entry2 = queue.dequeue()
    entry3 = queue.dequeue()

    assert entry1["task_id"] == task_id1
    assert entry2["task_id"] == task_id2
    assert entry3["task_id"] == task_id3


def test_dequeue_empty_returns_none(queue):
    """Test dequeue on empty queue returns None."""
    result = queue.dequeue()

    assert result is None


def test_dequeue_removes_from_deduplication_set(queue, sample_validated_action):
    """Test dequeue removes task_id from deduplication set."""
    task_id = "a" * 64

    queue.enqueue(sample_validated_action, task_id)
    assert queue.contains(task_id) is True

    queue.dequeue()
    assert queue.contains(task_id) is False

    # Should be able to enqueue again
    result = queue.enqueue(sample_validated_action, task_id)
    assert result is True


def test_peek_does_not_dequeue(queue, sample_validated_action):
    """Test peek returns task without dequeuing."""
    task_id = "a" * 64

    queue.enqueue(sample_validated_action, task_id)

    entry1 = queue.peek()
    entry2 = queue.peek()

    assert entry1["task_id"] == task_id
    assert entry2["task_id"] == task_id
    assert queue.size() == 1


def test_peek_empty_returns_none(queue):
    """Test peek on empty queue returns None."""
    result = queue.peek()

    assert result is None


def test_requeue_task(queue, sample_validated_action):
    """Test re-enqueueing a task (for retry)."""
    task_id = "a" * 64

    # Enqueue, dequeue, then requeue
    queue.enqueue(sample_validated_action, task_id)
    queue.dequeue()

    result = queue.requeue(sample_validated_action, task_id)

    assert result is True
    assert queue.size() == 1


def test_clear_removes_all_tasks(queue, sample_validated_action):
    """Test clear removes all tasks."""
    task_id1 = "a" * 64
    task_id2 = "b" * 64

    queue.enqueue(sample_validated_action, task_id1)
    queue.enqueue(sample_validated_action, task_id2)

    count = queue.clear()

    assert count == 2
    assert queue.size() == 0
    assert queue.is_empty() is True


def test_get_metrics(queue, sample_validated_action):
    """Test queue metrics."""
    task_id1 = "a" * 64
    task_id2 = "b" * 64

    queue.enqueue(sample_validated_action, task_id1)
    queue.enqueue(sample_validated_action, task_id2)
    queue.dequeue()

    metrics = queue.get_metrics()

    assert metrics["current_size"] == 1
    assert metrics["total_enqueued"] == 2
    assert metrics["total_dequeued"] == 1
    assert metrics["in_flight"] == 0


def test_contains_check(queue, sample_validated_action):
    """Test contains check for task_id."""
    task_id1 = "a" * 64
    task_id2 = "b" * 64

    queue.enqueue(sample_validated_action, task_id1)

    assert queue.contains(task_id1) is True
    assert queue.contains(task_id2) is False


def test_task_entry_structure(queue, sample_validated_action):
    """Test task entry has expected structure."""
    task_id = "a" * 64

    queue.enqueue(sample_validated_action, task_id)
    entry = queue.dequeue()

    assert "task_id" in entry
    assert "validated_action" in entry
    assert "enqueued_at" in entry
    assert entry["task_id"] == task_id
    assert entry["validated_action"] == sample_validated_action
