# Phase 3 Implementation Plan: Logging & Audit Spine

**Status:** Steps 1-5 Complete ✅ | Steps 6-10 Pending
**Progress:** 98 tests passing, foundation complete
**Estimated Remaining:** ~1000 lines of code + ~150 tests

---

## Implementation Strategy

Phase 3 will be implemented in the following order to ensure each component builds correctly on the previous:

### Step 1: Complete All 26 Event Payload Schemas ✅ COMPLETE
**Files:** `schemas/audit_payloads/*.schema.json` (26 files)
**Time:** ~30 minutes
**Dependencies:** None

Create schema for each event type with:
- `additionalProperties: false`
- `required` keys only
- No floats, explicit max sizes
- No raw secrets

### Step 2: Canonicalization & Hashing Utilities ✅ COMPLETE
**File:** `audit_logging/canonicalize.py` (renamed from logging/)
**Time:** Completed
**Tests:** 28 tests passing
**Dependencies:** None

Implement:
- `canonical_json(obj) -> bytes` - UTF-8, sorted keys, no floats
- `compute_sha256_hex(data) -> str` - SHA-256 hex
- `compute_payload_hash(payload) -> str`
- `compute_event_hash(event_body) -> str`
- `compute_event_id(run_id, event_seq, event_type, actor, correlation, payload_hash) -> str`

**Tests:** 15 tests for canonicalization edge cases

### Step 3: Ed25519 Signing & Verification ✅ COMPLETE
**File:** `audit_logging/crypto.py`
**Time:** Completed
**Tests:** 20 tests passing
**Dependencies:** `cryptography ^46.0.0` (installed)

Implement:
- `load_private_key(path) -> Ed25519PrivateKey`
- `load_public_key(path) -> Ed25519PublicKey`
- `sign_event_hash(private_key, event_hash_hex) -> str` (base64)
- `verify_signature(public_key, event_hash_hex, signature_b64) -> bool`
- `compute_key_fingerprint(public_key_bytes) -> str`

**Tests:** 10 tests for signing/verification

### Step 4: Key Management with Permission Enforcement ✅ COMPLETE
**File:** `audit_logging/key_manager.py`
**Time:** Completed
**Tests:** 12 tests passing
**Dependencies:** Step 3 ✅

Implement:
- `KeyManager` class
- Load keys from `core.yaml` paths
- **Enforce 0600 permissions** on private key (halt if weaker)
- `LOG_KEY_PERMISSIONS_INVALID` error on permission failure

**Tests:** 8 tests for permission enforcement

### Step 5: Redaction Engine ✅ COMPLETE
**File:** `audit_logging/redaction.py`
**Time:** Completed
**Tests:** 38 tests passing (including adversarial tests)
**Dependencies:** None

Implement:
- `redact(obj) -> (redacted_obj, redacted_paths[])`
- Detect secret patterns (case-insensitive):
  - authorization, api_key, token, secret, password, cookie, private_key, etc.
  - Bearer tokens
  - Long base64 strings (>80 chars)
- Replace with literal `"REDACTED"`
- Return JSON pointer paths

**Tests:** 20 tests including adversarial secret injection

### Step 6: LogDaemon Write Path with Hash Chain
**File:** `logging/log_daemon.py`
**Time:** ~90 minutes
**Dependencies:** Steps 2, 3, 4, 5

Implement:
- `LogDaemon` class
- Ingest events via bounded queue
- Validate `event_type` against closed enum
- Redact + enforce (no secrets)
- Assign monotonic `event_seq`
- Compute hashes (payload_hash, event_hash, event_id)
- Build hash chain (`prev_event_hash`)
- Sign event
- Append JSONL line
- Flush after every event
- Fsync policy:
  - Every N events
  - Immediate for critical types

**Tests:** 25 tests for write path

### Step 7: Segment Rotation & Manifest Updates
**File:** `logging/rotation.py`
**Time:** ~60 minutes
**Dependencies:** Step 6

Implement:
- Monitor segment size
- Rotate when exceeds `max_segment_bytes`
- Segment naming: `audit.000001.jsonl`, `audit.000002.jsonl`
- Manifest update (atomic):
  - Write temp file
  - fsync
  - rename to `manifest.json`
