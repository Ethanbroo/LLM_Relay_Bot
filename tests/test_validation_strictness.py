"""
Tests for validation strictness.

These tests verify that the validator rejects:
- Unknown fields
- Type coercion
- Schema drift
- Dangerous inputs
"""

import pytest
from validator.pipeline import ValidationPipeline


@pytest.fixture
def pipeline():
    """Create validation pipeline fixture."""
    return ValidationPipeline(base_dir=".")


@pytest.fixture
def valid_envelope():
    """Create a valid envelope for testing."""
    return {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {
            "path": "test.txt",
            "offset": 0,
            "length": 1024,
            "encoding": "utf-8"
        }
    }


def test_reject_unknown_envelope_field(pipeline, valid_envelope):
    """Test that unknown fields in envelope are rejected."""
    envelope = valid_envelope.copy()
    envelope["unknown_field"] = "should_be_rejected"

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert result["error_code"] == "ENVELOPE_INVALID"


def test_reject_unknown_payload_field(pipeline, valid_envelope):
    """Test that unknown fields in payload are rejected."""
    envelope = valid_envelope.copy()
    envelope["payload"] = {
        "path": "test.txt",
        "unknown_field": "should_be_rejected"
    }

    result = pipeline.validate(envelope)

    assert "error_id" in result
    # Could fail at JSON Schema or Pydantic stage
    assert result["error_code"] in ["JSON_SCHEMA_FAILED", "PYDANTIC_FAILED"]


def test_reject_missing_required_field(pipeline, valid_envelope):
    """Test that missing required fields are rejected."""
    envelope = valid_envelope.copy()
    del envelope["sender"]

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert result["error_code"] == "ENVELOPE_INVALID"


def test_reject_wrong_type(pipeline, valid_envelope):
    """Test that wrong types are rejected (no coercion)."""
    envelope = valid_envelope.copy()
    envelope["payload"]["offset"] = "should_be_number"

    result = pipeline.validate(envelope)

    assert "error_id" in result


def test_reject_absolute_path(pipeline, valid_envelope):
    """Test that absolute paths are rejected."""
    envelope = valid_envelope.copy()
    envelope["payload"]["path"] = "/etc/passwd"

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert result["error_code"] == "PYDANTIC_FAILED"


def test_reject_parent_directory_traversal(pipeline, valid_envelope):
    """Test that .. in paths is rejected."""
    envelope = valid_envelope.copy()
    envelope["payload"]["path"] = "../../../etc/passwd"

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert result["error_code"] == "PYDANTIC_FAILED"


def test_reject_null_byte_in_path(pipeline, valid_envelope):
    """Test that null bytes in paths are rejected."""
    envelope = valid_envelope.copy()
    envelope["payload"]["path"] = "test\x00.txt"

    result = pipeline.validate(envelope)

    assert "error_id" in result


def test_valid_envelope_passes(pipeline, valid_envelope):
    """Test that a valid envelope passes all checks."""
    result = pipeline.validate(valid_envelope)

    assert "validation_id" in result  # ValidatedAction
    assert "sanitized_payload" in result
    assert result["sanitized_payload"]["path"] == "test.txt"


def test_deterministic_output(pipeline, valid_envelope):
    """Test that same input produces consistent output."""
    result1 = pipeline.validate(valid_envelope)
    result2 = pipeline.validate(valid_envelope)

    # Schema hash and rule ID should be identical
    assert result1["schema_hash"] == result2["schema_hash"]
    assert result1["rbac_rule_id"] == result2["rbac_rule_id"]


def test_reject_oversized_payload_field(pipeline, valid_envelope):
    """Test that fields exceeding max length are rejected."""
    envelope = valid_envelope.copy()
    envelope["payload"]["path"] = "a" * 2000  # Exceeds maxLength: 1024

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert result["error_code"] in ["JSON_SCHEMA_FAILED", "PYDANTIC_FAILED"]
