"""
Canonicalization and hashing utilities for Phase 3 audit logging.

Provides deterministic JSON canonicalization and hashing functions for:
- Payload hash computation
- Event hash computation
- Event ID generation

All functions produce stable, deterministic outputs for identical inputs.
"""

import json
import hashlib
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """
    Convert Python object to canonical JSON bytes.

    Canonical form ensures:
    - Keys sorted alphabetically
    - No whitespace (compact separators)
    - UTF-8 encoding
    - No floating point numbers (must use integers or strings)
    - ensure_ascii=False (preserve Unicode)

    Args:
        obj: Python object (dict, list, str, int, bool, None)

    Returns:
        UTF-8 encoded bytes of canonical JSON

    Raises:
        TypeError: If obj contains floats or non-JSON-serializable types
    """
    # Check for floats recursively
    _check_no_floats(obj)

    json_str = json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
        allow_nan=False
    )
    return json_str.encode('utf-8')


def _check_no_floats(obj: Any) -> None:
    """
    Recursively check that object contains no floats.

    Raises:
        TypeError: If any float found
    """
    if isinstance(obj, float):
        raise TypeError(f"Floats not allowed in canonical JSON: {obj}")
    elif isinstance(obj, dict):
        for value in obj.values():
            _check_no_floats(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _check_no_floats(item)


def compute_sha256_hex(data: bytes) -> str:
    """
    Compute SHA-256 hash of bytes and return as lowercase hex string.

    Args:
        data: Bytes to hash

    Returns:
        64-character lowercase hex string
    """
    return hashlib.sha256(data).hexdigest()


def compute_payload_hash(payload: dict) -> str:
    """
    Compute deterministic hash of event payload.

    payload_hash = SHA-256(canonical_json(payload))

    Args:
        payload: Event payload dictionary (after redaction)

    Returns:
        64-character lowercase hex string
    """
    canonical = canonical_json(payload)
    return compute_sha256_hex(canonical)


def compute_event_hash(event_body: dict) -> str:
    """
    Compute deterministic hash of event body (excludes signature and event_hash).

    Event body includes:
    - schema_id
    - schema_version
    - run_id
    - event_seq
    - event_id
    - event_type
    - timestamp
    - actor
    - correlation
    - payload
    - payload_hash
    - prev_event_hash
    - redaction

    Does NOT include:
    - event_hash (self-referential)
    - signature (computed from event_hash)

    Args:
        event_body: Event dictionary without event_hash and signature fields

    Returns:
        64-character lowercase hex string
    """
    # Create copy without event_hash and signature
    body_for_hash = {k: v for k, v in event_body.items()
                     if k not in ('event_hash', 'signature')}

    canonical = canonical_json(body_for_hash)
    return compute_sha256_hex(canonical)


def compute_event_id(
    run_id: str,
    event_seq: int,
    event_type: str,
    actor: str,
    correlation: dict,
    payload_hash: str
) -> str:
    """
    Compute deterministic event ID.

    event_id = SHA-256(run_id || event_seq || event_type || actor ||
                       canonical_json(correlation) || payload_hash)

    Args:
        run_id: UUID of current run
        event_seq: Monotonic sequence number
        event_type: Event type enum value
        actor: Module/component name
        correlation: Correlation identifiers dict
        payload_hash: SHA-256 hash of payload

    Returns:
        64-character lowercase hex string
    """
    # Concatenate components with canonical correlation
    correlation_canonical = canonical_json(correlation)

    # Build deterministic string
    components = (
        f"{run_id}|"
        f"{event_seq}|"
        f"{event_type}|"
        f"{actor}|"
        f"{correlation_canonical.decode('utf-8')}|"
        f"{payload_hash}"
    )

    return compute_sha256_hex(components.encode('utf-8'))
