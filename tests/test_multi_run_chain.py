"""
Test multi-run global hash chain continuity.

Verifies that:
- event_seq resets per run (starts at 1 for each new run_id)
- Hash chain is GLOBAL (continues across run boundaries)
- Verifier accepts interleaved runs with continuous hash chain
"""

import pytest
import json
import uuid
from pathlib import Path
from audit_logging.log_daemon import LogDaemon
from audit_logging.key_manager import KeyManager
from audit_logging.verifier import AuditLogVerifier


@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory."""
    return tmp_path


@pytest.fixture
def key_manager(temp_dir):
    """Create KeyManager with test keys."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

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

    return KeyManager(
        private_key_path=private_path,
        public_key_path=public_path
    )


def test_multi_run_global_chain(temp_dir, key_manager):
    """
    Test that multiple runs maintain a global hash chain.

    Scenario:
    - Run A: 3 events (seq 1, 2, 3)
    - Run B: 2 events (seq 1, 2)  <- seq resets, but hash chain continues
    - Run C: 1 event (seq 1)      <- seq resets, but hash chain continues

    Verification should pass with per-run sequencing and global chain.
    """
    segment_path = temp_dir / "audit.000001.jsonl"

    # Run A: Create first run with 3 events
    run_a_id = str(uuid.uuid4())
    with LogDaemon(
        run_id=run_a_id,
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir
    ) as daemon_a:
        for i in range(3):
            daemon_a.ingest_event(
                event_type="TASK_STARTED",
                actor="test",
                correlation={"session_id": None, "message_id": None, "task_id": f"a{i}"},
                payload={"index": i}
            )

    # Verify Run A wrote 3 events with seq 1, 2, 3
    with open(segment_path, 'r') as f:
        events_a = [json.loads(line) for line in f if line.strip()]

    assert len(events_a) == 3
    assert events_a[0]["event_seq"] == 1
    assert events_a[1]["event_seq"] == 2
    assert events_a[2]["event_seq"] == 3
    assert events_a[0]["run_id"] == run_a_id
    assert events_a[1]["run_id"] == run_a_id
    assert events_a[2]["run_id"] == run_a_id

    # Capture Run A's last hash
    run_a_last_hash = events_a[2]["event_hash"]

    # Run B: Create second run with 2 events (appends to same file)
    run_b_id = str(uuid.uuid4())
    with LogDaemon(
        run_id=run_b_id,
        config_hash="b" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir
    ) as daemon_b:
        for i in range(2):
            daemon_b.ingest_event(
                event_type="TASK_STARTED",
                actor="test",
                correlation={"session_id": None, "message_id": None, "task_id": f"b{i}"},
                payload={"index": i}
            )

    # Verify Run B wrote events with seq 1, 2 (reset per-run)
    with open(segment_path, 'r') as f:
        all_events = [json.loads(line) for line in f if line.strip()]

    events_b = all_events[3:5]  # Events 3-4 (0-indexed)
    assert len(events_b) == 2
    assert events_b[0]["event_seq"] == 1  # Reset to 1
    assert events_b[1]["event_seq"] == 2
    assert events_b[0]["run_id"] == run_b_id
    assert events_b[1]["run_id"] == run_b_id

    # CRITICAL: Run B's first event must use Run A's last hash (global chain)
    assert events_b[0]["prev_event_hash"] == run_a_last_hash, \
        f"Run B first event prev_hash should be Run A last hash, got {events_b[0]['prev_event_hash']}"

    # Capture Run B's last hash
    run_b_last_hash = events_b[1]["event_hash"]

    # Run C: Create third run with 1 event
    run_c_id = str(uuid.uuid4())
    with LogDaemon(
        run_id=run_c_id,
        config_hash="c" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir
    ) as daemon_c:
        daemon_c.ingest_event(
            event_type="TASK_STARTED",
            actor="test",
            correlation={"session_id": None, "message_id": None, "task_id": "c0"},
            payload={"index": 0}
        )

    # Verify Run C wrote event with seq 1 (reset per-run)
    with open(segment_path, 'r') as f:
        all_events = [json.loads(line) for line in f if line.strip()]

    events_c = all_events[5:6]  # Event 5 (0-indexed)
    assert len(events_c) == 1
    assert events_c[0]["event_seq"] == 1  # Reset to 1
    assert events_c[0]["run_id"] == run_c_id

    # CRITICAL: Run C's first event must use Run B's last hash (global chain)
    assert events_c[0]["prev_event_hash"] == run_b_last_hash, \
        f"Run C first event prev_hash should be Run B last hash, got {events_c[0]['prev_event_hash']}"

    # Now verify the entire file with the verifier
    verifier = AuditLogVerifier(key_manager)
    result = verifier.verify_segment(segment_path)

    assert result.success, f"Verification failed with errors: {result.errors}"
    assert result.events_verified == 6
    assert len(result.errors) == 0


