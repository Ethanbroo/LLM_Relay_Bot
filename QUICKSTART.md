# Quick Start Guide

Get started with the LLM Relay Phase 1 validation pipeline in 5 minutes.

## Prerequisites

- Python 3.12 (recommended) or 3.11
- Poetry installed (will auto-install if missing)

## Installation

```bash
# Navigate to project
cd /Users/ethanbrooks/dev/llm-relay

# Ensure Poetry is in PATH
export PATH="$HOME/.local/bin:$PATH"

# Install dependencies (already done if you see this file)
poetry install
```

## Quick Test

```bash
# Run the demo
poetry run python demo.py

# Run tests
poetry run pytest tests/ -v

# Check coverage
poetry run pytest tests/ --cov=validator --cov-report=term
```

## Using the Validation Pipeline

### Python API

```python
from validator.pipeline import ValidationPipeline

# Initialize pipeline
pipeline = ValidationPipeline(base_dir=".")

# Create an envelope
envelope = {
    "envelope_version": "1.0.0",
    "message_id": "01234567-89ab-7def-8123-456789abcdef",  # UUID v7
    "timestamp": "2026-02-07T12:00:00Z",
    "sender": "validator",
    "recipient": "executor",
    "action": "fs.read",
    "action_version": "1.0.0",
    "payload": {
        "path": "data/example.txt",
        "offset": 0,
        "length": 1024,
        "encoding": "utf-8"
    }
}

# Validate
result = pipeline.validate(envelope)

# Check result
if "validation_id" in result:
    print("✅ Validation passed!")
    print(f"Schema hash: {result['schema_hash']}")
    print(f"RBAC rule: {result['rbac_rule_id']}")
    print(f"Sanitized payload: {result['sanitized_payload']}")
else:
    print("❌ Validation failed!")
    print(f"Error: {result['error_code']}")
    print(f"Stage: {result['stage']}")
    print(f"Message: {result['message']}")
```

### Supported Actions

#### 1. fs.read - Read file from workspace

```python
payload = {
    "path": "data/file.txt",  # Required: relative path
    "offset": 0,              # Optional: byte offset (default: 0)
    "length": 4096,           # Optional: bytes to read (default: 1MB)
    "encoding": "utf-8"       # Optional: utf-8|ascii|binary (default: utf-8)
}
```

#### 2. fs.list_dir - List directory contents

```python
payload = {
    "path": "data",                    # Required: directory path
    "max_entries": 100,                # Optional: max results (default: 100)
    "sort_order": "name_asc",          # Optional: name_asc|name_desc|mtime_asc|mtime_desc
    "include_hidden": false,           # Optional: include . files (default: false)
    "recursive": false                 # Optional: recurse subdirs (default: false)
}
```

#### 3. system.health_ping - Health check

```python
payload = {
    "echo": "hello"  # Optional: string to echo back
}
```

## Configuration

### Core Settings (`config/core.yaml`)

- **Time policy**: "recorded" (timestamp captured at ingress)
- **IPC limits**: 1MB max message, 100 msg/sec
- **Sandbox**: Network disabled, workspace-only access
- **Validation**: Strict mode, no unknown fields

### RBAC Policy (`config/policy.yaml`)

Default role: `executor` (granted to `validator` principal)

**Allowed:**
- `fs.read` on `/workspace/**`
- `fs.list_dir` on `/workspace/**`
- `system.health_ping` on any resource

**Denied:**
- All access to `/**/.git/**` (Git internals)
- All access to `/**/.env` (environment files)
- All access to `**/config/*.yaml` (config files)
- All unlisted actions (deny-by-default)

## Validation Flow

```
Envelope Input
    ↓
1. Envelope Validation (JSON Schema + Pydantic)
    ↓
2. Schema Selection (from registry)
    ↓
3. JSON Schema Validation (draft 2020-12)
    ↓
4. Pydantic Validation (strict types + custom validators)
    ↓
5. RBAC Check (deny-by-default)
    ↓
6. Sanitization (Unicode NFC, reject dangerous input)
    ↓
7. Audit Log (JSONL events)
    ↓
8. Output: ValidatedAction OR Error
```

## Common Error Codes

| Error Code | Stage | Meaning |
|------------|-------|---------|
| `ENVELOPE_INVALID` | envelope_validation | Envelope structure invalid |
| `SCHEMA_NOT_FOUND` | schema_selection | Unknown action or version |
| `JSON_SCHEMA_FAILED` | json_schema_validation | Payload doesn't match schema |
| `PYDANTIC_FAILED` | pydantic_validation | Type/validation error |
| `RBAC_DENIED` | rbac_check | Access denied by policy |
| `SANITIZATION_FAILED` | sanitization | Dangerous input detected |

## Audit Log

All validation attempts are logged to `/tmp/llm-relay-audit.jsonl`:

```bash
# View recent events
cat /tmp/llm-relay-audit.jsonl | tail -20 | jq

# Filter by stage
cat /tmp/llm-relay-audit.jsonl | jq 'select(.stage == "rbac_check")'

# Count failures
cat /tmp/llm-relay-audit.jsonl | jq 'select(.result == "fail")' | wc -l
```

## Testing

```bash
# Run all tests
poetry run pytest tests/ -v

# Run specific test file
poetry run pytest tests/test_pipeline_integration.py -v

# Run with coverage
poetry run pytest tests/ --cov=validator --cov-report=html
open htmlcov/index.html

# Run single test
poetry run pytest tests/test_rbac.py::test_allow_fs_read_in_workspace -v
```

## Project Structure

```
llm-relay/
├── schemas/              # JSON schemas for all message types
│   ├── envelope.schema.json
│   ├── validated_action.schema.json
│   ├── error.schema.json
│   ├── audit_event.schema.json
│   └── actions/          # Action schemas (versioned)
│       ├── fs.read/
│       ├── fs.list_dir/
│       └── system.health_ping/
├── validator/            # Validation pipeline modules
│   ├── pipeline.py       # Main validation pipeline
│   ├── schema_registry.py
│   ├── jsonschema_validate.py
│   ├── pydantic_validate.py
│   ├── rbac.py
│   ├── sanitize.py
│   ├── audit.py
│   ├── canonicalize.py
│   ├── time_policy.py
│   └── pydantic_models/  # Pydantic models for actions
├── config/               # Configuration files
│   ├── core.yaml         # Core system config
│   ├── policy.yaml       # RBAC policies
│   └── schema_registry_index.json
├── tests/                # Test suite (62 tests, 83% coverage)
├── examples/             # Example envelopes
├── demo.py              # Interactive demo
└── README.md            # Full documentation
```

## What's NOT in Phase 1

Phase 1 is **validation only**. It does NOT:
- Execute actions (Phase 2)
- Connect to LLMs (Phase 6)
- Connect to external APIs (Phase 5)
- Sign audit logs (Phase 3)
- Process splits into multiple processes (Phase 0/2)

## Getting Help

1. **Read full documentation**: `README.md`
2. **Check completion report**: `PHASE1_COMPLETE.md`
3. **Run the demo**: `poetry run python demo.py`
4. **Browse examples**: `examples/*.json`
5. **Read tests**: `tests/test_*.py` - tests serve as documentation

## Next Steps

Once you've validated Phase 1 works:

1. Review [PHASE1_COMPLETE.md](PHASE1_COMPLETE.md) for full feature list
2. Experiment with examples in `examples/`
3. Try creating custom envelopes
4. Review test suite to understand edge cases
5. Proceed to Phase 2 implementation

---

**Questions?** Phase 1 is complete and stable. Review the tests and demo to understand all features.
