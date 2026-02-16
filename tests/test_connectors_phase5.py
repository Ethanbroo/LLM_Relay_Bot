"""Comprehensive tests for Phase 5: Connectors (Controlled Power).

Tests:
- Connector registry (closed, no dynamic loading)
- Idempotency ledger
- Secrets provider with leak detection
- Lifecycle runner with audit events
- LocalFS connector with workspace boundaries
- Google Docs stub connector
- Integration with Phase 4 coordination
"""

import pytest
import os
import tempfile
import shutil
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock

from connectors.registry import ConnectorRegistry, register_connector, register_action, get_global_registry
from connectors.idempotency import IdempotencyLedger, IdempotencyRecord
from connectors.secrets import SecretsProvider, detect_secret_leak
from connectors.lifecycle import ConnectorLifecycleRunner, ConnectorAuditEvent
from connectors.local_fs import LocalFSConnector
from connectors.google_docs_stub import GoogleDocsStubConnector
from connectors.base import ConnectorRequest, ConnectorContext, CoordinationProof
from connectors.results import ConnectorStatus, RollbackStatus, VerificationMethod
from connectors.errors import (
    ConnectorUnknownError,
    ConnectorNotRegisteredError,
    SecretUnavailableError,
    SecretLeakDetectedError,
    ConnectorError,
    PhaseBoundaryViolationError,
    ConnectorInputTooLargeError
)


class TestConnectorRegistry:
    """Test closed connector registry."""

    def test_register_connector(self):
        """Test connector registration."""
        registry = ConnectorRegistry()

        registry.register("test_connector", LocalFSConnector)

        assert registry.is_registered("test_connector")
        assert registry.get_connector_class("test_connector") == LocalFSConnector

    def test_duplicate_registration_fails(self):
        """Test that duplicate connector registration fails."""
        registry = ConnectorRegistry()

        registry.register("test_connector", LocalFSConnector)

        with pytest.raises(ValueError, match="already registered"):
            registry.register("test_connector", GoogleDocsStubConnector)

    def test_unknown_connector_raises_error(self):
        """Test that unknown connector raises ConnectorUnknownError."""
        registry = ConnectorRegistry()

        with pytest.raises(ConnectorUnknownError, match="Unknown connector type"):
            registry.get_connector_class("nonexistent")

    def test_action_mapping(self):
        """Test action to connector mapping."""
        registry = ConnectorRegistry()

        registry.register("local_fs", LocalFSConnector)
        registry.register_action_mapping("fs.write_file", "local_fs", "execute")

        assert registry.has_action_mapping("fs.write_file")
        connector_type, method = registry.get_connector_for_action("fs.write_file")
        assert connector_type == "local_fs"
        assert method == "execute"

    def test_duplicate_action_mapping_fails(self):
        """Test that duplicate action mapping fails."""
        registry = ConnectorRegistry()

        registry.register_action_mapping("fs.write_file", "local_fs", "execute")

        with pytest.raises(ValueError, match="already mapped"):
            registry.register_action_mapping("fs.write_file", "other", "execute")

    def test_unmapped_action_raises_error(self):
        """Test that unmapped action raises ConnectorNotRegisteredError."""
        registry = ConnectorRegistry()

        with pytest.raises(ConnectorNotRegisteredError, match="No connector registered"):
            registry.get_connector_for_action("nonexistent.action")

    def test_list_connectors_and_actions(self):
        """Test listing registered connectors and actions."""
        registry = ConnectorRegistry()

        registry.register("local_fs", LocalFSConnector)
        registry.register("google_docs", GoogleDocsStubConnector)
        registry.register_action_mapping("fs.write", "local_fs", "execute")
        registry.register_action_mapping("gdocs.create", "google_docs", "execute")

        connectors = registry.list_connectors()
        assert "local_fs" in connectors
        assert "google_docs" in connectors

        actions = registry.list_actions()
        assert "fs.write" in actions
        assert "gdocs.create" in actions


