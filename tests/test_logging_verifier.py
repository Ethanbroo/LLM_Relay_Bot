"""
Tests for audit_logging/verifier.py

Covers:
- Event verification
- Hash chain verification
- Signature verification
- Tamper detection
"""

import pytest
import tempfile
import json
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from audit_logging.verifier import AuditLogVerifier, VerificationResult
from audit_logging.key_manager import KeyManager
from audit_logging.log_daemon import LogDaemon


@pytest.fixture
def temp_dir():
    """Create temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_keys(temp_dir):
    """Generate test keypair."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    private_path = temp_dir / "private.pem"
    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path = temp_dir / "public.pem"
    public_path.write_bytes(public_pem)

    return {"private_path": private_path, "public_path": public_path}


@pytest.fixture
def key_manager(test_keys):
    """Create KeyManager."""
    return KeyManager(
        private_key_path=test_keys["private_path"],
        public_key_path=test_keys["public_path"],
    )


@pytest.fixture
def verifier(key_manager):
    """Create AuditLogVerifier."""
    return AuditLogVerifier(key_manager)


def create_valid_log(temp_dir, key_manager, num_events=5):
    """Helper to create valid audit log."""
    with LogDaemon(
        run_id=str(uuid.uuid4()),
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir,
    ) as daemon:
        for i in range(num_events):
            daemon.ingest_event(
                event_type="TASK_STARTED",
                actor="executor",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload={"task_id": f"t{i}", "attempt": 0}
            )

    return temp_dir / "audit.000001.jsonl"


class TestVerifySegment:
    """Tests for verify_segment method."""

    def test_verify_valid_segment(self, temp_dir, key_manager, verifier):
        """Verify a valid segment"""
        segment_path = create_valid_log(temp_dir, key_manager, num_events=5)

        result = verifier.verify_segment(segment_path)

        assert result.success is True
        assert result.events_verified == 5
        assert result.segments_verified == 1
        assert len(result.errors) == 0
        assert result.tamper_detected is False

    def test_verify_nonexistent_segment(self, temp_dir, verifier):
        """Verification fails for nonexistent segment"""
        result = verifier.verify_segment(temp_dir / "nonexistent.jsonl")

        assert result.success is False
        assert "not found" in result.errors[0].lower()

    def test_detect_tampered_event_seq(self, temp_dir, key_manager, verifier):
        """Detect tampered event_seq"""
        segment_path = create_valid_log(temp_dir, key_manager, num_events=3)

        # Tamper with event_seq
        with open(segment_path, 'r') as f:
            lines = f.readlines()

        events = [json.loads(line) for line in lines]
        events[1]["event_seq"] = 999  # Tamper

        with open(segment_path, 'w') as f:
            for event in events:
                f.write(json.dumps(event) + '\n')

        result = verifier.verify_segment(segment_path)

        assert result.success is False
        assert any("event_seq mismatch" in err for err in result.errors)

    def test_detect_broken_hash_chain(self, temp_dir, key_manager, verifier):
        """Detect broken hash chain"""
        segment_path = create_valid_log(temp_dir, key_manager, num_events=3)

        # Break hash chain
        with open(segment_path, 'r') as f:
            lines = f.readlines()

        events = [json.loads(line) for line in lines]
        events[1]["prev_event_hash"] = "f" * 64  # Break chain

        with open(segment_path, 'w') as f:
            for event in events:
                f.write(json.dumps(event) + '\n')

        result = verifier.verify_segment(segment_path)

        assert result.success is False
        assert result.tamper_detected is True
        assert any("chain mismatch" in err.lower() for err in result.errors)

    def test_detect_invalid_signature(self, temp_dir, key_manager, verifier):
        """Detect invalid signature"""
        segment_path = create_valid_log(temp_dir, key_manager, num_events=3)

        # Tamper with signature
        with open(segment_path, 'r') as f:
            lines = f.readlines()

        events = [json.loads(line) for line in lines]
        events[1]["signature"] = "A" * 88  # Invalid signature

        with open(segment_path, 'w') as f:
            for event in events:
                f.write(json.dumps(event) + '\n')

        result = verifier.verify_segment(segment_path)

        assert result.success is False
        assert result.tamper_detected is True
        assert any("signature" in err.lower() for err in result.errors)


class TestVerifyChain:
    """Tests for verify_chain method."""

    def test_verify_single_segment_chain(self, temp_dir, key_manager, verifier):
        """Verify chain with single segment"""
        segment_path = create_valid_log(temp_dir, key_manager, num_events=5)

        result = verifier.verify_chain([segment_path])

        assert result.success is True
        assert result.events_verified == 5
        assert result.segments_verified == 1

    def test_verify_empty_chain(self, verifier):
        """Verify empty chain"""
        result = verifier.verify_chain([])

        assert result.success is True
        assert result.events_verified == 0
        assert result.segments_verified == 0
