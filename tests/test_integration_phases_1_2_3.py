"""Integration tests for Phases 1-3.

Tests the complete flow:
- Phase 1: Validation pipeline
- Phase 2: Execution engine
- Phase 3: Audit logging with Ed25519 signatures
"""

import pytest
import tempfile
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from supervisor import LLMRelaySupervisor, SupervisorError
from audit_logging.verifier import AuditLogVerifier
from audit_logging.key_manager import KeyManager


def generate_uuid_v7_like():
    """Generate a UUID v7-like string (fake but valid format for testing)."""
    # Get a random UUID v4
    base = uuid.uuid4()
    # Convert to string and replace version digit (3rd group, 1st char) with '7'
    uuid_str = str(base)
    parts = uuid_str.split('-')
    parts[2] = '7' + parts[2][1:]  # Change version to 7
    return '-'.join(parts)


@pytest.fixture
def temp_workspace():
    """Create temporary workspace for integration tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Create directory structure
        (workspace / "config").mkdir()
        (workspace / "keys").mkdir()
        (workspace / "logs").mkdir()
        (workspace / "schemas" / "actions" / "system.health_ping").mkdir(parents=True)

        # Generate audit test keys
        audit_private_key = Ed25519PrivateKey.generate()
        audit_public_key = audit_private_key.public_key()

        audit_private_pem = audit_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        audit_private_path = workspace / "keys" / "audit_private.pem"
        audit_private_path.write_bytes(audit_private_pem)
        audit_private_path.chmod(0o600)

        audit_public_pem = audit_public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        audit_public_path = workspace / "keys" / "audit_public.pem"
        audit_public_path.write_bytes(audit_public_pem)

        # Generate approval test keys (Phase 4)
        approval_private_key = Ed25519PrivateKey.generate()
        approval_public_key = approval_private_key.public_key()

        approval_private_pem = approval_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        approval_private_path = workspace / "keys" / "approval_private.pem"
        approval_private_path.write_bytes(approval_private_pem)
        approval_private_path.chmod(0o600)

        approval_public_pem = approval_public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        approval_public_path = workspace / "keys" / "approval_public.pem"
        approval_public_path.write_bytes(approval_public_pem)

        # Create minimal core.yaml
        core_yaml = """
system:
  python_version: "3.12"

time_policy:
  mode: "frozen"

audit:
  format: "jsonl"
  sink: "logdaemon"
  log_directory: "logs"
  ed25519_private_key_path: "keys/audit_private.pem"
  ed25519_public_key_path: "keys/audit_public.pem"
  max_segment_bytes: 10485760
  fsync_every_n_events: 100
  ingress_buffer_max_events: 1000
  signature_required: true

coordination:
  lock_ttl_events: 1000
  deadlock_detection_enabled: true
  approval_enabled: true
  approval_public_key_path: "keys/approval_public.pem"
  default_approval_ttl_events: 10000
"""
        (workspace / "config" / "core.yaml").write_text(core_yaml)

        # Create minimal policy.yaml
        policy_yaml = """
version: "1.0.0"

roles:
  executor:
    allow:
      - action: "system.health_ping"
        resource: "*"
        rule_id: "executor.system.health_ping.any"

principals:
  validator:
    roles: ["executor"]
