"""Execution handlers for supported actions.

Each handler:
1. Accepts ValidatedAction and Sandbox
2. Executes action within sandbox
3. Returns artifacts dict or raises HandlerError
4. Is deterministic (same input → same output)
5. Has timeout protection (Phase 3)

Supported handlers:
- system.health_ping: Minimal health check
- fs.read: Read file from sandbox
- fs.list_dir: List directory in sandbox
"""

from typing import Protocol, Any


class HandlerError(Exception):
    """Base exception for handler errors."""
    pass


class HandlerTimeout(HandlerError):
    """Handler exceeded timeout."""
    pass


class HandlerProtocol(Protocol):
    """Protocol for execution handlers."""

    def execute(self, validated_action: dict, sandbox: Any) -> dict:
        """Execute action within sandbox.

        Args:
            validated_action: ValidatedAction from Phase 1
            sandbox: Sandbox instance

        Returns:
            Artifacts dict (action-specific output)

        Raises:
            HandlerError: If execution fails
        """
        ...