- Track first/last event hashes per segment

**Tests:** 15 tests for rotation logic

### Step 8: Crash Recovery & Verifier
**Files:** `logging/recovery.py`, `logging/verifier.py`
**Time:** ~90 minutes
**Dependencies:** Steps 2, 3, 7

Implement **recovery.py**:
- Load manifest on startup
- Enumerate segments in order
- For last segment:
  - Read line-by-line
  - Parse JSON
  - Verify schema/fields
  - Verify hash chain
  - Verify signatures
- **Truncate back to last valid newline** if truncated
- Emit `LOG_CORRUPTION_DETECTED`
- **Halt if chain mismatch** (not just truncation)
- Emit `LOG_TAMPER_DETECTED`

Implement **verifier.py**:
- `verify_segment(segment_path, public_key) -> VerificationResult`
- `verify_chain(segments, manifest, public_key) -> VerificationResult`
- Prove:
  - Strict sequencing (`event_seq` monotonic)
  - Unbroken hash chain
  - Valid signatures for every event

**Tests:** 20 tests including tamper detection

### Step 9: Backpressure Ingress Protocol
**File:** `logging/ingress.py`
**Time:** ~45 minutes
**Dependencies:** Step 6

Implement:
- Bounded queue (`ingress_buffer_max_events`)
- Accept events via IPC (JSONL envelopes)
- If buffer full:
  - Return synchronous `LOG_BACKPRESSURE` response
  - Producer must block/retry
- Emit `PRODUCER_PROTOCOL_VIOLATION` if ignored

**Tests:** 12 tests for backpressure scenarios

### Step 10: Comprehensive Tests (Including Adversarial)
**Files:** `tests/test_logging_*.py` (multiple files)
**Time:** ~120 minutes
**Dependencies:** All previous steps

Implement adversarial test scenarios:
1. **Secret injection tests** - Verify redaction enforcement
2. **Event type validation** - Unknown types rejected
3. **Tamper detection** - Corrupt hash chain
4. **Signature verification** - Corrupt signature field
5. **Truncation recovery** - Truncate last line
6. **Backpressure tests** - Fill buffer, verify no drops
7. **Rotation tests** - Exceed size limit, verify manifest
8. **Key permissions tests** - Weaken permissions, verify halt
9. **Chain continuity tests** - Verify genesis value, strict monotonic
10. **Canonicalization tests** - Same input → same hashes

**Tests:** ~100 total Phase 3 tests

---

## Success Criteria (Hard Gates)

Phase 3 is complete ONLY if:

1. ✅ **Verifier proves integrity:**
   - Strict sequencing (`event_seq` monotonic)
   - Unbroken hash chain
   - Valid signatures for every event

2. ✅ **Rotation works correctly:**
   - Produces correct manifest updates
   - Segment boundary hashes accurate

3. ✅ **Crash recovery behaves correctly:**
   - Truncates only partial final lines
   - Emits `LOG_CORRUPTION_DETECTED` for truncation
   - Emits `LOG_TAMPER_DETECTED` and halts for chain mismatch

4. ✅ **Tamper detection works:**
   - Any chain mismatch produces `LOG_TAMPER_DETECTED`
   - System halts (does not proceed)

5. ✅ **Backpressure enforced:**
   - No silent drops
   - Synchronous `LOG_BACKPRESSURE` responses work

6. ✅ **Redaction prevents leaks:**
   - Secrets never persisted
   - Adversarial secret injection tests pass
   - All secret patterns caught

7. ✅ **Unknown types rejected:**
   - Event types not in closed enum rejected deterministically
   - System doesn't expand taxonomy at runtime

---

## File Structure (Complete)

