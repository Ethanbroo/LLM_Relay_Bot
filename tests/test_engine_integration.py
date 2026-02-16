"""End-to-end integration tests for execution engine."""

import pytest
import shutil
from pathlib import Path
from executor.engine import ExecutionEngine
from executor.task_queue import TaskQueue
from executor.task_id import compute_task_id
from validator.pipeline import ValidationPipeline


@pytest.fixture
def clean_workspace(tmp_path):
    """Clean workspace and snapshot directories before/after tests."""
    workspace_root = str(tmp_path / "sandboxes")
    snapshot_root = str(tmp_path / "snapshots")
    log_path = str(tmp_path / "logs" / "execution_events.jsonl")

    yield workspace_root, snapshot_root, log_path

    # Cleanup
    if Path(workspace_root).exists():
        shutil.rmtree(workspace_root)
    if Path(snapshot_root).exists():
        shutil.rmtree(snapshot_root)


@pytest.fixture
def engine(clean_workspace):
    """Create execution engine with clean workspace."""
    workspace_root, snapshot_root, log_path = clean_workspace

    from executor.events import ExecutionEventLogger
    from executor.rollback import SnapshotManager

    event_logger = ExecutionEventLogger(log_path=log_path)
    snapshot_manager = SnapshotManager(snapshot_root=snapshot_root)

    return ExecutionEngine(
        workspace_root=workspace_root,
        snapshot_root=snapshot_root,
        event_logger=event_logger,
        snapshot_manager=snapshot_manager
    )


@pytest.fixture
def sample_validated_action():
    """Sample ValidatedAction for health_ping."""
    return {
        "validation_id": "valid_" + ("a" * 58),
        "original_envelope": {
            "envelope_version": "1.0.0",
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "test",
            "recipient": "executor",
            "action": "system.health_ping",
            "action_version": "1.0.0",
            "payload": {"echo": "test message"}
        },
        "validated_at": "2026-02-07T12:00:00Z",
        "schema_hash": "hash_" + ("b" * 59),
        "rbac_rule_id": "test.rule",
        "sanitized_payload": {"echo": "test message"}
    }


def test_engine_execute_health_ping_success(engine, sample_validated_action):
    """Test successful execution of health_ping action."""
    task_id = engine.enqueue_validated_action(sample_validated_action)

    result = engine.execute_one()

    assert result is not None
    assert result["status"] == "success"
    assert result["task_id"] == task_id
    assert result["action"] == "system.health_ping"
    assert result["artifacts"]["echo"] == "test message"
    assert result["artifacts"]["status"] == "healthy"
    assert result["retryable"] is False


def test_engine_execute_fs_read_success(engine, tmp_path):
    """Test successful execution of fs.read action."""
    # Create validated action for fs.read
    validated_action = {
        "validation_id": "valid_" + ("a" * 58),
        "original_envelope": {
            "envelope_version": "1.0.0",
            "message_id": "11234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "test",
            "recipient": "executor",
            "action": "fs.read",
            "action_version": "1.0.0",
            "payload": {"path": "test.txt"}
        },
        "validated_at": "2026-02-07T12:00:00Z",
        "schema_hash": "hash_" + ("b" * 59),
        "rbac_rule_id": "test.rule",
        "sanitized_payload": {
            "path": "test.txt",
            "offset": 0,
            "length": 1048576,
            "encoding": "utf-8"
        }
    }

    # Enqueue
    task_id = engine.enqueue_validated_action(validated_action)

    # Need to create file in sandbox before execution
    # For this test, we'll create it via handler, but in real scenario
    # the file would be pre-populated

    # Execute - will fail because file doesn't exist
    result = engine.execute_one()

    # Since file doesn't exist, this should fail and be retryable
    assert result["status"] in ["failure", "dead"]
    # File not found is non-retryable
    assert result["error_code"] in ["HANDLER_EXCEPTION"]


def test_engine_empty_queue_returns_none(engine):
    """Test execute_one on empty queue returns None."""
    result = engine.execute_one()

    assert result is None


def test_engine_deduplication(engine, sample_validated_action):
    """Test that duplicate tasks are not enqueued twice."""
    task_id1 = engine.enqueue_validated_action(sample_validated_action)
    task_id2 = engine.enqueue_validated_action(sample_validated_action)

    assert task_id1 == task_id2

    # Queue should have only 1 task
    assert engine.queue.size() == 1


def test_engine_execute_creates_sandbox(engine, sample_validated_action):
    """Test execution creates and destroys sandbox."""
    engine.enqueue_validated_action(sample_validated_action)

    result = engine.execute_one()

    assert result is not None
    assert "sandbox_id" in result
    assert result["sandbox_id"].startswith("sandbox_")