class TestIdempotencyLedger:
    """Test idempotency ledger."""

    def test_check_not_executed(self):
        """Test checking idempotency key that hasn't been executed."""
        ledger = IdempotencyLedger()

        assert not ledger.has_executed("test_key")
        assert ledger.check("test_key") is None

    def test_record_and_check_executed(self):
        """Test recording execution and checking."""
        ledger = IdempotencyLedger()

        from connectors.results import ConnectorResult, ConnectorStatus

        result = ConnectorResult(
            status=ConnectorStatus.SUCCESS,
            connector_type="test",
            idempotency_key="test_key",
            result_hash="abc123"
        )

        ledger.record("test_key", result)

        assert ledger.has_executed("test_key")
        record = ledger.check("test_key")
        assert record is not None
        assert record.idempotency_key == "test_key"
        assert record.status == ConnectorStatus.SUCCESS
        assert record.result_hash == "abc123"

    def test_get_result(self):
        """Test getting prior result."""
        ledger = IdempotencyLedger()

        from connectors.results import ConnectorResult, ConnectorStatus

        result = ConnectorResult(
            status=ConnectorStatus.SUCCESS,
            connector_type="test",
            idempotency_key="test_key",
            result_hash="abc123"
        )

        ledger.record("test_key", result)

        retrieved_result = ledger.get_result("test_key")
        assert retrieved_result == result


