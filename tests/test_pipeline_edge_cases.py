"""Edge case tests for validation pipeline."""

import pytest
from validator.pipeline import ValidationPipeline
from validator.schema_registry import SchemaVersionNotAllowedError


@pytest.fixture
def pipeline():
    """Create validation pipeline."""
    return ValidationPipeline(base_dir=".")


def test_pipeline_handles_schema_version_not_allowed(pipeline):
    """Test pipeline handles schema version not allowed error."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "99.99.99",  # Version not in allowed list
        "payload": {"path": "test.txt"}
    }

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert result["error_code"] == "SCHEMA_NOT_FOUND"
    assert result["stage"] == "schema_selection"


def test_pipeline_error_includes_original_envelope(pipeline):
    """Test that error output includes original envelope."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {
            "path": "test.txt",
            "unknown_field": "value"
        }
    }

    result = pipeline.validate(envelope)

    assert "error_id" in result
    assert "original_envelope" in result
    assert result["original_envelope"]["message_id"] == envelope["message_id"]


def test_pipeline_extract_resource_for_filesystem_actions(pipeline):
    """Test resource extraction for filesystem actions."""
    # fs.read
    resource = pipeline._extract_resource("fs.read", {"path": "data/file.txt"})
    assert resource == "/workspace/data/file.txt"

    # fs.list_dir
    resource = pipeline._extract_resource("fs.list_dir", {"path": "data"})
    assert resource == "/workspace/data"


def test_pipeline_extract_resource_for_non_filesystem(pipeline):
    """Test resource extraction for non-filesystem actions."""
    resource = pipeline._extract_resource("system.health_ping", {})
    assert resource == "*"


def test_pipeline_validates_at_timestamp_format(pipeline):
    """Test that validated_at timestamp is properly formatted."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {}
    }

    result = pipeline.validate(envelope)

    assert "validated_at" in result
    # Should be ISO 8601 format with Z
    assert result["validated_at"].endswith("Z")


def test_pipeline_error_occurred_at_timestamp(pipeline):
    """Test that error occurred_at timestamp is properly formatted."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "unknown_principal",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {"path": "test.txt"}
    }

    result = pipeline.validate(envelope)

    assert "occurred_at" in result
    assert result["occurred_at"].endswith("Z")


def test_pipeline_validation_id_is_uuid(pipeline):
    """Test that validation_id is a valid UUID format."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {}
    }

    result = pipeline.validate(envelope)

    # Should be UUID format (8-4-4-4-12 hex digits)
    import re
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    assert re.match(uuid_pattern, result["validation_id"], re.IGNORECASE)


def test_pipeline_error_id_is_uuid(pipeline):
    """Test that error_id is a valid UUID format."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "unknown.action",
        "action_version": "1.0.0",
        "payload": {}
    }

    result = pipeline.validate(envelope)

    import re
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    assert re.match(uuid_pattern, result["error_id"], re.IGNORECASE)


def test_pipeline_unparseable_envelope_error(pipeline):
    """Test pipeline handles envelope that can't be parsed."""
    # Envelope missing critical fields
    envelope = {"random": "data"}

    result = pipeline.validate(envelope)

    assert "error_id" in result
    # Should fail at envelope validation stage
    assert result["stage"] == "envelope_validation"


def test_pipeline_preserves_original_envelope_on_success(pipeline):
    """Test that original envelope is preserved in ValidatedAction."""
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {"echo": "test"}
    }

    result = pipeline.validate(envelope)

    assert "original_envelope" in result
    assert result["original_envelope"] == envelope


def test_pipeline_different_actions_different_schemas(pipeline):
    """Test that different actions use different schemas."""
    envelope1 = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {"path": "test.txt"}
    }

    envelope2 = {
        "envelope_version": "1.0.0",
        "message_id": "12345678-90ab-7cde-8901-23456789abcd",
        "timestamp": "2026-02-07T12:01:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.list_dir",
        "action_version": "1.0.0",
        "payload": {"path": "data"}
    }

    result1 = pipeline.validate(envelope1)
    result2 = pipeline.validate(envelope2)

    # Different actions should have different schema hashes
    assert result1["schema_hash"] != result2["schema_hash"]
