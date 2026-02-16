"""
Canonical JSON serialization and hashing utilities.

Ensures deterministic, stable JSON representation for schema hashing
and message integrity verification.
"""

import json
import hashlib
from typing import Any


def canonicalize_json(obj: Any) -> str:
    """
    Convert a Python object to canonical JSON string.

    Rules:
    - Keys sorted alphabetically
    - No whitespace (compact)
    - UTF-8 encoding
    - Deterministic float representation
    - No trailing newline

    Args:
        obj: Python object (dict, list, etc.)

    Returns:
        Canonical JSON string

    Raises:
        TypeError: If object is not JSON-serializable
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,  # Reject NaN, Infinity (non-deterministic)
    )


def compute_sha256(data: str | bytes) -> str:
    """
    Compute SHA-256 hash of data.

    Args:
        data: String or bytes to hash

    Returns:
        Hex-encoded SHA-256 hash (64 lowercase hex chars)
    """
    if isinstance(data, str):
        data = data.encode('utf-8')

    return hashlib.sha256(data).hexdigest()


def compute_schema_hash(schema: dict) -> str:
    """
    Compute deterministic hash of a JSON schema.

    Args:
        schema: JSON schema as dict

    Returns:
        SHA-256 hash of canonical schema representation
    """
    canonical = canonicalize_json(schema)
    return compute_sha256(canonical)


def canonicalize_envelope(envelope: dict) -> str:
    """
    Convert envelope to canonical JSONL format.

    Args:
        envelope: Envelope dict

    Returns:
        Canonical JSONL line (JSON + newline)
    """
    canonical = canonicalize_json(envelope)
    return canonical + '\n'