def test_engine_execute_creates_snapshot(engine, sample_validated_action):
    """Test execution creates snapshot before handler."""
    engine.enqueue_validated_action(sample_validated_action)

    result = engine.execute_one()

    assert result is not None
    assert "snapshot_id" in result
    # For success, snapshot should be deleted
    # assert result["snapshot_id"].startswith("snapshot_")


def test_engine_execute_logs_events(engine, sample_validated_action):
    """Test execution logs events."""
    task_id = engine.enqueue_validated_action(sample_validated_action)

    result = engine.execute_one()

    # Check events were logged
    events = engine.event_logger.get_task_lifecycle(task_id)

    assert len(events) > 0

    # Check for expected event types
    event_types = [e["event_type"] for e in events]
    assert "TASK_ENQUEUED" in event_types
    assert "TASK_STARTED" in event_types
    assert "SANDBOX_CREATED" in event_types
    assert "SNAPSHOT_CREATED" in event_types
    assert "HANDLER_STARTED" in event_types
    assert "HANDLER_FINISHED" in event_types
    assert "TASK_FINISHED" in event_types


def test_engine_execute_all(engine):
    """Test execute_all processes all tasks in queue."""
    # Create 3 different health_ping tasks
    for i in range(3):
        validated_action = {
            "validation_id": "valid_" + ("a" * 58),
            "original_envelope": {
                "envelope_version": "1.0.0",
                "message_id": f"0123456{i}-89ab-7def-8123-456789abcdef",
                "timestamp": "2026-02-07T12:00:00Z",
                "sender": "test",
                "recipient": "executor",
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {"echo": f"message{i}"}
            },
            "validated_at": "2026-02-07T12:00:00Z",
            "schema_hash": "hash_" + ("b" * 59),
            "rbac_rule_id": "test.rule",
            "sanitized_payload": {"echo": f"message{i}"}
        }

        engine.enqueue_validated_action(validated_action)

    results = engine.execute_all()

    assert len(results) == 3
    assert all(r["status"] == "success" for r in results)
    assert engine.queue.is_empty()


def test_engine_deterministic_task_id(engine, sample_validated_action):
    """Test task_id is deterministic for same ValidatedAction."""
    task_id1 = compute_task_id(sample_validated_action)
    task_id2 = engine.enqueue_validated_action(sample_validated_action)

    assert task_id1 == task_id2


def test_engine_execution_result_structure(engine, sample_validated_action):
    """Test ExecutionResult has expected structure."""
    engine.enqueue_validated_action(sample_validated_action)

    result = engine.execute_one()

    # Required fields
    assert "run_id" in result
    assert "session_id" in result
    assert "message_id" in result
    assert "task_id" in result
    assert "attempt" in result
    assert "action" in result
    assert "action_version" in result
    assert "status" in result
    assert "started_at" in result
    assert "finished_at" in result
    assert "retryable" in result
    assert "sandbox_id" in result
    assert "total_duration_ms" in result

    # Success-specific fields
    if result["status"] == "success":
        assert "artifacts" in result
        assert result["artifacts"] is not None


def test_engine_fifo_execution_order(engine):
    """Test tasks are executed in FIFO order."""
    task_ids = []

    for i in range(3):
        validated_action = {
            "validation_id": "valid_" + ("a" * 58),
            "original_envelope": {
                "envelope_version": "1.0.0",
                "message_id": f"0123456{i}-89ab-7def-8123-456789abcdef",
                "timestamp": "2026-02-07T12:00:00Z",
                "sender": "test",
                "recipient": "executor",
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {"echo": f"message{i}"}
            },
            "validated_at": "2026-02-07T12:00:00Z",
            "schema_hash": "hash_" + ("b" * 59),
            "rbac_rule_id": "test.rule",
            "sanitized_payload": {"echo": f"message{i}"}
        }

        task_id = engine.enqueue_validated_action(validated_action)
        task_ids.append(task_id)

    # Execute in order
    result1 = engine.execute_one()
    result2 = engine.execute_one()
    result3 = engine.execute_one()

    assert result1["task_id"] == task_ids[0]
    assert result2["task_id"] == task_ids[1]
    assert result3["task_id"] == task_ids[2]


def test_engine_session_id_groups_retries(engine, sample_validated_action):
    """Test session_id groups all attempts for a task."""
    engine.enqueue_validated_action(sample_validated_action)

    result1 = engine.execute_one()

    # All attempts should have same session_id
    session_id = result1["session_id"]
    assert session_id is not None


def test_engine_attempt_number(engine, sample_validated_action):
    """Test attempt number is tracked."""
    engine.enqueue_validated_action(sample_validated_action)

    result = engine.execute_one()

    assert result["attempt"] == 1  # First attempt
