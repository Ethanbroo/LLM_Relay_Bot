"""Handler registry for action execution.

Maps action names to handler classes.
Provides handler lookup and validation.
"""

from typing import Any, Type
from executor.handlers import HandlerError
from executor.handlers.health_ping import HealthPingHandler
from executor.handlers.fs_read import FsReadHandler
from executor.handlers.fs_list_dir import FsListDirHandler


class HandlerRegistry:
    """Registry of execution handlers."""

    def __init__(self):
        """Initialize handler registry with built-in handlers."""
        self._handlers: dict[str, Any] = {
            "system.health_ping": HealthPingHandler(),
            "fs.read": FsReadHandler(),
            "fs.list_dir": FsListDirHandler(),
        }

    def get_handler(self, action: str) -> Any:
        """Get handler for action.

        Args:
            action: Action name (e.g., 'fs.read')

        Returns:
            Handler instance

        Raises:
            HandlerError: If handler not found
        """
        if action not in self._handlers:
            raise HandlerError(f"Handler not found for action: {action}")

        return self._handlers[action]

    def has_handler(self, action: str) -> bool:
        """Check if handler exists for action.

        Args:
            action: Action name

        Returns:
            True if handler exists
        """
        return action in self._handlers

    def list_actions(self) -> list[str]:
        """List all supported actions.

        Returns:
            List of action names
        """
        return list(self._handlers.keys())

    def register_handler(self, action: str, handler: Any) -> None:
        """Register custom handler (for testing/extension).

        Args:
            action: Action name
            handler: Handler instance

        Raises:
            HandlerError: If action already registered
        """
        if action in self._handlers:
            raise HandlerError(f"Handler already registered for action: {action}")

        self._handlers[action] = handler
