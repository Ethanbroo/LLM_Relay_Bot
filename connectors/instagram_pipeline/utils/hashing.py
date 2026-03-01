"""Hashing utilities for canonical content addressing.

Provides deterministic hash functions for reproducibility and audit traceability.
All hashes are SHA-256 for consistency with the existing audit logging system.
"""

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_hash(data: dict | Any) -> str:
    """
    Compute SHA-256 hash of a dict or dataclass in canonical form.

    Canonical form ensures the same logical data always produces the same hash,
    regardless of dict key ordering or minor formatting differences.

    Args:
        data: Dict or any JSON-serializable object

    Returns:
        Hex-encoded SHA-256 hash (64 characters)
    """
    if not isinstance(data, dict):
        # Convert dataclasses, named tuples, etc. to dict
        try:
            import dataclasses
            if dataclasses.is_dataclass(data):
                data = dataclasses.asdict(data)
            else:
                data = dict(data)
        except (TypeError, ValueError):
            # Last resort: str representation
            canonical_str = str(data)
            return hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

    # Sort keys recursively for deterministic ordering
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


def sha256_file(file_path: str) -> str:
    """
    Compute SHA-256 hash of a file's contents.

    Used for LoRA weights, reference images, and any other binary assets
    that need cryptographic verification for drift detection.

    Args:
        file_path: Path to file to hash

    Returns:
        Hex-encoded SHA-256 hash

    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file can't be read
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Cannot hash non-existent file: {file_path}")

    sha256 = hashlib.sha256()

    # Read in 8KB chunks to handle large files (LoRA weights can be 100s of MB)
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha256.update(chunk)

    return sha256.hexdigest()


def quick_hash(text: str) -> str:
    """
    Quick hash for non-cryptographic use cases (cache keys, temp IDs).

    Args:
        text: String to hash

    Returns:
        First 16 characters of SHA-256 hash (sufficient for collision avoidance)
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
