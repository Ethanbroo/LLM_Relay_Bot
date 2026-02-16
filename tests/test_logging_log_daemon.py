"""
Tests for audit_logging/log_daemon.py

Covers:
- Event ingestion
- Event type validation
- Secret redaction enforcement
- Monotonic event_seq
- Hash chain construction
- Event signing
- JSONL writing
- Fsync policy
"""

import pytest
import tempfile
import json
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from audit_logging.log_daemon import (
    LogDaemon,
    InvalidEventTypeError,
    SecretLeakError,
    GENESIS_HASH,
    VALID_EVENT_TYPES,
)
from audit_logging.key_manager import KeyManager
from audit_logging.crypto import verify_signature


@pytest.fixture
def temp_log_dir():
    """Create temporary directory for log files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_keys(temp_log_dir):
    """Generate test keypair."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save keys
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    private_path = temp_log_dir / "private.pem"
    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path = temp_log_dir / "public.pem"
    public_path.write_bytes(public_pem)

    return {"private_path": private_path, "public_path": public_path}


@pytest.fixture
def key_manager(test_keys):
    """Create KeyManager for tests."""
    return KeyManager(
        private_key_path=test_keys["private_path"],
        public_key_path=test_keys["public_path"],
        enforce_permissions=True
    )


@pytest.fixture
def log_daemon(temp_log_dir, key_manager):
    """Create LogDaemon for tests."""
    daemon = LogDaemon(
        run_id=str(uuid.uuid4()),
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_log_dir,
        segment_filename="audit.000001.jsonl",
        fsync_every_n_events=100
    )
    yield daemon
    daemon.close()


class TestLogDaemonInit:
    """Tests for LogDaemon initialization."""

    def test_creates_log_directory(self, temp_log_dir, key_manager):
        """LogDaemon creates log directory if missing"""
        log_dir = temp_log_dir / "logs"
        assert not log_dir.exists()

        daemon = LogDaemon(
            run_id=str(uuid.uuid4()),
            config_hash="a" * 64,
            time_policy="frozen",
            key_manager=key_manager,
            log_directory=log_dir,
        )

        assert log_dir.exists()
        daemon.close()

    def test_initial_state(self, log_daemon):
        """Initial state has event_seq=0, genesis prev_event_hash"""
        state = log_daemon.get_current_state()

        assert state["event_seq"] == 0
        assert state["prev_event_hash"] == GENESIS_HASH
        assert state["events_since_fsync"] == 0


class TestEventIngestion:
    """Tests for ingest_event method."""

    def test_ingest_simple_event(self, log_daemon):
        """Ingest a simple event"""
        event = log_daemon.ingest_event(
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"run_id": log_daemon.run_id, "config_hash": "a" * 64, "time_policy": "frozen", "module_versions": {}},
            timestamp=None
        )

        assert event["schema_id"] == "relay.audit_event"
        assert event["schema_version"] == "1.0.0"
        assert event["event_seq"] == 1
        assert event["event_type"] == "RUN_STARTED"
        assert event["actor"] == "supervisor"
        assert event["prev_event_hash"] == GENESIS_HASH
        assert len(event["event_hash"]) == 64
        assert len(event["signature"]) == 88

    def test_event_seq_increments(self, log_daemon):
        """event_seq increments monotonically"""
        event1 = log_daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": "t1"},
            payload={"task_id": "t1", "attempt": 0}
        )

        event2 = log_daemon.ingest_event(
            event_type="TASK_FINISHED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": "t1"},
            payload={"task_id": "t1", "attempt": 0, "success": True}
        )

        assert event1["event_seq"] == 1
        assert event2["event_seq"] == 2

    def test_hash_chain_construction(self, log_daemon):
        """prev_event_hash forms unbroken chain"""
        event1 = log_daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0}
        )

        event2 = log_daemon.ingest_event(
            event_type="TASK_FINISHED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0, "success": True}
        )

        # Event 1 prev_event_hash should be genesis
        assert event1["prev_event_hash"] == GENESIS_HASH

        # Event 2 prev_event_hash should be event 1's event_hash
        assert event2["prev_event_hash"] == event1["event_hash"]


class TestEventTypeValidation:
    """Tests for event type validation."""

    def test_valid_event_types_accepted(self, log_daemon):
        """All valid event types are accepted"""
        for event_type in ["RUN_STARTED", "TASK_STARTED", "ENGINE_HALTED"]:
            event = log_daemon.ingest_event(
                event_type=event_type,
                actor="test",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload={}
            )
            assert event["event_type"] == event_type

    def test_invalid_event_type_rejected(self, log_daemon):
        """Unknown event types are rejected"""
        with pytest.raises(InvalidEventTypeError, match="Unknown event_type: INVALID_TYPE"):
            log_daemon.ingest_event(
                event_type="INVALID_TYPE",
                actor="test",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload={}
            )

    def test_closed_enum_enforcement(self, log_daemon):
        """Event type taxonomy is closed (no runtime expansion)"""
        # This should fail - system doesn't expand taxonomy
        with pytest.raises(InvalidEventTypeError):
            log_daemon.ingest_event(
                event_type="NEW_EVENT_TYPE",
                actor="test",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload={}
            )


