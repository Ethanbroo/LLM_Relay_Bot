"""Tests for task_id generation."""

import pytest
from executor.task_id import (
    compute_task_id,
    validate_task_id,
    compute_session_id_deterministic
)


def test_compute_task_id_deterministic():
    """Test that same ValidatedAction produces same task_id."""
    validated_action = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {
            "path": "test.txt",
            "offset": 0,
            "length": 1024,
            "encoding": "utf-8"
        }
    }

    task_id1 = compute_task_id(validated_action)
    task_id2 = compute_task_id(validated_action)

    assert task_id1 == task_id2
    assert len(task_id1) == 64
    assert isinstance(task_id1, str)


def test_compute_task_id_different_message_id():
    """Test that different message_id produces different task_id."""
    validated_action1 = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test.txt"}
    }

    validated_action2 = {
        "original_envelope": {
            "message_id": "11234567-89ab-7def-8123-456789abcdef",  # Different
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test.txt"}
    }

    task_id1 = compute_task_id(validated_action1)
    task_id2 = compute_task_id(validated_action2)

    assert task_id1 != task_id2


def test_compute_task_id_different_payload():
    """Test that different payload produces different task_id."""
    validated_action1 = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test1.txt"}
    }

    validated_action2 = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test2.txt"}  # Different
    }

    task_id1 = compute_task_id(validated_action1)
    task_id2 = compute_task_id(validated_action2)

    assert task_id1 != task_id2


def test_compute_task_id_different_action():
    """Test that different action produces different task_id."""
    validated_action1 = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.read",
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test.txt"}
    }

    validated_action2 = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "action": "fs.list_dir",  # Different
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test.txt"}
    }

    task_id1 = compute_task_id(validated_action1)
    task_id2 = compute_task_id(validated_action2)

    assert task_id1 != task_id2


def test_compute_task_id_missing_field():
    """Test that missing field raises ValueError."""
    validated_action = {
        "original_envelope": {
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            # Missing action
            "action_version": "1.0.0"
        },
        "sanitized_payload": {"path": "test.txt"}
    }

    with pytest.raises(ValueError, match="missing required field"):
        compute_task_id(validated_action)


def test_validate_task_id_valid():
    """Test validation of valid task_id."""
    task_id = "a" * 64  # 64-char hex string

    assert validate_task_id(task_id) is True


def test_validate_task_id_invalid_length():
    """Test validation rejects wrong length."""
    assert validate_task_id("a" * 63) is False
    assert validate_task_id("a" * 65) is False


def test_validate_task_id_invalid_chars():
    """Test validation rejects non-hex chars."""
    task_id = "g" * 64  # 'g' is not valid hex

    assert validate_task_id(task_id) is False


def test_validate_task_id_not_string():
    """Test validation rejects non-string."""
    assert validate_task_id(123) is False
    assert validate_task_id(None) is False


def test_compute_session_id_deterministic():
    """Test session_id computation is deterministic."""
    task_id = "a" * 64
    attempt = 1

    session_id1 = compute_session_id_deterministic(task_id, attempt)
    session_id2 = compute_session_id_deterministic(task_id, attempt)

    assert session_id1 == session_id2
    assert len(session_id1) == 64


def test_compute_session_id_different_attempts():
    """Test different attempts produce different session_ids."""
    task_id = "a" * 64

    session_id1 = compute_session_id_deterministic(task_id, 1)
    session_id2 = compute_session_id_deterministic(task_id, 2)

    assert session_id1 != session_id2
