"""Canonical JSON serialization for Layer 2.

Layer 2 Invariant: All structured objects that are hashed use this module
as the single serialization path. This guarantees:

- Deterministic key ordering (sort_keys=True)
- No whitespace variation (separators=(",", ":"))
- Full Unicode preserved, no ASCII escaping (ensure_ascii=False)
- Float formatting is Python's default repr (sufficient for our use — we
  do not store floats in diff payloads; confidence floats live in text fields)

Usage:
    from orchestration.canonical import canonical_dumps, canonical_hash

    # For hashing a structured diff:
    h = canonical_hash({"file": "foo.py", "op": "modify"})

    # For hashing a structured diff identity (separate from proposal_hash):
    diff_identity_hash = canonical_hash(diff_as_dict)
"""

import json
import hashlib


def canonical_dumps(obj: dict) -> str:
    """Serialize obj to a canonical JSON string.

    Guarantees:
    - Keys sorted lexicographically at every nesting level
    - No spaces around separators
    - Unicode characters preserved (not ASCII-escaped)
    - Identical semantic content → identical byte string

    Args:
        obj: JSON-serializable dict (or list/scalar)

    Returns:
        Canonical JSON string
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj: dict) -> str:
    """SHA-256 of canonical_dumps(obj), hex-encoded.

    This is the single hash function for all structured objects in Layer 2.
    Do NOT hash raw strings with this — use hashlib directly on normalized
    text for proposal_hash (which is already established in Layer 1).

    Args:
        obj: JSON-serializable dict

    Returns:
        64-character lowercase hex SHA-256 digest
    """
    serialized = canonical_dumps(obj).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
