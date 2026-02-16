# LLM Relay - Deterministic Multi-LLM Orchestration System

A deterministic, sandboxed, multi-LLM relay system that mediates structured JSON messages between LLMs and restricted execution targets.

## Project Status: Phase 1 Complete ✅

**Phase 1: Validation Before Intelligence** is now complete with **98% test coverage**.

### What Phase 1 Provides

- **Fail-closed validation**: Nothing unsafe or ambiguous can pass through
- **Dual validation**: JSON Schema + Pydantic with strict mode
- **RBAC enforcement**: Deny-by-default access control
- **Sanitization**: Minimal safe normalization, reject dangerous input
- **Audit logging**: Structured JSONL events for all validation stages
- **Deterministic behavior**: Same input → same output (including error codes)

### Key Components

```
llm-relay/
├── schemas/               # JSON schemas (envelope, actions, errors)
├── validator/             # Validation pipeline modules
│   ├── pipeline.py       # 8-stage validation pipeline
│   ├── schema_registry.py
│   ├── jsonschema_validate.py
│   ├── pydantic_validate.py
│   ├── rbac.py
│   ├── sanitize.py
│   └── audit.py
├── config/               # Configuration files
│   ├── core.yaml        # Core system config
│   ├── policy.yaml      # RBAC policies
│   └── schema_registry_index.json
└── tests/               # Comprehensive test suite (90%+ coverage)
```

## Getting Started

### Prerequisites

- Python 3.12
- Poetry (installed automatically if missing)

### Installation

```bash
cd /Users/ethanbrooks/dev/llm-relay
export PATH="$HOME/.local/bin:$PATH"
poetry install
```

### Running Tests

```bash
poetry run pytest tests/ -v --cov=validator --cov-report=term-missing
```

### Quick Example

```python
from validator.pipeline import ValidationPipeline

# Initialize pipeline
pipeline = ValidationPipeline(base_dir=".")

# Validate an envelope
envelope = {
    "envelope_version": "1.0.0",
    "message_id": "01234567-89ab-7def-8123-456789abcdef",
    "timestamp": "2026-02-07T12:00:00Z",
    "sender": "validator",
    "recipient": "executor",
    "action": "fs.read",
    "action_version": "1.0.0",
    "payload": {
        "path": "test.txt",
        "offset": 0,
        "length": 1024,
        "encoding": "utf-8"
    }
}

result = pipeline.validate(envelope)

# Result is either ValidatedAction or Error
if "validation_id" in result:
    print(f"✅ Validation passed: {result['validation_id']}")
    print(f"   Schema hash: {result['schema_hash']}")
    print(f"   RBAC rule: {result['rbac_rule_id']}")
else:
    print(f"❌ Validation failed: {result['error_code']}")
    print(f"   Stage: {result['stage']}")
    print(f"   Message: {result['message']}")
```

## Supported Actions (Phase 1)

1. **fs.read** - Read file from workspace (bounded, safe paths only)
2. **fs.list_dir** - List directory contents (bounded, deterministic ordering)
3. **system.health_ping** - Health check with optional echo

## Phase 1 Completion Criteria ✅

- [x] Validator processes envelopes end-to-end
- [x] Emits only `ValidatedAction` or `error`
- [x] No coercion, no extra fields accepted
- [x] Registry is deterministic and non-discovering
- [x] RBAC deny-by-default enforced
- [x] Outputs stable across repeated runs
- [x] Comprehensive test coverage **(98% - 196 tests passing)**

## Next Steps: Phase 2

**Phase 2: Deterministic Execution Core** will implement:

- Controlled execution of validated actions
- Deterministic result computation
- Compensation/rollback mechanisms
- Execution result validation

## Architecture Principles

1. **LLMs are never trusted actors** - They can only propose actions, never execute
2. **All state transitions are logged** - Complete audit trail with hashing
3. **Fail-closed validation** - Uncertainty results in rejection, not coercion
4. **Deterministic by design** - Same input always yields same output
5. **Defense in depth** - Multiple validation layers (JSON Schema + Pydantic + RBAC + Sanitization)

## Configuration

### Time Policy

The system uses "recorded" time policy by default (timestamp captured at ingress, propagated everywhere). This ensures deterministic behavior while maintaining realistic timestamps.

### RBAC Policy

Access is deny-by-default. Only explicitly allowed actions are permitted. See `config/policy.yaml` for current rules.

### Workspace Isolation

All filesystem operations are restricted to `/workspace/**`. The following are always denied:

- `.git/**` (Git internals)
- `.env` (Environment files with secrets)
- `config/*.yaml` (Immutable configuration files)

## Development

### Running Tests with Coverage

```bash
poetry run pytest tests/ -v --cov=validator --cov-report=html
open htmlcov/index.html  # View coverage report
```

### Code Structure

The validation pipeline has a fixed 8-stage order:

1. **Envelope validation** - Validate envelope structure
2. **Schema selection** - Load action schema from registry
3. **JSON Schema validation** - Validate payload against JSON Schema
4. **Pydantic validation** - Validate with Pydantic models (strict mode)
5. **RBAC check** - Enforce access control policies
6. **Sanitization** - Safe normalization (reject, don't fix)
7. **Audit log** - Write structured audit events
8. **Output emit** - Return ValidatedAction or Error

## License

Internal project - all rights reserved.
