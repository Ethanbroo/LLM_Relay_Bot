"""
Schema Registry for action schema lookup and validation.

NO directory scanning. NO auto-discovery. Only explicit registry entries.
"""

import json
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass

from .canonicalize import compute_schema_hash


@dataclass
class SchemaSelection:
    """Result of successful schema selection."""
    action: str
    version: str
    schema: dict
    schema_hash: str
    schema_path: str


class SchemaRegistryError(Exception):
    """Base exception for schema registry errors."""
    pass


class SchemaNotFoundError(SchemaRegistryError):
    """Schema not found in registry."""
    pass


class SchemaVersionNotAllowedError(SchemaRegistryError):
    """Requested schema version is not in allowed list."""
    pass


class SchemaRegistry:
    """
    Deterministic schema registry.

    Loads schemas from explicit registry index only.
    No directory scanning, no auto-discovery, no fallbacks.
    """

    def __init__(self, registry_path: str = "config/schema_registry_index.json", base_dir: Optional[str] = None):
        """
        Initialize schema registry.

        Args:
            registry_path: Path to registry index JSON
            base_dir: Base directory for resolving schema paths (defaults to cwd)
        """
        self.registry_path = Path(registry_path)
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()

        # Load registry index
        with open(self.registry_path, 'r') as f:
            self.index = json.load(f)

        # Cache for loaded schemas
        self._schema_cache: Dict[str, dict] = {}
        self._hash_cache: Dict[str, str] = {}

    def select_schema(self, action: str, version: Optional[str] = None) -> SchemaSelection:
        """
        Select schema for action and version.

        Args:
            action: Action identifier (e.g., "fs.read")
            version: Explicit version or None for default

        Returns:
            SchemaSelection with loaded schema and hash

        Raises:
            SchemaNotFoundError: Action not in registry
            SchemaVersionNotAllowedError: Version not allowed
        """
        # Check action exists
        if action not in self.index['actions']:
            raise SchemaNotFoundError(f"Action '{action}' not found in registry")

        action_config = self.index['actions'][action]

        # Resolve version
        if version is None:
            # Use default version
            version = action_config['default']
        else:
            # Check version is allowed
            if version not in action_config['allowed']:
                raise SchemaVersionNotAllowedError(
                    f"Version '{version}' not allowed for action '{action}'. "
                    f"Allowed: {action_config['allowed']}"
                )

        # Load schema
        schema_path = action_config['schema_path']
        cache_key = f"{action}:{version}"

        if cache_key not in self._schema_cache:
            full_path = self.base_dir / schema_path
            with open(full_path, 'r') as f:
                schema = json.load(f)

            # Compute and cache hash
            schema_hash = compute_schema_hash(schema)

            self._schema_cache[cache_key] = schema
            self._hash_cache[cache_key] = schema_hash
        else:
            schema = self._schema_cache[cache_key]
            schema_hash = self._hash_cache[cache_key]

        return SchemaSelection(
            action=action,
            version=version,
            schema=schema,
            schema_hash=schema_hash,
            schema_path=schema_path
        )

    def get_schema_hash(self, action: str, version: str) -> str:
        """
        Get hash for a schema without loading the full schema.

        Args:
            action: Action identifier
            version: Schema version

        Returns:
            SHA-256 hash of schema
        """
        cache_key = f"{action}:{version}"

        if cache_key not in self._hash_cache:
            # Need to load schema to compute hash
            self.select_schema(action, version)

        return self._hash_cache[cache_key]

    def list_actions(self) -> list[str]:
        """Get list of all registered actions."""
        return list(self.index['actions'].keys())

    def get_allowed_versions(self, action: str) -> list[str]:
        """Get list of allowed versions for an action."""
        if action not in self.index['actions']:
            raise SchemaNotFoundError(f"Action '{action}' not found in registry")

        return self.index['actions'][action]['allowed']
