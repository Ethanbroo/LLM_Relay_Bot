"""Deterministic task_id generation.

task_id = SHA-256(message_id + action + action_version + canonical_payload)

This ensures:
1. Same ValidatedAction → same task_id (deduplication)
2. Different payload → different task_id (isolation)
3. Deterministic across runs (no randomness)
4. Cryptographically strong collision resistance
"""

import hashlib
from validator.canonicalize import canonicalize_json


def compute_task_id(validated_action: dict) -> str:
    """Compute deterministic task_id from ValidatedAction.

    Args:
        validated_action: ValidatedAction dict from Phase 1 pipeline

    Returns:
        Deterministic task_id (64-char hex SHA-256 hash)

    Raises:
        KeyError: If required fields are missing
        ValueError: If input is malformed
    """
    # Extract required fields
    try:
        message_id = validated_action["original_envelope"]["message_id"]
        action = validated_action["original_envelope"]["action"]
        action_version = validated_action["original_envelope"]["action_version"]
        payload = validated_action["sanitized_payload"]
    except KeyError as e:
        raise ValueError(f"ValidatedAction missing required field: {e}") from e

    # Canonical representation of task components
    canonical_payload = canonicalize_json(payload)

    # Deterministic concatenation
    # Format: message_id|action|action_version|canonical_payload
    # Using | as separator (won't appear in UUIDs or action names)
    components = f"{message_id}|{action}|{action_version}|{canonical_payload}"

    # SHA-256 hash (deterministic, collision-resistant)
    task_id_hash = hashlib.sha256(components.encode('utf-8')).hexdigest()

    return task_id_hash


def compute_session_id_deterministic(task_id: str, attempt: int) -> str:
    """Compute session_id for a specific attempt (deterministic).

    Note: In production, session_id should be a UUID v7 generated at first attempt
    and reused across retries. This function is for testing determinism.

    Args:
        task_id: Deterministic task identifier
        attempt: Attempt number (1-indexed)

    Returns:
        Deterministic session ID (SHA-256 hash)
    """
    session_data = f"session_{task_id}_attempt_{attempt}"
    return hashlib.sha256(session_data.encode('utf-8')).hexdigest()


def validate_task_id(task_id: str) -> bool:
    """Validate task_id format.

    Args:
        task_id: Task identifier to validate

    Returns:
        True if valid (64-char hex string), False otherwise
    """
    if not isinstance(task_id, str):
        return False

    if len(task_id) != 64:
        return False

    try:
        int(task_id, 16)  # Check if valid hex
        return True
    except ValueError:
        return False
