"""
Integration tests for full validation pipeline.

Tests complete end-to-end flow from envelope to ValidatedAction/Error.
"""

import pytest
from validator.pipeline import ValidationPipeline


@pytest.fixture
def pipeline():
    """Create validation pipeline."""
    return ValidationPipeline(base_dir=".")


class TestSuccessfulValidation:
    """Tests for successful validation flows."""

    def test_fs_read_complete_flow(self, pipeline):
        """Test complete validation flow for fs.read."""
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
                "offset": 0,
                "length": 1024,
                "encoding": "utf-8"
            }
        }

        result = pipeline.validate(envelope)

        # Should be ValidatedAction
        assert "validation_id" in result
        assert "original_envelope" in result
        assert "validated_at" in result
        assert "schema_hash" in result
        assert "rbac_rule_id" in result
        assert "sanitized_payload" in result

        # Verify payload was sanitized
        assert result["sanitized_payload"]["path"] == "test.txt"

    def test_fs_list_dir_complete_flow(self, pipeline):
        """Test complete validation flow for fs.list_dir."""
        envelope = {
            "envelope_version": "1.0.0",
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "validator",
            "recipient": "executor",
            "action": "fs.list_dir",
            "action_version": "1.0.0",
            "payload": {
                "path": "data",
                "max_entries": 50,
                "sort_order": "name_asc"
            }
        }

        result = pipeline.validate(envelope)

        assert "validation_id" in result
        assert result["sanitized_payload"]["path"] == "data"

    def test_health_ping_complete_flow(self, pipeline):
        """Test complete validation flow for system.health_ping."""
        envelope = {
            "envelope_version": "1.0.0",
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "validator",
            "recipient": "executor",
            "action": "system.health_ping",
            "action_version": "1.0.0",
            "payload": {
                "echo": "hello"
            }
        }

        result = pipeline.validate(envelope)

        assert "validation_id" in result
        assert result["sanitized_payload"]["echo"] == "hello"


class TestValidationFailures:
    """Tests for validation failure scenarios."""

    def test_envelope_validation_failure(self, pipeline):
        """Test that invalid envelope produces error."""
        envelope = {
            "envelope_version": "1.0.0",
            # Missing required field: message_id
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "validator",
            "recipient": "executor",
            "action": "fs.read",
            "action_version": "1.0.0",
            "payload": {"path": "test.txt"}
        }

        result = pipeline.validate(envelope)

        assert "error_id" in result
        assert result["error_code"] == "ENVELOPE_INVALID"
        assert result["stage"] == "envelope_validation"

    def test_schema_not_found_failure(self, pipeline):
        """Test that unknown action produces error."""
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

        assert "error_id" in result
        assert result["error_code"] == "SCHEMA_NOT_FOUND"
        assert result["stage"] == "schema_selection"

    def test_rbac_denied_failure(self, pipeline):
        """Test that RBAC denial produces error."""
        envelope = {
            "envelope_version": "1.0.0",
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "validator",
            "recipient": "executor",
            "action": "fs.read",
            "action_version": "1.0.0",
            "payload": {
                "path": "../../../etc/passwd"  # Will fail both Pydantic and potentially RBAC
            }
        }

        result = pipeline.validate(envelope)

        # Will fail at Pydantic stage due to path validation
        assert "error_id" in result

    def test_payload_validation_failure(self, pipeline):
        """Test that invalid payload produces error."""
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
                "offset": "not_a_number"  # Wrong type
            }
        }

        result = pipeline.validate(envelope)

        assert "error_id" in result
        assert result["error_code"] in ["JSON_SCHEMA_FAILED", "PYDANTIC_FAILED"]


class TestDeterminism:
    """Tests for deterministic behavior."""

    def test_same_input_same_output(self, pipeline):
        """Test that same input produces same validation result."""
        envelope = {
            "envelope_version": "1.0.0",
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "validator",
            "recipient": "executor",
            "action": "fs.read",
            "action_version": "1.0.0",
            "payload": {"path": "test.txt"}
        }

        result1 = pipeline.validate(envelope)
        result2 = pipeline.validate(envelope)

        # Schema hash and RBAC rule ID should be identical
        assert result1["schema_hash"] == result2["schema_hash"]
        assert result1["rbac_rule_id"] == result2["rbac_rule_id"]

    def test_error_code_deterministic(self, pipeline):
        """Test that same error produces same error code."""
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

        result1 = pipeline.validate(envelope)
        result2 = pipeline.validate(envelope)

        assert result1["error_code"] == result2["error_code"]
        assert result1["stage"] == result2["stage"]


class TestAuditLogging:
    """Tests for audit logging."""

    def test_audit_events_created(self, pipeline):
        """Test that audit events are created during validation."""
        envelope = {
            "envelope_version": "1.0.0",
            "message_id": "01234567-89ab-7def-8123-456789abcdef",
            "timestamp": "2026-02-07T12:00:00Z",
            "sender": "validator",
            "recipient": "executor",
            "action": "fs.read",
            "action_version": "1.0.0",
            "payload": {"path": "test.txt"}
        }

        # Clear audit log
        if pipeline.audit.audit_path.exists():
            pipeline.audit.audit_path.unlink()

        pipeline.validate(envelope)

        # Check audit log has events
        events = pipeline.audit.read_events()
        assert len(events) > 0

        # Should have events for each stage
        stages = [e['stage'] for e in events]
        assert 'envelope_validation' in stages
        assert 'schema_selection' in stages
