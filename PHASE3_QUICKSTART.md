# Phase 3 Quick Start Guide

This guide shows you how to use the Phase 3 audit logging system.

---

## 1. Generate Ed25519 Keypair

First, generate cryptographic keys for signing audit events:

```bash
# Create keys directory
mkdir -p /Users/ethanbrooks/dev/llm-relay/keys

# Generate private key
openssl genpkey -algorithm ED25519 -out keys/audit_private.pem

# Extract public key
openssl pkey -in keys/audit_private.pem -pubout -out keys/audit_public.pem

# Set correct permissions (MANDATORY - system will halt if incorrect)
chmod 0600 keys/audit_private.pem
chmod 0644 keys/audit_public.pem
```

**⚠️ CRITICAL:** The private key MUST have 0600 permissions. The system enforces this and will halt with `LOG_KEY_PERMISSIONS_INVALID` if permissions are weaker.

---

## 2. Update Configuration

Add logging configuration to `config/core.yaml`:

```yaml
logging:
  ed25519_private_key_path: "keys/audit_private.pem"
  ed25519_public_key_path: "keys/audit_public.pem"
  max_segment_bytes: 10485760  # 10MB per segment
  fsync_every_n_events: 100     # Fsync after 100 events
  ingress_buffer_max_events: 1000
  log_directory: "logs"
```

---

## 3. Basic Usage

### Initialize Logging System

```python
import uuid
from audit_logging.key_manager import KeyManager
from audit_logging.log_daemon import LogDaemon

# Load keys
key_manager = KeyManager(
    private_key_path="keys/audit_private.pem",
    public_key_path="keys/audit_public.pem",
    enforce_permissions=True
)

# Create log daemon
daemon = LogDaemon(
    run_id=str(uuid.uuid4()),
    config_hash="a" * 64,  # SHA-256 of your core.yaml
    time_policy="recorded",  # or "frozen"
    key_manager=key_manager,
    log_directory="logs",
    fsync_every_n_events=100
)
```

### Log Events

```python
# Log a task started event
event = daemon.ingest_event(
    event_type="TASK_STARTED",
    actor="executor",
    correlation={
        "session_id": "session-123",
        "message_id": "msg-456",
        "task_id": "task-789"
    },
    payload={
        "task_id": "task-789",
        "attempt": 0
    }
)

print(f"Event logged: seq={event['event_seq']}, hash={event['event_hash'][:16]}...")
```

### Close Daemon

```python
# Close and flush all pending writes
daemon.close()
```

### Using Context Manager (Recommended)

```python
with LogDaemon(
    run_id=str(uuid.uuid4()),
    config_hash="a" * 64,
    time_policy="recorded",
    key_manager=key_manager,
    log_directory="logs"
) as daemon:
    # Log events
    daemon.ingest_event(
        event_type="RUN_STARTED",
        actor="supervisor",
        correlation={"session_id": None, "message_id": None, "task_id": None},
        payload={
            "run_id": daemon.run_id,
            "config_hash": daemon.config_hash,
            "time_policy": "recorded",
            "module_versions": {"validator": "1.0.0", "executor": "1.0.0"}
        }
    )
    # ... more events ...
# File automatically closed and fsynced
```

---

## 4. Secret Redaction

The system automatically redacts secrets from event payloads:

```python
# This payload contains a secret (password field)
event = daemon.ingest_event(
    event_type="VALIDATION_FAILED",
    actor="validator",
    correlation={"session_id": None, "message_id": None, "task_id": None},
    payload={
        "validation_id": "v1",
        "error_code": "AUTH_FAILED",
        "stage": "rbac",
        "password": "secret123"  # Will be redacted!
    }
)

# Check redaction
assert event["payload"]["password"] == "REDACTED"
assert event["redaction"]["was_redacted"] is True
assert "/password" in event["redaction"]["redacted_paths"]
```

**Automatically redacted patterns:**
- Field names: password, api_key, token, secret, authorization, cookie, credential
- Bearer tokens in strings: "Authorization: Bearer abc123"
- Long base64 strings (>80 characters)