class TestSecretsProvider:
    """Test secrets provider with opaque handles."""

    def test_resolve_secret_from_env(self):
        """Test resolving secret from environment variable."""
        os.environ["LLM_RELAY_SECRET_TEST_KEY"] = "secret_value"

        provider = SecretsProvider()
        secret = provider.resolve_string("secret:test_key")

        assert secret == "secret_value"

        del os.environ["LLM_RELAY_SECRET_TEST_KEY"]

    def test_resolve_nonexistent_secret_fails(self):
        """Test that resolving nonexistent secret fails."""
        provider = SecretsProvider()

        with pytest.raises(SecretUnavailableError, match="Secret not found"):
            provider.resolve("secret:nonexistent")

    def test_invalid_secret_handle_format(self):
        """Test that invalid secret handle format fails."""
        provider = SecretsProvider()

        with pytest.raises(SecretUnavailableError, match="Invalid secret handle"):
            provider.resolve("invalid_format")

    def test_detect_bearer_token_leak(self):
        """Test detecting Bearer token leak."""
        assert detect_secret_leak("Authorization: Bearer abc123def456") is True
        assert detect_secret_leak("No secrets here") is False

    def test_detect_jwt_leak(self):
        """Test detecting JWT leak."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        assert detect_secret_leak(jwt) is True

    def test_detect_api_key_leak(self):
        """Test detecting API key leak."""
        assert detect_secret_leak("sk-1234567890abcdefghij") is True

    def test_redact_secrets(self):
        """Test secret redaction."""
        provider = SecretsProvider()

        text = "Token: Bearer abc123 and sk-keyvalue"
        redacted = provider.redact_secrets(text)

        assert "[REDACTED]" in redacted
        assert "abc123" not in redacted

    def test_check_for_leaks_raises_error(self):
        """Test that check_for_leaks raises error on leak detection."""
        provider = SecretsProvider()

        with pytest.raises(SecretLeakDetectedError):
            provider.check_for_leaks("Bearer token_value")


class TestConnectorRequest:
    """Test ConnectorRequest creation and validation."""

    def test_from_coordinated_action(self):
        """Test creating ConnectorRequest from CoordinatedAction."""
        # Mock CoordinatedAction
        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="fs.write_file",
            action_version="v1",
            payload={"path": "test.txt", "content": "data"},
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id="lockset_1",
            acquired_locks=["lock_1"],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        assert req.run_id == "run_123"
        assert req.task_id == "task_123"
        assert req.action == "fs.write_file"
        assert req.coordination_proof.coordination_id == "coord_123"
        assert len(req.idempotency_key) == 64  # SHA-256 hex

    def test_from_coordinated_action_without_coordination_id_fails(self):
        """Test that missing coordination_id raises PhaseBoundaryViolationError."""
        # Mock invalid CoordinatedAction
        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="fs.write_file",
            action_version="v1",
            payload={"path": "test.txt"},
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id=None,  # Missing!
            coordination_event_seq=0,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        with pytest.raises(PhaseBoundaryViolationError, match="coordination_id"):
            ConnectorRequest.from_coordinated_action(
                coordinated_action,
                run_id="run_123",
                config_hash="config_abc"
            )

    def test_validate_size_limits_payload_too_large(self):
        """Test payload size validation."""
        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        # Create large payload
        large_payload = {"data": "x" * 2000000}  # 2MB

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="fs.write_file",
            action_version="v1",
            payload=large_payload,
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        with pytest.raises(ConnectorInputTooLargeError, match="Payload size"):
            req.validate_size_limits(max_payload_bytes=1048576, max_nesting_depth=32)


class TestLocalFSConnector:
    """Test LocalFS connector with workspace boundaries."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        workspace = tempfile.mkdtemp()
        yield workspace
        shutil.rmtree(workspace)

    @pytest.fixture
    def connector(self, temp_workspace):
        """Create and connect LocalFS connector."""
        connector = LocalFSConnector()
        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root=temp_workspace
        )
        connector.connect(ctx)
        return connector

    def test_connect(self, temp_workspace):
        """Test connector connection."""
        connector = LocalFSConnector()
        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root=temp_workspace
        )

        connector.connect(ctx)
        assert connector._workspace_root == Path(temp_workspace).resolve()

    def test_connect_nonexistent_workspace_fails(self):
        """Test that connecting to nonexistent workspace fails."""
        connector = LocalFSConnector()
        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root="/nonexistent/workspace"
        )

        with pytest.raises(ConnectorError, match="Workspace root does not exist"):
            connector.connect(ctx)

    def test_write_file(self, connector, temp_workspace):
        """Test writing file within workspace."""
        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        payload = {
            "path": "test.txt",
            "content": "Hello, World!"
        }

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="fs.write_file",
            action_version="v1",
            payload=payload,
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        result = connector.execute(req)

        assert result.status == ConnectorStatus.SUCCESS
        assert "file_hash" in result.artifacts

        # Verify file exists
        file_path = Path(temp_workspace) / "test.txt"
        assert file_path.exists()
        assert file_path.read_text() == "Hello, World!"

    def test_write_file_outside_workspace_fails(self, connector, temp_workspace):
        """Test that writing outside workspace fails."""
        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        payload = {
            "path": "../escape.txt",
            "content": "Escaped!"
        }

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="fs.write_file",
            action_version="v1",
            payload=payload,
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        with pytest.raises(ConnectorError, match="Path escapes workspace"):
            connector.execute(req)

    def test_rollback_restores_file(self, connector, temp_workspace):
        """Test rollback restores previous file state."""
        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        # Create initial file
        file_path = Path(temp_workspace) / "test.txt"
        file_path.write_text("Original content")
        original_hash = hashlib.sha256(b"Original content").hexdigest()

        # Modify file
        payload = {
            "path": "test.txt",
            "content": "Modified content"
        }

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="fs.write_file",
            action_version="v1",
            payload=payload,
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        connector.execute(req)

        # Rollback
        rollback_result = connector.rollback(req, None)

        assert rollback_result.rollback_status == RollbackStatus.SUCCESS
        assert rollback_result.verification_method == VerificationMethod.FILE_HASH
        assert rollback_result.verification_artifact_hash == original_hash

        # Verify file restored
        assert file_path.read_text() == "Original content"


