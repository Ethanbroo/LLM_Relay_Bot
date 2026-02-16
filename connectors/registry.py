"""Closed connector registry.

Phase 5 Invariant: Static mapping only. No plugin discovery.
No dynamic loading. No importing by string.
"""

from typing import Type, Optional
from connectors.base import BaseConnector
from connectors.errors import ConnectorUnknownError, ConnectorNotRegisteredError


class ConnectorRegistry:
    """Closed connector registry.

    Phase 5 Invariant: Only connectors explicitly registered
    in code can be instantiated.
    """

    def __init__(self):
        """Initialize connector registry."""
        # connector_type -> connector_class
        self._connectors: dict[str, Type[BaseConnector]] = {}

        # action -> (connector_type, method)
        self._action_mappings: dict[str, tuple[str, str]] = {}

    def register(
        self,
        connector_type: str,
        connector_class: Type[BaseConnector]
    ) -> None:
        """Register connector class.

        Args:
            connector_type: Connector type identifier
            connector_class: Connector class

        Raises:
            ValueError: If connector_type already registered
        """
        if connector_type in self._connectors:
            raise ValueError(f"Connector {connector_type} already registered")

        self._connectors[connector_type] = connector_class

    def register_action_mapping(
        self,
        action: str,
        connector_type: str,
        method: str
    ) -> None:
        """Register action to connector mapping.

        Args:
            action: Action identifier
            connector_type: Connector type
            method: Connector method name

        Raises:
            ValueError: If action already mapped
        """
        if action in self._action_mappings:
            raise ValueError(f"Action {action} already mapped")

        self._action_mappings[action] = (connector_type, method)

    def get_connector_class(self, connector_type: str) -> Type[BaseConnector]:
        """Get connector class by type.

        Args:
            connector_type: Connector type

        Returns:
            Connector class

        Raises:
            ConnectorUnknownError: If connector not registered
        """
        if connector_type not in self._connectors:
            raise ConnectorUnknownError(
                f"Unknown connector type: {connector_type}"
            )

        return self._connectors[connector_type]

    def get_connector_for_action(self, action: str) -> tuple[str, str]:
        """Get connector type and method for action.

        Args:
            action: Action identifier

        Returns:
            Tuple of (connector_type, method)

        Raises:
            ConnectorNotRegisteredError: If action not mapped
        """
        if action not in self._action_mappings:
            raise ConnectorNotRegisteredError(
                f"No connector registered for action: {action}"
            )

        return self._action_mappings[action]

    def is_registered(self, connector_type: str) -> bool:
        """Check if connector type is registered.

        Args:
            connector_type: Connector type

        Returns:
            True if registered, False otherwise
        """
        return connector_type in self._connectors

    def has_action_mapping(self, action: str) -> bool:
        """Check if action has connector mapping.

        Args:
            action: Action identifier

        Returns:
            True if mapped, False otherwise
        """
        return action in self._action_mappings

    def list_connectors(self) -> list[str]:
        """List all registered connector types.

        Returns:
            List of connector type identifiers
        """
        return list(self._connectors.keys())

    def list_actions(self) -> list[str]:
        """List all mapped actions.

        Returns:
            List of action identifiers
        """
        return list(self._action_mappings.keys())


# Global connector registry instance
_global_registry: Optional[ConnectorRegistry] = None


def get_global_registry() -> ConnectorRegistry:
    """Get global connector registry.

    Returns:
        Global ConnectorRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = ConnectorRegistry()
    return _global_registry


def register_connector(
    connector_type: str,
    connector_class: Type[BaseConnector]
) -> None:
    """Register connector in global registry.

    Args:
        connector_type: Connector type
        connector_class: Connector class
    """
    registry = get_global_registry()
    registry.register(connector_type, connector_class)


def register_action(
    action: str,
    connector_type: str,
    method: str = "execute"
) -> None:
    """Register action mapping in global registry.

    Args:
        action: Action identifier
        connector_type: Connector type
        method: Connector method (default: "execute")
    """
    registry = get_global_registry()
    registry.register_action_mapping(action, connector_type, method)