---

## 5. Verify Audit Logs

Verify the integrity of audit logs:

```python
from audit_logging.verifier import AuditLogVerifier
from audit_logging.key_manager import KeyManager
from pathlib import Path

# Load public key for verification
key_manager = KeyManager(
    private_key_path="keys/audit_private.pem",
    public_key_path="keys/audit_public.pem"
)

# Create verifier
verifier = AuditLogVerifier(key_manager)

# Verify a segment
segment_path = Path("logs/audit.000001.jsonl")
result = verifier.verify_segment(segment_path)

if result.success:
    print(f"✅ Verified {result.events_verified} events")
    print(f"   Hash chain intact")
    print(f"   All signatures valid")
else:
    print(f"❌ Verification failed!")
    print(f"   Errors: {result.errors}")
    if result.tamper_detected:
        print(f"   ⚠️  TAMPERING DETECTED!")
```

---

## 6. Crash Recovery

On system startup, run crash recovery:

```python
from audit_logging.recovery import CrashRecoveryManager
from audit_logging.key_manager import KeyManager

# Load keys
key_manager = KeyManager(
    private_key_path="keys/audit_private.pem",
    public_key_path="keys/audit_public.pem"
)

# Create recovery manager
recovery_mgr = CrashRecoveryManager(
    log_directory="logs",
    key_manager=key_manager
)

# Recover
try:
    result = recovery_mgr.recover()

    if result.success:
        print(f"✅ Recovery successful")
        print(f"   Last valid event: seq={result.last_valid_event_seq}")

        if result.corruption_detected:
            print(f"   ⚠️  Corruption detected: {result.truncated_lines} lines truncated")
            # Emit LOG_CORRUPTION_DETECTED event

        if result.tamper_detected:
            print(f"   🚨 TAMPERING DETECTED - SYSTEM HALTED")
            # Emit LOG_TAMPER_DETECTED and halt
            raise SystemExit(1)
    else:
        print(f"❌ Recovery failed: {result.error_message}")
        raise SystemExit(1)

except TamperDetectedError as e:
    print(f"🚨 TAMPERING DETECTED: {e}")
    # System must halt - do not proceed
    raise SystemExit(1)
```

---

## 7. Read Audit Logs

Audit logs are stored as JSONL (JSON Lines):

```python
import json
from pathlib import Path

# Read all events from a segment
segment_path = Path("logs/audit.000001.jsonl")

with open(segment_path, 'r') as f:
    for line in f:
        event = json.loads(line)

        print(f"Event {event['event_seq']}: {event['event_type']}")
        print(f"  Actor: {event['actor']}")
        print(f"  Timestamp: {event['timestamp']}")
        print(f"  Hash: {event['event_hash'][:16]}...")
        print(f"  Signature: {event['signature'][:16]}...")
        print()
```

---

## 8. Segment Rotation

Segments automatically rotate when they reach `max_segment_bytes`:

```bash
logs/
├── manifest.json        # Manifest with segment metadata
├── audit.000001.jsonl   # First segment (10MB)
├── audit.000002.jsonl   # Second segment (10MB)
└── audit.000003.jsonl   # Current segment (growing)
```

The manifest tracks all segments:

```json
{
  "schema_id": "relay.audit_manifest",
  "schema_version": "1.0.0",
  "run_id": "abc-123",
  "config_hash": "a...a",
  "segments": [
    {
      "filename": "audit.000001.jsonl",
      "first_event_seq": 1,
      "last_event_seq": 1000,
      "first_event_hash": "abc...",
      "last_event_hash": "def...",
      "byte_length": 10485760
    }
  ],
  "event_count_total": 1000,
  "first_event_hash": "abc...",
  "last_event_hash": "def..."
}
```

---

## 9. Valid Event Types (Closed Enum)

The system only accepts these 26 event types:

