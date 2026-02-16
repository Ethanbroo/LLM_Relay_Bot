#!/usr/bin/env python3
"""
Phase 3 Demo: Tamper-Evident Audit Logging

This script demonstrates the Phase 3 audit logging system.
"""

import uuid
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from audit_logging.key_manager import KeyManager
from audit_logging.log_daemon import LogDaemon
from audit_logging.verifier import AuditLogVerifier
from audit_logging.recovery import CrashRecoveryManager


def generate_test_keys(temp_dir: Path):
    """Generate test Ed25519 keypair."""
    print("🔑 Generating Ed25519 keypair...")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save private key with 0600 permissions
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    private_path = temp_dir / "audit_private.pem"
    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)

    # Save public key
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path = temp_dir / "audit_public.pem"
    public_path.write_bytes(public_pem)

    print(f"   ✅ Private key: {private_path}")
    print(f"   ✅ Public key: {public_path}")
    print(f"   ✅ Permissions: {oct(private_path.stat().st_mode)[-3:]}")

    return private_path, public_path


def demo_logging(temp_dir: Path, private_path: Path, public_path: Path):
    """Demonstrate audit logging."""
    print("\n📝 Creating audit log...")

    # Load keys
    key_manager = KeyManager(
        private_key_path=private_path,
        public_key_path=public_path
    )
    print(f"   ✅ Keys loaded (fingerprint: {key_manager.public_key_fingerprint[:16]}...)")

    # Create log daemon
    run_id = str(uuid.uuid4())
    log_dir = temp_dir / "logs"
    log_dir.mkdir()

    with LogDaemon(
        run_id=run_id,
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=log_dir,
        fsync_every_n_events=10
    ) as daemon:

        # Log events
        events = [
            ("RUN_STARTED", "supervisor", {
                "run_id": run_id,
                "config_hash": "a" * 64,
                "time_policy": "frozen",
                "module_versions": {"validator": "1.0.0"}
            }),
            ("TASK_ENQUEUED", "executor", {
                "task_id": "task-001",
                "enqueue_seq": 1
            }),
            ("TASK_STARTED", "executor", {
                "task_id": "task-001",
                "attempt": 0
            }),
            ("HANDLER_STARTED", "executor", {
                "task_id": "task-001",
                "handler_name": "fs.read"
            }),
            ("HANDLER_FINISHED", "executor", {
                "task_id": "task-001",
                "handler_name": "fs.read",
                "success": True
            }),
            ("TASK_FINISHED", "executor", {
                "task_id": "task-001",
                "attempt": 0,
                "success": True
            }),
        ]

        for event_type, actor, payload in events:
            event = daemon.ingest_event(
                event_type=event_type,
                actor=actor,
                correlation={
                    "session_id": "session-123",
                    "message_id": "msg-456",
                    "task_id": "task-001"
                },
                payload=payload
            )
            print(f"   ✅ Event {event['event_seq']}: {event_type}")

        # Get daemon state
        state = daemon.get_current_state()
        print(f"\n   📊 Final state:")
        print(f"      Events logged: {state['event_seq']}")
        print(f"      Last event hash: {state['prev_event_hash'][:16]}...")
        print(f"      Segment: {Path(state['segment_path']).name}")

    return log_dir, run_id


def demo_secret_redaction(temp_dir: Path, private_path: Path, public_path: Path):
    """Demonstrate secret redaction."""
    print("\n🔒 Testing secret redaction...")

    key_manager = KeyManager(private_key_path=private_path, public_key_path=public_path)
    log_dir = temp_dir / "logs_redaction"
    log_dir.mkdir()

    with LogDaemon(
        run_id=str(uuid.uuid4()),
        config_hash="a" * 64,
        time_policy="frozen",
        key_manager=key_manager,
        log_directory=log_dir
    ) as daemon:

        # Log event with secrets
        event = daemon.ingest_event(
            event_type="VALIDATION_FAILED",
            actor="validator",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "validation_id": "v1",
                "error_code": "AUTH_FAILED",
                "stage": "rbac",
                "password": "secret123",  # Secret!
                "reason": "Bearer token_abc123"  # Secret!
            }
        )

        print(f"   ✅ Secrets redacted:")
        print(f"      password: {event['payload']['password']}")
        print(f"      reason: {event['payload']['reason']}")
        print(f"      Redacted paths: {event['redaction']['redacted_paths']}")


