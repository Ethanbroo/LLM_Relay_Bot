# Phases 1-3 Integration Quick Start

Get the integrated LLM Relay system running in 5 minutes.

---

## Prerequisites

- Python 3.12+
- Poetry (dependency management)
- OpenSSL (for key generation)

---

## Step 1: Install Dependencies

```bash
cd /Users/ethanbrooks/dev/llm-relay
poetry install
```

---

## Step 2: Verify Keys Exist

Ed25519 keypair should already be generated:

```bash
ls -la keys/
# Should show:
# -rw------- audit_private.pem (600 permissions - MANDATORY)
# -rw-r--r-- audit_public.pem (644 permissions)
```

If keys don't exist, generate them:

```bash
mkdir -p keys
openssl genpkey -algorithm ED25519 -out keys/audit_private.pem
openssl pkey -in keys/audit_private.pem -pubout -out keys/audit_public.pem
chmod 0600 keys/audit_private.pem
chmod 0644 keys/audit_public.pem
```

---

## Step 3: Run the Demo

```bash
poetry run python demo_integration.py
```

**Expected output:**
```
✅ Supervisor initialized
   Run ID: <uuid>
   Config hash: <hash>...

📋 Phase 1: Validation Pipeline
   ✅ Validation passed

⚙️  Phase 2: Execution Engine
   ✅ Execution completed

🔒 Phase 3: Audit Log Verification
   ✅ Audit log verified
      Events verified: 23
      Hash chain: intact
      Signatures: valid
```

---

## Step 4: Explore the Audit Log

```bash
# View audit events
cat logs/audit.000001.jsonl | jq .

# View manifest
cat logs/manifest.json | jq .

# Check specific event types
cat logs/audit.000001.jsonl | jq 'select(.event_type == "TASK_STARTED")'
```

---

## Step 5: Run Tests

```bash
# Run all tests (447 tests)
poetry run pytest tests/ -v

# Run only integration tests (8 tests)
poetry run pytest tests/test_integration_phases_1_2_3.py -v

# Run with coverage
poetry run pytest tests/ --cov=. --cov-report=html
```

---

## Usage: Python API

### Basic Usage

```python
from supervisor import LLMRelaySupervisor

# Initialize supervisor
with LLMRelaySupervisor() as supervisor:
    # Create envelope
    envelope = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-0123-456789abcdef",
        "sender": "validator",
        "recipient": "executor",
        "timestamp": "2026-02-08T10:00:00Z",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {}
    }

    # Phase 1: Validate
    result = supervisor.process_envelope(envelope)

    if "validation_id" in result:
        # Validation passed
        print(f"Task ID: {result['task_id']}")

        # Phase 2: Execute
        execution_results = supervisor.execute_pending_tasks()

        for exec_result in execution_results:
            print(f"Status: {exec_result['status']}")
    else:
        # Validation failed
        print(f"Error: {result['error_code']}")
```

### Advanced: Custom Configuration

```python
from supervisor import LLMRelaySupervisor

# Use custom config paths
supervisor = LLMRelaySupervisor(
    config_path="config/core.yaml",
    policy_path="config/policy.yaml",
    base_dir="/path/to/project"
)

# Process envelope
result = supervisor.process_envelope(envelope)

# Cleanup
supervisor.shutdown()
```

---

## Verify Audit Log Integrity

```python
from pathlib import Path
from audit_logging.verifier import AuditLogVerifier
from audit_logging.key_manager import KeyManager

# Load keys
key_manager = KeyManager(
    private_key_path="keys/audit_private.pem",
    public_key_path="keys/audit_public.pem"
)

# Verify segment
verifier = AuditLogVerifier(key_manager)
segment_path = Path("logs/audit.000001.jsonl")
result = verifier.verify_segment(segment_path)

if result.success:
    print(f"✅ Verified {result.events_verified} events")
else:
    print(f"❌ Verification failed!")
    for error in result.errors:
        print(f"   {error}")
```

---

## Configuration

### Core Configuration (`config/core.yaml`)

```yaml
time_policy:
  mode: "recorded"  # or "frozen" for testing

audit:
  format: "jsonl"
  sink: "logdaemon"
  log_directory: "logs"
  ed25519_private_key_path: "keys/audit_private.pem"
  ed25519_public_key_path: "keys/audit_public.pem"
  max_segment_bytes: 10485760  # 10MB
  fsync_every_n_events: 100
  ingress_buffer_max_events: 1000
  signature_required: true
```

### RBAC Policy (`config/policy.yaml`)

```yaml
roles:
  executor:
    allow:
      - action: "system.health_ping"
        resource: "*"
        rule_id: "executor.system.health_ping.any"

principals:
  validator:
    roles: ["executor"]
```

---

## Troubleshooting

### Error: "Ed25519 private key not found"

**Solution:** Generate keys (see Step 2)

### Error: "KeyPermissionError"

**Problem:** Private key doesn't have 0600 permissions

**Solution:**
```bash
chmod 0600 keys/audit_private.pem
```

### Error: "RBAC_DENIED"

**Problem:** Principal not authorized in `policy.yaml`

**Solution:** Add principal to `config/policy.yaml`:
```yaml
principals:
  my_principal:
    roles: ["executor"]
```

### Tests Failing

**Problem:** Missing dependencies or stale cache

**Solution:**
```bash
poetry install
poetry run pytest tests/ --cache-clear
```

---

## File Locations

```
/Users/ethanbrooks/dev/llm-relay/
├── config/
│   ├── core.yaml              # System configuration
│   └── policy.yaml            # RBAC policy
├── keys/
│   ├── audit_private.pem      # Ed25519 private key (0600)
│   └── audit_public.pem       # Ed25519 public key (0644)
├── logs/
│   ├── audit.000001.jsonl     # Audit log segment
│   └── manifest.json          # Segment metadata
├── supervisor.py              # Unified supervisor
├── demo_integration.py        # Integration demo
└── tests/
    └── test_integration_phases_1_2_3.py  # Integration tests
```

---

## Next Steps

1. **Explore the code:**
   - `supervisor.py` - Entry point
   - `validator/pipeline.py` - Phase 1
   - `executor/engine.py` - Phase 2
   - `audit_logging/log_daemon.py` - Phase 3

2. **Read documentation:**
   - `PHASE123_INTEGRATION_COMPLETE.md` - Full integration details
   - `PHASE1_FINAL_SUMMARY.md` - Phase 1 details
   - `PHASE2_COMPLETE.md` - Phase 2 details
   - `PHASE3_FINAL_SUMMARY.md` - Phase 3 details

3. **Run your own tests:**
   - Create custom envelopes
   - Test different actions
   - Explore audit log events

4. **Deploy:**
   - Update configuration for production
   - Set up log rotation monitoring
   - Configure backup/archival

---

## Support

- Documentation: See `*.md` files in project root
- Tests: `tests/` directory has comprehensive examples
- Demo: `demo_integration.py` shows full workflow

---

**Quick Start Version:** 1.0.0
**Last Updated:** 2026-02-08
