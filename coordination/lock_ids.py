"""Deterministic lock identity computation.

Lock IDs are computed as:
    lock_id = sha256_hex(canonical_json({resource_type, resource_id, scope}))

Phase 4 Invariant: Lock IDs are deterministic and collision-resistant.
"""

import hashlib
from typing import Literal
from validator.canonicalize import canonicalize_json


# Closed enum of resource types
ResourceType = Literal[
    "filesystem_path",
    "document_id",
    "account_id",
    "connector_target"
]


class InvalidResourceTypeError(Exception):
    """Raised when resource_type is not in closed enum."""
    pass


def compute_lock_id(
    resource_type: ResourceType,
    resource_id: str,
    scope: str = "global"
) -> str:
    """Compute deterministic lock ID.

    Args:
        resource_type: Type of resource (closed enum)
        resource_id: Canonical resource identifier (already sanitized)
        scope: Lock scope (default "global")

    Returns:
        SHA-256 hex string (64 characters)

    Raises:
        InvalidResourceTypeError: If resource_type not in enum

    Examples:
        >>> compute_lock_id("filesystem_path", "/workspace/file.txt")
        'a1b2c3...'
    """
    # Validate resource type
    valid_types = {"filesystem_path", "document_id", "account_id", "connector_target"}
    if resource_type not in valid_types:
        raise InvalidResourceTypeError(
            f"Invalid resource_type: {resource_type}. Must be one of {valid_types}"
        )

    # Build canonical structure
    lock_spec = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "scope": scope
    }

    # Canonicalize and hash
    canonical = canonicalize_json(lock_spec)
    lock_id = hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    return lock_id


def compute_lock_set_id(lock_ids: list[str]) -> str:
    """Compute deterministic ID for a set of locks.

    Used for audit logging and tracking lock set acquisitions.

    Args:
        lock_ids: List of lock IDs (must be sorted lexicographically)

    Returns:
        SHA-256 hex string

    Raises:
        ValueError: If lock_ids not sorted
    """
    if lock_ids != sorted(lock_ids):
        raise ValueError("lock_ids must be sorted lexicographically")

    # Hash the sorted list
    canonical = canonicalize_json(lock_ids)
    lock_set_id = hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    return lock_set_id


def validate_lock_id(lock_id: str) -> bool:
    """Validate lock ID format.

    Args:
        lock_id: Lock ID to validate

    Returns:
        True if valid, False otherwise
    """
    # Must be 64-character hex string
    if not isinstance(lock_id, str):
        return False

    if len(lock_id) != 64:
        return False

    try:
        int(lock_id, 16)
        return True
    except ValueError:
        return False