class TestGoogleDocsStubConnector:
    """Test Google Docs stub connector."""

    def test_connect(self):
        """Test connector connection."""
        connector = GoogleDocsStubConnector()
        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root="/workspace"
        )

        connector.connect(ctx)
        assert connector._connected is True

    def test_create_document_stub(self):
        """Test stub document creation."""
        connector = GoogleDocsStubConnector()
        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root="/workspace"
        )
        connector.connect(ctx)

        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        payload = {"title": "Test Document"}

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="gdocs.create_document",
            action_version="v1",
            payload=payload,
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        result = connector.execute(req)

        assert result.status == ConnectorStatus.SUCCESS
        assert result.external_transaction_id is not None
        assert "doc_id" in result.artifacts

    def test_rollback_stub(self):
        """Test stub rollback."""
        connector = GoogleDocsStubConnector()
        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root="/workspace"
        )
        connector.connect(ctx)

        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="gdocs.create_document",
            action_version="v1",
            payload={"title": "Test"},
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        rollback_result = connector.rollback(req, None)

        assert rollback_result.rollback_status == RollbackStatus.SUCCESS
        assert rollback_result.verification_method == VerificationMethod.EXTERNAL_VERIFICATION


class TestConnectorLifecycleRunner:
    """Test connector lifecycle runner with audit integration."""

    def test_full_lifecycle_success(self):
        """Test successful full lifecycle."""
        connector = GoogleDocsStubConnector()
        ledger = IdempotencyLedger()
        audit_events = []

        def audit_callback(event: ConnectorAuditEvent):
            audit_events.append(event)

        runner = ConnectorLifecycleRunner(connector, ledger, audit_callback)

        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root="/workspace"
        )

        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="gdocs.create_document",
            action_version="v1",
            payload={"title": "Test"},
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        result = runner.run_full_lifecycle(ctx, req, rollback_on_failure=True)

        assert result.status == ConnectorStatus.SUCCESS

        # Verify audit events emitted
        event_types = [e.event_type for e in audit_events]
        assert "CONNECTOR_CONNECT_STARTED" in event_types
        assert "CONNECTOR_CONNECTED" in event_types
        assert "CONNECTOR_EXECUTE_STARTED" in event_types
        assert "CONNECTOR_EXECUTE_FINISHED" in event_types
        assert "CONNECTOR_DISCONNECT_STARTED" in event_types
        assert "CONNECTOR_DISCONNECTED" in event_types

    def test_idempotency_hit(self):
        """Test idempotency hit returns prior result."""
        connector = GoogleDocsStubConnector()
        ledger = IdempotencyLedger()
        audit_events = []

        def audit_callback(event: ConnectorAuditEvent):
            audit_events.append(event)

        runner = ConnectorLifecycleRunner(connector, ledger, audit_callback)

        ctx = ConnectorContext(
            task_id="task_123",
            attempt=1,
            workspace_root="/workspace"
        )

        from coordination.phase4_pipeline import CoordinatedAction, ValidatedAction

        validated_action = ValidatedAction(
            validation_id="val_123",
            task_id="task_123",
            action="gdocs.create_document",
            action_version="v1",
            payload={"title": "Test"},
            schema_hash="abc123",
            rbac_rule_id="rule_1",
            sender="user",
            recipient="system",
            message_id="msg_123",
            enqueue_seq=1,
            attempt=1,
            approval_id=None
        )

        coordinated_action = CoordinatedAction(
            validated_action=validated_action,
            coordination_id="coord_123",
            coordination_event_seq=1,
            lock_set_id=None,
            acquired_locks=[],
            approval_verified=False
        )

        req = ConnectorRequest.from_coordinated_action(
            coordinated_action,
            run_id="run_123",
            config_hash="config_abc"
        )

        # First execution
        result1 = runner.run_full_lifecycle(ctx, req, rollback_on_failure=True)

        # Create new runner with same ledger
        runner2 = ConnectorLifecycleRunner(
            GoogleDocsStubConnector(),
            ledger,  # Same ledger!
            audit_callback
        )

        runner2.connect(ctx)

        # Second execution should hit idempotency
        result2 = runner2.execute(req)

        assert result2 == result1

        # Verify CONNECTOR_IDEMPOTENCY_HIT event
        event_types = [e.event_type for e in audit_events]
        assert "CONNECTOR_IDEMPOTENCY_HIT" in event_types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