def demo_verification(log_dir: Path, private_path: Path, public_path: Path):
    """Demonstrate log verification."""
    print("\n✅ Verifying audit log integrity...")

    key_manager = KeyManager(private_key_path=private_path, public_key_path=public_path)
    verifier = AuditLogVerifier(key_manager)

    segment_path = log_dir / "audit.000001.jsonl"
    result = verifier.verify_segment(segment_path)

    if result.success:
        print(f"   ✅ Verification passed!")
        print(f"      Events verified: {result.events_verified}")
        print(f"      Hash chain: intact")
        print(f"      Signatures: valid")
    else:
        print(f"   ❌ Verification failed!")
        for error in result.errors:
            print(f"      - {error}")


def demo_tamper_detection(log_dir: Path, private_path: Path, public_path: Path):
    """Demonstrate tamper detection."""
    print("\n🚨 Testing tamper detection...")

    import json

    # Tamper with a log entry
    segment_path = log_dir / "audit.000001.jsonl"
    with open(segment_path, 'r') as f:
        lines = f.readlines()

    events = [json.loads(line) for line in lines]
    print(f"   Original event_seq: {events[2]['event_seq']}")

    # Tamper with event_seq
    events[2]['event_seq'] = 999
    print(f"   Tampered event_seq: {events[2]['event_seq']}")

    with open(segment_path, 'w') as f:
        for event in events:
            f.write(json.dumps(event) + '\n')

    # Try to verify
    key_manager = KeyManager(private_key_path=private_path, public_key_path=public_path)
    verifier = AuditLogVerifier(key_manager)
    result = verifier.verify_segment(segment_path)

    if not result.success and result.tamper_detected:
        print(f"   ✅ Tampering detected successfully!")
        print(f"      Error: {result.errors[0]}")
    else:
        print(f"   ❌ Failed to detect tampering!")


def demo_recovery(log_dir: Path, private_path: Path, public_path: Path):
    """Demonstrate crash recovery."""
    print("\n♻️  Testing crash recovery...")

    key_manager = KeyManager(private_key_path=private_path, public_key_path=public_path)
    recovery_mgr = CrashRecoveryManager(log_directory=log_dir, key_manager=key_manager)

    result = recovery_mgr.recover()

    if result.success:
        print(f"   ✅ Recovery successful!")
        print(f"      Last valid event: {result.last_valid_event_seq}")
        print(f"      Last valid hash: {result.last_valid_event_hash[:16]}...")

        if result.corruption_detected:
            print(f"      ⚠️  Corruption: {result.truncated_lines} lines truncated")
        if result.tamper_detected:
            print(f"      🚨 TAMPERING DETECTED!")
    else:
        print(f"   ❌ Recovery failed: {result.error_message}")


def main():
    """Run Phase 3 demo."""
    print("=" * 60)
    print("Phase 3 Demo: Tamper-Evident Audit Logging")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)

        # 1. Generate keys
        private_path, public_path = generate_test_keys(temp_dir)

        # 2. Log events
        log_dir, run_id = demo_logging(temp_dir, private_path, public_path)

        # 3. Test secret redaction
        demo_secret_redaction(temp_dir, private_path, public_path)

        # 4. Verify logs
        demo_verification(log_dir, private_path, public_path)

        # 5. Test tamper detection
        demo_tamper_detection(log_dir, private_path, public_path)

        # 6. Test recovery
        demo_recovery(log_dir, private_path, public_path)

    print("\n" + "=" * 60)
    print("✅ Phase 3 Demo Complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
