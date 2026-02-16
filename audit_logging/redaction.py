"""
Secret redaction engine for Phase 3 audit logging.

Detects and redacts secrets from event payloads before persistence.

Secret detection patterns (case-insensitive):
- authorization, api_key, token, secret, password, cookie, private_key, etc.
- Bearer tokens in string values
- Long base64 strings (>80 characters)

Redaction:
- Replaces secret values with literal string "REDACTED"
- Returns JSON pointer paths of redacted fields
- Fails closed: Better to over-redact than leak secrets
"""

import re
from typing import Any


# Secret field name patterns (case-insensitive)
SECRET_FIELD_PATTERNS = [
    r'authorization',
    r'api[_-]?key',
    r'token',
    r'secret',
    r'password',
    r'passwd',
    r'pwd',
    r'cookie',
    r'private[_-]?key',
    r'priv[_-]?key',
    r'auth',
    r'credential',
    r'cred',
]

# Compile regex for field names
SECRET_FIELD_REGEX = re.compile(
    r'(' + '|'.join(SECRET_FIELD_PATTERNS) + r')',
    re.IGNORECASE
)

# Bearer token pattern
BEARER_TOKEN_REGEX = re.compile(
    r'\bBearer\s+[A-Za-z0-9\-._~+/]+=*',
    re.IGNORECASE
)

# Long base64 string pattern (>80 chars)
LONG_BASE64_REGEX = re.compile(
    r'\b[A-Za-z0-9+/]{80,}={0,2}\b'
)


def redact(obj: Any, path: str = "") -> tuple[Any, list[str]]:
    """
    Recursively redact secrets from object.

    Redaction rules:
    1. Field names matching secret patterns → redact value
    2. String values containing Bearer tokens → redact entire string
    3. String values with long base64 (>80 chars) → redact entire string

    Args:
        obj: Object to redact (dict, list, str, int, bool, None)
        path: Current JSON pointer path (for tracking redacted fields)

    Returns:
        Tuple of (redacted_object, list_of_redacted_json_pointer_paths)

    Examples:
        >>> redact({"password": "secret123"})
        ({"password": "REDACTED"}, ["/password"])

        >>> redact({"auth": "Bearer abc123"})
        ({"auth": "REDACTED"}, ["/auth"])

        >>> redact({"data": {"api_key": "key"}})
        ({"data": {"api_key": "REDACTED"}}, ["/data/api_key"])
    """
    redacted_paths = []

    if isinstance(obj, dict):
        redacted_dict = {}
        for key, value in obj.items():
            current_path = f"{path}/{key}"

            # Check if field name matches secret pattern
            # BUT only redact if value is a leaf (not a dict or list)
            if SECRET_FIELD_REGEX.search(key) and not isinstance(value, (dict, list)):
                redacted_dict[key] = "REDACTED"
                redacted_paths.append(current_path)
            else:
                # Recursively redact value
                redacted_value, child_paths = redact(value, current_path)
                redacted_dict[key] = redacted_value
                redacted_paths.extend(child_paths)

        return redacted_dict, redacted_paths

    elif isinstance(obj, list):
        redacted_list = []
        for index, item in enumerate(obj):
            current_path = f"{path}/{index}"
            redacted_item, child_paths = redact(item, current_path)
            redacted_list.append(redacted_item)
            redacted_paths.extend(child_paths)

        return redacted_list, redacted_paths

    elif isinstance(obj, str):
        # Check for Bearer tokens
        if BEARER_TOKEN_REGEX.search(obj):
            return "REDACTED", [path] if path else []

        # Check for long base64 strings
        if LONG_BASE64_REGEX.search(obj):
            return "REDACTED", [path] if path else []

        # No secrets detected in string
        return obj, []

    else:
        # Primitives (int, bool, None) pass through
        return obj, []


def check_no_secrets(obj: Any) -> None:
    """
    Verify that object contains no secrets after redaction.

    This is a safety check to ensure redaction was successful.

    Args:
        obj: Object to check

    Raises:
        ValueError: If any secrets detected (redaction failed)
    """
    _, redacted_paths = redact(obj)

    if redacted_paths:
        raise ValueError(
            f"Secrets still present after redaction at paths: {redacted_paths}"
        )


def create_redaction_metadata(was_redacted: bool, redacted_paths: list[str]) -> dict:
    """
    Create redaction metadata dict for audit event.

    Args:
        was_redacted: True if any secrets were redacted
        redacted_paths: List of JSON pointer paths that were redacted

    Returns:
        Dict with keys: was_redacted, redacted_paths
    """
    return {
        "was_redacted": was_redacted,
        "redacted_paths": redacted_paths
    }