class TestSecretRedaction:
    """Tests for secret redaction enforcement."""

    def test_secrets_redacted_from_payload(self, log_daemon):
        """Secrets in payload are redacted"""
        event = log_daemon.ingest_event(
            event_type="VALIDATION_FAILED",
            actor="validator",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "validation_id": "v1",
                "error_code": "SCHEMA_FAILED",
                "stage": "json_schema",
                "password": "secret123"  # Secret should be redacted
            }
        )

        # Payload should have password redacted
        assert event["payload"]["password"] == "REDACTED"

        # Redaction metadata should indicate redaction
        assert event["redaction"]["was_redacted"] is True
        assert "/password" in event["redaction"]["redacted_paths"]

    def test_bearer_token_redacted(self, log_daemon):
        """Bearer tokens are redacted"""
        event = log_daemon.ingest_event(
            event_type="VALIDATION_FAILED",
            actor="validator",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "validation_id": "v1",
                "error_code": "AUTH_FAILED",
                "stage": "rbac",
                "reason": "Authorization: Bearer abc123"
            }
        )

        # Reason should be redacted (contains Bearer token)
        assert event["payload"]["reason"] == "REDACTED"
        assert event["redaction"]["was_redacted"] is True


class TestEventSigning:
    """Tests for Ed25519 event signing."""

    def test_event_has_valid_signature(self, log_daemon, key_manager):
        """Event signature is valid"""
        event = log_daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0}
        )

        # Verify signature
        is_valid = verify_signature(
            key_manager.public_key,
            event["event_hash"],
            event["signature"]
        )
        assert is_valid is True

    def test_tampered_event_fails_verification(self, log_daemon, key_manager):
        """Tampered event fails signature verification"""
        event = log_daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0}
        )

        # Tamper with event_seq
        original_seq = event["event_seq"]
        event["event_seq"] = 999

        # Recompute event_hash with tampered data
        from audit_logging.canonicalize import compute_event_hash
        tampered_hash = compute_event_hash(event)

        # Original signature should NOT verify tampered hash
        is_valid = verify_signature(
            key_manager.public_key,
            tampered_hash,
            event["signature"]
        )
        assert is_valid is False


class TestJSONLWriting:
    """Tests for JSONL file writing."""

    def test_events_written_to_file(self, log_daemon, temp_log_dir):
        """Events are written to JSONL file"""
        log_daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0}
        )

        log_daemon.ingest_event(
            event_type="TASK_FINISHED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0, "success": True}
        )

        log_daemon.close()

        # Read file and verify 2 events
        segment_path = temp_log_dir / "audit.000001.jsonl"
        with open(segment_path, 'r') as f:
            lines = f.readlines()

        assert len(lines) == 2

        # Parse events
        event1 = json.loads(lines[0])
        event2 = json.loads(lines[1])

        assert event1["event_seq"] == 1
        assert event2["event_seq"] == 2
        assert event2["prev_event_hash"] == event1["event_hash"]

    def test_jsonl_format_valid(self, log_daemon, temp_log_dir):
        """Written JSONL is valid (one JSON object per line)"""
        log_daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="executor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={"task_id": "t1", "attempt": 0}
        )

        log_daemon.close()

        segment_path = temp_log_dir / "audit.000001.jsonl"
        with open(segment_path, 'r') as f:
            line = f.readline()

        # Should be valid JSON
        event = json.loads(line)
        assert event["schema_id"] == "relay.audit_event"

        # Should end with newline
        assert line.endswith('\n')


class TestContextManager:
    """Tests for context manager usage."""

    def test_context_manager_closes_file(self, temp_log_dir, key_manager):
        """Context manager ensures file is closed"""
        with LogDaemon(
            run_id=str(uuid.uuid4()),
            config_hash="a" * 64,
            time_policy="frozen",
            key_manager=key_manager,
            log_directory=temp_log_dir,
        ) as daemon:
            daemon.ingest_event(
                event_type="TASK_STARTED",
                actor="executor",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload={"task_id": "t1", "attempt": 0}
            )

        # File should be closed and fsynced
        segment_path = temp_log_dir / "audit.000001.jsonl"
        assert segment_path.exists()

        # Should be able to read file
        with open(segment_path, 'r') as f:
            lines = f.readlines()
        assert len(lines) == 1