"""
        (workspace / "config" / "policy.yaml").write_text(policy_yaml)

        # Create schema registry
        registry = {
            "registry_version": "1.0.0",
            "default_version_policy": "explicit_only",
            "actions": {
                "system.health_ping": {
                    "default": "1.0.0",
                    "allowed": ["1.0.0"],
                    "schema_path": "schemas/actions/system.health_ping/1.0.0.schema.json"
                }
            }
        }
        import json
        (workspace / "config" / "schema_registry_index.json").write_text(
            json.dumps(registry, indent=2)
        )

        # Create action schema
        action_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/system.health_ping.schema.json",
            "title": "system.health_ping",
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": []
        }
        (workspace / "schemas" / "actions" / "system.health_ping" / "1.0.0.schema.json").write_text(
            json.dumps(action_schema, indent=2)
        )

        # Create envelope schema
        envelope_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/envelope.schema.json",
            "title": "Envelope",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "envelope_version": {"type": "string"},
                "message_id": {"type": "string"},
                "sender": {"type": "string"},
                "recipient": {"type": "string"},
                "timestamp": {"type": "string"},
                "action": {"type": "string"},
                "action_version": {"type": "string"},
                "payload": {"type": "object"}
            },
            "required": [
                "envelope_version",
                "message_id",
                "sender",
                "recipient",
                "timestamp",
                "action",
                "action_version",
                "payload"
            ]
        }
        (workspace / "schemas" / "envelope.schema.json").write_text(
            json.dumps(envelope_schema, indent=2)
        )

        yield workspace


class TestPhases123Integration:
    """Integration tests for Phases 1-3."""

    def test_supervisor_initialization(self, temp_workspace):
        """Supervisor initializes successfully with all phases."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            assert supervisor.log_daemon is not None
            assert supervisor.validator is not None
            assert supervisor.executor is not None
            assert supervisor.config_hash is not None
            assert supervisor.run_id is not None

    def test_validate_and_enqueue(self, temp_workspace):
        """Envelope validates and enqueues for execution."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            envelope = {
                "envelope_version": "1.0.0",
                "message_id": generate_uuid_v7_like(),
                "sender": "validator",
                "recipient": "executor",
                "timestamp": "2026-02-08T10:00:00Z",
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {}
            }

            result = supervisor.process_envelope(envelope)

            # Should be ValidatedAction
            assert "validation_id" in result
            assert "task_id" in result
            assert result["schema_hash"] is not None

    def test_end_to_end_execution(self, temp_workspace):
        """Complete flow: validate → enqueue → execute."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            envelope = {
                "envelope_version": "1.0.0",
                "message_id": generate_uuid_v7_like(),
                "sender": "validator",
                "recipient": "executor",
                "timestamp": "2026-02-08T10:00:00Z",
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {}
            }

            # Validate and enqueue
            validated = supervisor.process_envelope(envelope)
            assert "validation_id" in validated
            assert "task_id" in validated

            # Execute
            execution_results = supervisor.execute_pending_tasks()
            assert len(execution_results) == 1

            exec_result = execution_results[0]
            assert exec_result["status"] == "success"
            assert exec_result["task_id"] == validated["task_id"]

    def test_audit_log_integrity(self, temp_workspace):
        """Audit log has valid signatures and hash chain."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            envelope = {
                "envelope_version": "1.0.0",
                "message_id": generate_uuid_v7_like(),
                "sender": "validator",
                "recipient": "executor",
                "timestamp": "2026-02-08T10:00:00Z",
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {}
            }

            # Process envelope
            validated = supervisor.process_envelope(envelope)
            execution_results = supervisor.execute_pending_tasks()

        # Verify audit log integrity
        key_manager = KeyManager(
            private_key_path=str(temp_workspace / "keys" / "audit_private.pem"),
            public_key_path=str(temp_workspace / "keys" / "audit_public.pem")
        )
        verifier = AuditLogVerifier(key_manager)

        segment_path = temp_workspace / "logs" / "audit.000001.jsonl"
        result = verifier.verify_segment(segment_path)

        assert result.success is True
        assert result.events_verified > 0
        assert result.tamper_detected is False

    def test_validation_failure_logged(self, temp_workspace):
        """Validation failures are logged to audit."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            # Invalid envelope (missing required fields)
            envelope = {
                "message_id": generate_uuid_v7_like(),
                "action": "system.health_ping"
            }

            result = supervisor.process_envelope(envelope)

            # Should be Error
            assert "error_id" in result
            assert "error_code" in result

        # Verify audit log contains failure event
        segment_path = temp_workspace / "logs" / "audit.000001.jsonl"
        assert segment_path.exists()

        import json
        with open(segment_path, 'r') as f:
            events = [json.loads(line) for line in f]

        # Should have RUN_STARTED and VALIDATION_FAILED events
        event_types = [e["event_type"] for e in events]
        assert "RUN_STARTED" in event_types
        assert "VALIDATION_FAILED" in event_types

    def test_rbac_denial_logged(self, temp_workspace):
        """RBAC denials are logged to audit."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            envelope = {
                "envelope_version": "1.0.0",
                "message_id": generate_uuid_v7_like(),
                "sender": "unauthorized_principal",
                "recipient": "executor",  # Not in policy
                "timestamp": "2026-02-08T10:00:00Z",
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {}
            }

            result = supervisor.process_envelope(envelope)

            # Should be Error (RBAC denied)
            assert "error_id" in result
            assert result["error_code"] == "RBAC_DENIED"

        # Verify audit log contains RBAC denial (as VALIDATION_FAILED with error_code)
        segment_path = temp_workspace / "logs" / "audit.000001.jsonl"
        import json
        with open(segment_path, 'r') as f:
            events = [json.loads(line) for line in f]

        # RBAC denials are logged as VALIDATION_FAILED events with error_code="RBAC_DENIED"
        validation_failed_events = [e for e in events if e.get("event_type") == "VALIDATION_FAILED"]
        rbac_events = [e for e in validation_failed_events if e.get("payload", {}).get("error_code") == "RBAC_DENIED"]
        assert len(rbac_events) > 0

    def test_multiple_tasks_sequencing(self, temp_workspace):
        """Multiple tasks execute in FIFO order."""
        with LLMRelaySupervisor(base_dir=str(temp_workspace)) as supervisor:
            task_ids = []

            # Enqueue 3 tasks
            for i in range(3):
                envelope = {
                    "envelope_version": "1.0.0",
                    "message_id": generate_uuid_v7_like(),
                    "sender": "validator",
                "recipient": "executor",
                    "timestamp": "2026-02-08T10:00:00Z",
                    "action": "system.health_ping",
                    "action_version": "1.0.0",
                    "payload": {}
                }

                validated = supervisor.process_envelope(envelope)
                task_ids.append(validated["task_id"])

            # Execute all
            execution_results = supervisor.execute_pending_tasks()
            assert len(execution_results) == 3

            # Verify FIFO order
            executed_task_ids = [r["task_id"] for r in execution_results]
            assert executed_task_ids == task_ids

    def test_supervisor_shutdown_flushes_logs(self, temp_workspace):
        """Supervisor shutdown flushes all logs."""
        supervisor = LLMRelaySupervisor(base_dir=str(temp_workspace))

        envelope = {
            "envelope_version": "1.0.0",
            "message_id": generate_uuid_v7_like(),
            "sender": "validator",
                "recipient": "executor",
            "timestamp": "2026-02-08T10:00:00Z",
            "action": "system.health_ping",
            "action_version": "1.0.0",
            "payload": {}
        }

        supervisor.process_envelope(envelope)

        # Shutdown
        supervisor.shutdown()

        # Verify audit log exists and is complete
        segment_path = temp_workspace / "logs" / "audit.000001.jsonl"
        assert segment_path.exists()

        import json
        with open(segment_path, 'r') as f:
            events = [json.loads(line) for line in f]

        # Should have at least RUN_STARTED and validation events
        assert len(events) >= 2