```python
VALID_EVENT_TYPES = {
    "RUN_STARTED",
    "CONFIG_HASH_VERIFIED",
    "CONFIG_MISMATCH",
    "PROCESS_STARTED",
    "PROCESS_RESTARTED",
    "PROCESS_HALTED",
    "VALIDATION_STARTED",
    "VALIDATION_PASSED",
    "VALIDATION_FAILED",
    "RBAC_DENIED",
    "PATH_REJECTED",
    "TASK_ENQUEUED",
    "TASK_STARTED",
    "SNAPSHOT_CREATED",
    "SANDBOX_CREATED",
    "HANDLER_STARTED",
    "HANDLER_FINISHED",
    "SANDBOX_DESTROYED",
    "ROLLBACK_STARTED",
    "ROLLBACK_FINISHED",
    "TASK_FINISHED",
    "TASK_REQUEUED",
    "ENGINE_HALTED",
    "LOG_CORRUPTION_DETECTED",
    "LOG_TAMPER_DETECTED",
    "LOG_BACKPRESSURE",
    "PRODUCER_PROTOCOL_VIOLATION",
    "SECRET_REDACTED",
}
```

Unknown event types are rejected with `InvalidEventTypeError`.

---

## 10. Security Properties

The audit logging system provides:

✅ **Non-repudiation** - Ed25519 signatures prove event authenticity
✅ **Tamper evidence** - Hash chain detects any modification
✅ **Integrity** - Deterministic hashes and event IDs
✅ **Confidentiality** - Automatic secret redaction
✅ **Availability** - Backpressure prevents event loss
✅ **Auditability** - Complete chronological event history

---

## 11. Troubleshooting

### Error: LOG_KEY_PERMISSIONS_INVALID

**Problem:** Private key has incorrect permissions

**Solution:**
```bash
chmod 0600 keys/audit_private.pem
```

### Error: InvalidEventTypeError

**Problem:** Unknown event type used

**Solution:** Use only the 26 valid event types from the closed enum

### Error: SecretLeakError

**Problem:** Secret detected but not properly redacted

**Solution:** This should never happen - it's a safety check. Report as bug.

### Verification fails with "Hash chain mismatch"

**Problem:** Tampering detected

**Solution:**
- System MUST halt - do not proceed
- Emit LOG_TAMPER_DETECTED event
- Investigate security incident

---

## 12. Example: Complete Workflow

```python
import uuid
from audit_logging.key_manager import KeyManager
from audit_logging.log_daemon import LogDaemon

# 1. Load keys
key_manager = KeyManager(
    private_key_path="keys/audit_private.pem",
    public_key_path="keys/audit_public.pem"
)

# 2. Create daemon
with LogDaemon(
    run_id=str(uuid.uuid4()),
    config_hash="a" * 64,
    time_policy="recorded",
    key_manager=key_manager,
    log_directory="logs"
) as daemon:

    # 3. Log run started
    daemon.ingest_event(
        event_type="RUN_STARTED",
        actor="supervisor",
        correlation={"session_id": None, "message_id": None, "task_id": None},
        payload={
            "run_id": daemon.run_id,
            "config_hash": "a" * 64,
            "time_policy": "recorded",
            "module_versions": {}
        }
    )

    # 4. Log task events
    daemon.ingest_event(
        event_type="TASK_ENQUEUED",
        actor="executor",
        correlation={"session_id": "s1", "message_id": "m1", "task_id": "t1"},
        payload={"task_id": "t1", "enqueue_seq": 1}
    )

    daemon.ingest_event(
        event_type="TASK_STARTED",
        actor="executor",
        correlation={"session_id": "s1", "message_id": "m1", "task_id": "t1"},
        payload={"task_id": "t1", "attempt": 0}
    )

    daemon.ingest_event(
        event_type="TASK_FINISHED",
        actor="executor",
        correlation={"session_id": "s1", "message_id": "m1", "task_id": "t1"},
        payload={"task_id": "t1", "attempt": 0, "success": True}
    )

print("✅ All events logged and verified!")
```

---

## Next Steps

- Review [PHASE3_COMPLETE.md](PHASE3_COMPLETE.md) for full system documentation
- Generate your production keypair
- Integrate with your application
- Set up log rotation monitoring
- Configure log backup/archival