def test_empty_file_uses_genesis(temp_dir, key_manager):
    """
    Test that a brand-new file uses GENESIS_HASH for first event.
    """
    segment_path = temp_dir / "audit.000001.jsonl"

    # Create first run (file doesn't exist yet)
    run_id = str(uuid.uuid4())
    with LogDaemon(
        run_id=run_id,
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir
    ) as daemon:
        daemon.ingest_event(
            event_type="TASK_STARTED",
            actor="test",
            correlation={"session_id": None, "message_id": None, "task_id": "t0"},
            payload={"index": 0}
        )

    # Verify first event uses GENESIS_HASH
    with open(segment_path, 'r') as f:
        events = [json.loads(line) for line in f if line.strip()]

    assert len(events) == 1
    assert events[0]["event_seq"] == 1
    assert events[0]["prev_event_hash"] == "0" * 64  # GENESIS_HASH


def test_restart_continues_chain(temp_dir, key_manager):
    """
    Test that restarting the daemon (new LogDaemon instance) continues the chain.
    """
    segment_path = temp_dir / "audit.000001.jsonl"

    # First daemon writes 2 events
    run_id_1 = str(uuid.uuid4())
    with LogDaemon(
        run_id=run_id_1,
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir
    ) as daemon1:
        daemon1.ingest_event(
            event_type="TASK_STARTED",
            actor="test",
            correlation={"session_id": None, "message_id": None, "task_id": "t0"},
            payload={"index": 0}
        )
        daemon1.ingest_event(
            event_type="TASK_STARTED",
            actor="test",
            correlation={"session_id": None, "message_id": None, "task_id": "t1"},
            payload={"index": 1}
        )

    # Read last hash
    with open(segment_path, 'r') as f:
        events1 = [json.loads(line) for line in f if line.strip()]
    last_hash_1 = events1[-1]["event_hash"]

    # Second daemon (different run_id) writes 1 event
    run_id_2 = str(uuid.uuid4())
    with LogDaemon(
        run_id=run_id_2,
        config_hash="b" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=temp_dir
    ) as daemon2:
        daemon2.ingest_event(
            event_type="TASK_STARTED",
            actor="test",
            correlation={"session_id": None, "message_id": None, "task_id": "t2"},
            payload={"index": 2}
        )

    # Verify second daemon continued the chain
    with open(segment_path, 'r') as f:
        all_events = [json.loads(line) for line in f if line.strip()]

    assert len(all_events) == 3
    event3 = all_events[2]
    assert event3["event_seq"] == 1  # New run, seq reset
    assert event3["run_id"] == run_id_2
    assert event3["prev_event_hash"] == last_hash_1, \
        "Second daemon should continue global hash chain"

    # Verify entire file
    verifier = AuditLogVerifier(key_manager)
    result = verifier.verify_segment(segment_path)
    assert result.success
    assert result.events_verified == 3