```
/Users/ethanbrooks/dev/llm-relay/
├── schemas/
│   ├── relay.audit_event.schema.json ✅
│   ├── relay.audit_manifest.schema.json ✅
│   └── audit_payloads/
│       ├── RUN_STARTED.schema.json ⚠️  (needs module_versions)
│       ├── CONFIG_HASH_VERIFIED.schema.json ✅
│       ├── CONFIG_MISMATCH.schema.json
│       ├── PROCESS_STARTED.schema.json
│       ├── PROCESS_RESTARTED.schema.json
│       ├── PROCESS_HALTED.schema.json
│       ├── VALIDATION_STARTED.schema.json
│       ├── VALIDATION_PASSED.schema.json
│       ├── VALIDATION_FAILED.schema.json
│       ├── RBAC_DENIED.schema.json
│       ├── PATH_REJECTED.schema.json
│       ├── TASK_ENQUEUED.schema.json
│       ├── TASK_STARTED.schema.json
│       ├── SNAPSHOT_CREATED.schema.json
│       ├── SANDBOX_CREATED.schema.json
│       ├── HANDLER_STARTED.schema.json
│       ├── HANDLER_FINISHED.schema.json
│       ├── SANDBOX_DESTROYED.schema.json
│       ├── ROLLBACK_STARTED.schema.json
│       ├── ROLLBACK_FINISHED.schema.json
│       ├── TASK_FINISHED.schema.json
│       ├── TASK_REQUEUED.schema.json
│       ├── ENGINE_HALTED.schema.json
│       ├── LOG_CORRUPTION_DETECTED.schema.json
│       ├── LOG_TAMPER_DETECTED.schema.json
│       ├── LOG_BACKPRESSURE.schema.json
│       ├── PRODUCER_PROTOCOL_VIOLATION.schema.json
│       └── SECRET_REDACTED.schema.json
├── logging/
│   ├── __init__.py
│   ├── canonicalize.py (Step 2)
│   ├── crypto.py (Step 3)
│   ├── key_manager.py (Step 4)
│   ├── redaction.py (Step 5)
│   ├── log_daemon.py (Step 6)
│   ├── rotation.py (Step 7)
│   ├── recovery.py (Step 8)
│   ├── verifier.py (Step 8)
│   └── ingress.py (Step 9)
├── tests/
│   ├── test_canonicalize.py
│   ├── test_crypto.py
│   ├── test_key_manager.py
│   ├── test_redaction.py
│   ├── test_log_daemon.py
│   ├── test_rotation.py
│   ├── test_recovery.py
│   ├── test_verifier.py
│   ├── test_ingress.py
│   └── test_adversarial.py
└── logs/
    └── <run_id>/
        ├── manifest.json
        ├── audit.000001.jsonl
        ├── audit.000002.jsonl
        └── ...
```

---

## Configuration Requirements

Update `config/core.yaml` to include:

```yaml
logging:
  ed25519_private_key_path: "keys/audit_private.pem"
  ed25519_public_key_path: "keys/audit_public.pem"
  max_segment_bytes: 10485760  # 10MB
  fsync_every_n_events: 100
  ingress_buffer_max_events: 1000
  log_directory: "logs"
```

---

## Dependencies

Add to `pyproject.toml`:

```toml
[tool.poetry.dependencies]
cryptography = "^42.0.0"  # For Ed25519
```

---

## Estimated Timeline

- **Step 1 (Schemas):** 30 minutes
- **Step 2 (Canonicalization):** 45 minutes
- **Step 3 (Ed25519):** 45 minutes
- **Step 4 (Key Manager):** 30 minutes
- **Step 5 (Redaction):** 60 minutes
- **Step 6 (LogDaemon):** 90 minutes
- **Step 7 (Rotation):** 60 minutes
- **Step 8 (Recovery/Verifier):** 90 minutes
- **Step 9 (Backpressure):** 45 minutes
- **Step 10 (Tests):** 120 minutes

**Total:** ~8-10 hours of focused implementation

---

## Next Action

Awaiting your approval to proceed with full Phase 3 implementation following this plan.

Shall I:
- **Option A:** Proceed with complete Phase 3 implementation (all 10 steps)
- **Option B:** Implement Steps 1-5 first (schemas + crypto foundation), pause for review
- **Option C:** Create minimal MVP for demonstration, then expand

**Recommended:** Option B (foundation first, then review)
