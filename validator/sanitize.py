"""
Sanitization module.

ONLY allowed operations:
- Unicode NFC normalization
- Path separator normalization

NEVER "fix" dangerous input - REJECT instead.
"""

import unicodedata
from typing import Any


class SanitizationError(Exception):
    """Sanitization detected dangerous input that must be rejected."""
    pass


def sanitize_payload(payload: dict, action: str) -> dict:
    """
    Sanitize payload according to strict rules.

    Only performs minimal safe normalization:
    - Unicode NFC normalization for strings
    - Path separator normalization for path fields

    Rejects dangerous input rather than "fixing" it.

    Args:
        payload: Validated payload dict
        action: Action identifier

    Returns:
        Sanitized payload dict

    Raises:
        SanitizationError: If dangerous input detected
    """
    # Action-specific sanitization
    if action == 'fs.read':
        return _sanitize_fs_read(payload)
    elif action == 'fs.list_dir':
        return _sanitize_fs_list_dir(payload)
    elif action == 'system.health_ping':
        return _sanitize_health_ping(payload)
    else:
        # Unknown action - do generic sanitization
        return _sanitize_dict(payload)


def _sanitize_fs_read(payload: dict) -> dict:
    """Sanitize fs.read payload."""
    sanitized = {}

    # Path: already validated by Pydantic, but ensure NFC normalization
    path = payload['path']
    sanitized['path'] = _sanitize_string(path)

    # Verify no dangerous patterns survived validation
    if '..' in sanitized['path']:
        raise SanitizationError("Path contains '..' after sanitization")
    if sanitized['path'].startswith('/'):
        raise SanitizationError("Path is absolute after sanitization")

    # Copy other fields (already validated)
    sanitized['offset'] = payload.get('offset', 0)
    sanitized['length'] = payload.get('length', 1048576)
    sanitized['encoding'] = payload.get('encoding', 'utf-8')

    return sanitized


def _sanitize_fs_list_dir(payload: dict) -> dict:
    """Sanitize fs.list_dir payload."""
    sanitized = {}

    # Path validation
    path = payload['path']
    sanitized['path'] = _sanitize_string(path)

    # Verify safety
    if '..' in sanitized['path']:
        raise SanitizationError("Path contains '..' after sanitization")
    if sanitized['path'].startswith('/'):
        raise SanitizationError("Path is absolute after sanitization")

    # Copy other fields
    sanitized['max_entries'] = payload.get('max_entries', 100)
    sanitized['sort_order'] = payload.get('sort_order', 'name_asc')
    sanitized['include_hidden'] = payload.get('include_hidden', False)
    sanitized['recursive'] = payload.get('recursive', False)

    return sanitized


def _sanitize_health_ping(payload: dict) -> dict:
    """Sanitize system.health_ping payload."""
    sanitized = {}

    # Echo string (if present)
    if 'echo' in payload and payload['echo'] is not None:
        sanitized['echo'] = _sanitize_string(payload['echo'])

    return sanitized


def _sanitize_dict(obj: Any) -> Any:
    """
    Recursively sanitize a dictionary or value.

    Args:
        obj: Object to sanitize

    Returns:
        Sanitized object
    """
    if isinstance(obj, dict):
        return {key: _sanitize_dict(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_dict(item) for item in obj]
    elif isinstance(obj, str):
        return _sanitize_string(obj)
    else:
        return obj


def _sanitize_string(s: str) -> str:
    """
    Sanitize a string.

    Only performs:
    - Unicode NFC normalization
    - Control character check (reject, don't fix)

    Args:
        s: String to sanitize

    Returns:
        Sanitized string

    Raises:
        SanitizationError: If dangerous characters detected
    """
    # Check for null bytes (should already be caught by Pydantic, but double-check)
    if '\x00' in s:
        raise SanitizationError("String contains null byte")

    # Unicode NFC normalization
    normalized = unicodedata.normalize('NFC', s)

    # Verify no unexpected control characters
    # Allow common whitespace (space, tab, newline, carriage return)
    allowed_control = {' ', '\t', '\n', '\r'}
    for char in normalized:
        if ord(char) < 32 and char not in allowed_control:
            raise SanitizationError(f"String contains control character (0x{ord(char):02x})")

    return normalized
