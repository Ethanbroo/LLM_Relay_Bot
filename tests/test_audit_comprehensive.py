"""Comprehensive tests for audit logging."""

import pytest
from pathlib import Path
from validator.audit import AuditLogger


@pytest.fixture
def audit_logger(tmp_path):
    """Create audit logger with temp file."""
    audit_file = tmp_path / "test_audit.jsonl"
    return AuditLogger(audit_path=str(audit_file))


def test_audit_log_validation_started(audit_logger):
    """Test logging validation started event."""
    event_id = audit_logger.log_validation_started(
        stage="envelope_validation",
        message_id="test-123",
        principal="validator",
        action="fs.read"
    )

    assert event_id is not None

    events = audit_logger.read_events()
    assert len(events) > 0
    assert events[0]["event_type"] == "validation_started"
    assert events[0]["stage"] == "envelope_validation"
    assert events[0]["result"] == "pass"


def test_audit_log_validation_passed(audit_logger):
    """Test logging validation passed event."""
    event_id = audit_logger.log_validation_passed(
        stage="schema_selection",
        message_id="test-123",
        action="fs.read",
        details={"schema_hash": "abc123"}
    )

    assert event_id is not None

    events = audit_logger.read_events()
    assert events[0]["event_type"] == "validation_passed"
    assert events[0]["details"]["schema_hash"] == "abc123"


def test_audit_log_validation_failed(audit_logger):
    """Test logging validation failed event."""
    event_id = audit_logger.log_validation_failed(
        stage="rbac_check",
        error_code="RBAC_DENIED",
        error_message="Access denied",
        message_id="test-123",
        principal="validator",
        action="fs.read",
        details={"reason": "Not authorized"}
    )

    assert event_id is not None

    events = audit_logger.read_events()
    assert events[0]["event_type"] == "validation_failed"
    assert events[0]["result"] == "fail"
    assert events[0]["error_code"] == "RBAC_DENIED"
    assert events[0]["error_message"] == "Access denied"


def test_audit_read_events_empty(audit_logger):
    """Test reading events from empty log."""
    events = audit_logger.read_events()
    assert events == []


def test_audit_read_events_with_limit(audit_logger):
    """Test reading events with limit."""
    # Log multiple events
    for i in range(10):
        audit_logger.log_validation_started(
            stage="test_stage",
            message_id=f"msg-{i}"
        )

    # Read only 5 most recent
    events = audit_logger.read_events(limit=5)

    assert len(events) == 5
    # Should be most recent first (reversed)
    assert events[0]["message_id"] == "msg-9"
    assert events[4]["message_id"] == "msg-5"


def test_audit_read_events_no_limit(audit_logger):
    """Test reading all events without limit."""
    # Log multiple events
    for i in range(5):
        audit_logger.log_validation_started(
            stage="test_stage",
            message_id=f"msg-{i}"
        )

    events = audit_logger.read_events()

    assert len(events) == 5


def test_audit_event_has_timestamp(audit_logger):
    """Test that audit events include timestamp."""
    audit_logger.log_validation_started(stage="test")

    events = audit_logger.read_events()
    assert "timestamp" in events[0]
    assert events[0]["timestamp"].endswith("Z")


def test_audit_event_has_signature_field_null(audit_logger):
    """Test that audit events have signature field (null in Phase 1)."""
    audit_logger.log_validation_started(stage="test")

    events = audit_logger.read_events()
    assert "signature" in events[0]
    assert events[0]["signature"] is None


def test_audit_log_minimal_event(audit_logger):
    """Test logging event with minimal fields."""
    event_id = audit_logger.log_validation_started(stage="test")

    events = audit_logger.read_events()
    assert events[0]["stage"] == "test"
    assert "message_id" not in events[0]  # Optional field not present


def test_audit_log_with_all_optional_fields(audit_logger):
    """Test logging event with all optional fields."""
    event_id = audit_logger.log_validation_passed(
        stage="sanitization",
        message_id="test-123",
        principal="validator",
        action="fs.read",
        details={"key": "value"}
    )

    events = audit_logger.read_events()
    event = events[0]

    assert event["message_id"] == "test-123"
    assert event["principal"] == "validator"
    assert event["action"] == "fs.read"
    assert event["details"]["key"] == "value"


def test_audit_append_to_existing_log(tmp_path):
    """Test that events are appended to existing log file."""
    audit_file = tmp_path / "test_audit.jsonl"

    # First logger writes events
    logger1 = AuditLogger(audit_path=str(audit_file))
    logger1.log_validation_started(stage="stage1")
    logger1.log_validation_passed(stage="stage2")

    # Second logger appends to same file
    logger2 = AuditLogger(audit_path=str(audit_file))
    logger2.log_validation_started(stage="stage3")

    # Read all events
    events = logger2.read_events()
    assert len(events) == 3
    # Most recent first
    assert events[0]["stage"] == "stage3"
    assert events[1]["stage"] == "stage2"
    assert events[2]["stage"] == "stage1"


def test_audit_creates_parent_directory(tmp_path):
    """Test that audit logger creates parent directory if needed."""
    nested_path = tmp_path / "deep" / "nested" / "audit.jsonl"

    # Parent directories don't exist yet
    assert not nested_path.parent.exists()

    # Logger should create them
    logger = AuditLogger(audit_path=str(nested_path))
    logger.log_validation_started(stage="test")

    assert nested_path.exists()


def test_audit_event_id_is_unique(audit_logger):
    """Test that each event gets unique ID."""
    id1 = audit_logger.log_validation_started(stage="test1")
    id2 = audit_logger.log_validation_started(stage="test2")
    id3 = audit_logger.log_validation_passed(stage="test3")

    assert id1 != id2
    assert id2 != id3
    assert id1 != id3


def test_audit_jsonl_format(audit_logger):
    """Test that audit log uses proper JSONL format."""
    audit_logger.log_validation_started(stage="test1")
    audit_logger.log_validation_passed(stage="test2")

    # Read raw file
    with open(audit_logger.audit_path, 'r') as f:
        lines = f.readlines()

    # Should have 2 lines
    assert len(lines) == 2

    # Each line should be valid JSON ending with newline
    import json
    for line in lines:
        assert line.endswith('\n')
        obj = json.loads(line.strip())
        assert isinstance(obj, dict)


def test_audit_read_events_reversed_order(audit_logger):
    """Test that read_events returns most recent first."""
    # Log events in order
    audit_logger.log_validation_started(stage="first", message_id="msg-1")
    audit_logger.log_validation_started(stage="second", message_id="msg-2")
    audit_logger.log_validation_started(stage="third", message_id="msg-3")

    events = audit_logger.read_events()

    # Should be reversed (most recent first)
    assert events[0]["message_id"] == "msg-3"
    assert events[1]["message_id"] == "msg-2"
    assert events[2]["message_id"] == "msg-1"


def test_audit_log_without_details(audit_logger):
    """Test logging failed event without details."""
    event_id = audit_logger.log_validation_failed(
        stage="test",
        error_code="TEST_ERROR",
        error_message="Test error"
    )

    events = audit_logger.read_events()
    event = events[0]

    assert "details" not in event
    assert event["error_code"] == "TEST_ERROR"


def test_audit_log_with_empty_details(audit_logger):
    """Test logging with empty details dict."""
    event_id = audit_logger.log_validation_failed(
        stage="test",
        error_code="TEST_ERROR",
        error_message="Test error",
        details={}
    )

    events = audit_logger.read_events()
    # Empty details should not be included
    assert "details" not in events[0]
