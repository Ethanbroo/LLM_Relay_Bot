"""Secrets provider with opaque handles.

Phase 5 Invariant: Secrets never appear in logs, exceptions, or results.
"""

import os
import re
from typing import Optional
from connectors.errors import SecretUnavailableError, SecretLeakDetectedError


# Secret patterns to detect leakage
SECRET_PATTERNS = [
    re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', re.IGNORECASE),
    re.compile(r'eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_.+/=]*'),  # JWT
    re.compile(r'sk-[A-Za-z0-9]{20,}'),  # OpenAI-like keys
    re.compile(r'aws_secret_access_key\s*=\s*[A-Za-z0-9/+]{40}'),
    re.compile(r'password\s*[:=]\s*["\']?[^"\'\s]{8,}', re.IGNORECASE),
]


def detect_secret_leak(text: str) -> bool:
    """Detect if text contains secret-like patterns.

    Args:
        text: Text to check

    Returns:
        True if secret pattern detected, False otherwise
    """
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return True
    return False


class SecretsProvider:
    """Provider for resolving secret handles.

    Phase 5 Invariants:
    - Returns secret bytes in-memory only
    - Never logs secret values
    - Never throws with secret content
    """

    def __init__(self, env_prefix: str = "LLM_RELAY_SECRET_"):
        """Initialize secrets provider.

        Args:
            env_prefix: Environment variable prefix for secrets
        """
        self.env_prefix = env_prefix
        self._cache = {}  # handle -> secret (in-memory only)

    def resolve(self, secret_handle: str) -> bytes:
        """Resolve secret handle to secret value.

        Args:
            secret_handle: Secret handle (e.g., "secret:wp_app_password_v1")

        Returns:
            Secret bytes

        Raises:
            SecretUnavailableError: If secret cannot be resolved
        """
        if not secret_handle.startswith("secret:"):
            raise SecretUnavailableError(f"Invalid secret handle format")

        # Extract secret name
        secret_name = secret_handle[7:]  # Remove "secret:" prefix

        # Check cache
        if secret_handle in self._cache:
            return self._cache[secret_handle]

        # Resolve from environment
        env_var = f"{self.env_prefix}{secret_name.upper()}"
        secret_value = os.environ.get(env_var)

        if secret_value is None:
            raise SecretUnavailableError(
                f"Secret not found: {secret_handle} (env var: {env_var})"
            )

        # Cache and return
        secret_bytes = secret_value.encode('utf-8')
        self._cache[secret_handle] = secret_bytes
        return secret_bytes

    def resolve_string(self, secret_handle: str) -> str:
        """Resolve secret handle to string.

        Args:
            secret_handle: Secret handle

        Returns:
            Secret string

        Raises:
            SecretUnavailableError: If secret cannot be resolved
        """
        return self.resolve(secret_handle).decode('utf-8')

    def check_for_leaks(self, text: str) -> None:
        """Check if text contains secret patterns.

        Args:
            text: Text to check

        Raises:
            SecretLeakDetectedError: If secret pattern detected
        """
        if detect_secret_leak(text):
            raise SecretLeakDetectedError(
                "Secret-like pattern detected in output"
            )

    def redact_secrets(self, text: str) -> str:
        """Redact secrets from text.

        Args:
            text: Text to redact

        Returns:
            Redacted text
        """
        redacted = text
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub('[REDACTED]', redacted)
        return redacted
