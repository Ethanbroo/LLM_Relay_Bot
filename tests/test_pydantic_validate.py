"""Tests for Pydantic validation module."""

import pytest
from validator.pydantic_validate import (
    validate_envelope,
    validate_payload,
    get_supported_actions,
    PydanticValidationError,
)


def test_validate_envelope_success():
    """Test successful envelope validation."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {}
    }

    result = validate_envelope(envelope)

    assert result.action == "fs.read"
    assert result.sender == "validator"


def test_validate_envelope_invalid_uuid():
    """Test envelope validation with invalid UUID."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "not-a-valid-uuid",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {}
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_envelope(envelope)

    assert "message_id" in str(exc_info.value.details)


def test_validate_envelope_invalid_timestamp():
    """Test envelope validation with invalid timestamp."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "not-a-timestamp",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {}
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_envelope(envelope)

    assert "timestamp" in str(exc_info.value.details)


def test_validate_envelope_missing_field():
    """Test envelope validation with missing required field."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        # Missing recipient
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {}
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_envelope(envelope)

    assert len(exc_info.value.details) > 0


def test_validate_envelope_extra_field():
    """Test envelope validation rejects extra fields."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {},
        "extra": "not_allowed"
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_envelope(envelope)

    assert "extra" in str(exc_info.value.details).lower()


def test_validate_payload_fs_read_success():
    """Test successful fs.read payload validation."""
    payload = {
        "path": "data/test.txt",
        "offset": 0,
        "length": 1024,
        "encoding": "utf-8"
    }

    result = validate_payload(payload, "fs.read")

    assert result.path == "data/test.txt"


def test_validate_payload_fs_list_dir_success():
    """Test successful fs.list_dir payload validation."""
    payload = {
        "path": "data",
        "max_entries": 100,
        "sort_order": "name_asc"
    }

    result = validate_payload(payload, "fs.list_dir")

    assert result.path == "data"


def test_validate_payload_health_ping_success():
    """Test successful health_ping payload validation."""
    payload = {
        "echo": "hello"
    }

    result = validate_payload(payload, "system.health_ping")

    assert result.echo == "hello"


def test_validate_payload_health_ping_empty():
    """Test health_ping with empty payload."""
    payload = {}

    result = validate_payload(payload, "system.health_ping")

    assert result.echo is None


def test_validate_payload_unknown_action():
    """Test payload validation with unknown action."""
    payload = {"field": "value"}

    with pytest.raises(PydanticValidationError, match="Unknown action") as exc_info:
        validate_payload(payload, "unknown.action")

    assert "unknown.action" in str(exc_info.value)
    assert "details" in dir(exc_info.value)


def test_validate_payload_validation_error():
    """Test payload validation with validation error."""
    payload = {
        "path": "/absolute/path"  # Absolute path not allowed
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_payload(payload, "fs.read")

    assert len(exc_info.value.details) > 0
    assert "path" in str(exc_info.value.details)


def test_validate_payload_type_error():
    """Test payload validation with wrong type."""
    payload = {
        "path": "test.txt",
        "offset": "not_a_number"  # Should be integer
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_payload(payload, "fs.read")

    assert len(exc_info.value.details) > 0


def test_validate_payload_extra_field():
    """Test payload validation rejects extra fields."""
    payload = {
        "path": "test.txt",
        "unknown_field": "value"
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_payload(payload, "fs.read")

    # Check error details mention the extra field
    error_str = str(exc_info.value.details)
    assert "extra" in error_str.lower() or "unknown_field" in error_str.lower()


def test_get_supported_actions():
    """Test getting list of supported actions."""
    actions = get_supported_actions()

    assert "fs.read" in actions
    assert "fs.list_dir" in actions
    assert "system.health_ping" in actions
    assert isinstance(actions, list)


def test_pydantic_validation_error_has_details():
    """Test that PydanticValidationError includes details."""
    payload = {
        "path": 123  # Wrong type
    }

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_payload(payload, "fs.read")

    assert hasattr(exc_info.value, 'details')
    assert isinstance(exc_info.value.details, list)
    assert len(exc_info.value.details) > 0


def test_pydantic_validation_error_message_format():
    """Test that PydanticValidationError has proper message format."""
    payload = {}  # Missing required field

    with pytest.raises(PydanticValidationError) as exc_info:
        validate_payload(payload, "fs.read")

    error_msg = str(exc_info.value)
    assert "fs.read" in error_msg
    assert "Payload validation failed" in error_msg
