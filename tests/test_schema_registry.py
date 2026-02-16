"""Tests for schema registry."""

import pytest
from validator.schema_registry import (
    SchemaRegistry,
    SchemaNotFoundError,
    SchemaVersionNotAllowedError,
)


@pytest.fixture
def registry():
    """Create schema registry fixture."""
    return SchemaRegistry(
        registry_path="config/schema_registry_index.json",
        base_dir="."
    )


def test_registry_loads_successfully(registry):
    """Test that registry loads without errors."""
    assert registry is not None
    assert registry.index is not None


def test_list_actions(registry):
    """Test listing registered actions."""
    actions = registry.list_actions()

    assert "fs.read" in actions
    assert "fs.list_dir" in actions
    assert "system.health_ping" in actions


def test_select_schema_with_default_version(registry):
    """Test schema selection with default version."""
    selection = registry.select_schema("fs.read")

    assert selection.action == "fs.read"
    assert selection.version == "1.0.0"
    assert selection.schema is not None
    assert len(selection.schema_hash) == 64  # SHA-256
    assert selection.schema['type'] == 'object'


def test_select_schema_with_explicit_version(registry):
    """Test schema selection with explicit version."""
    selection = registry.select_schema("fs.read", "1.0.0")

    assert selection.action == "fs.read"
    assert selection.version == "1.0.0"


def test_select_schema_unknown_action(registry):
    """Test that unknown action raises error."""
    with pytest.raises(SchemaNotFoundError, match="not found in registry"):
        registry.select_schema("unknown.action")


def test_select_schema_disallowed_version(registry):
    """Test that disallowed version raises error."""
    with pytest.raises(SchemaVersionNotAllowedError, match="not allowed"):
        registry.select_schema("fs.read", "99.99.99")


def test_schema_hash_deterministic(registry):
    """Test that schema hash is deterministic."""
    selection1 = registry.select_schema("fs.read")
    selection2 = registry.select_schema("fs.read")

    assert selection1.schema_hash == selection2.schema_hash


def test_schema_caching(registry):
    """Test that schemas are cached."""
    selection1 = registry.select_schema("fs.read")
    selection2 = registry.select_schema("fs.read")

    # Should return same object (cached)
    assert selection1.schema is selection2.schema


def test_get_schema_hash(registry):
    """Test getting schema hash directly."""
    hash1 = registry.get_schema_hash("fs.read", "1.0.0")

    assert len(hash1) == 64
    assert hash1.islower()  # Hex should be lowercase


def test_get_allowed_versions(registry):
    """Test getting allowed versions for an action."""
    versions = registry.get_allowed_versions("fs.read")

    assert "1.0.0" in versions
    assert len(versions) >= 1


def test_schema_structure_valid(registry):
    """Test that loaded schemas have required structure."""
    selection = registry.select_schema("fs.read")

    # Should have JSON Schema required fields
    assert '$schema' in selection.schema
    assert 'type' in selection.schema
    assert selection.schema['type'] == 'object'
    assert 'additionalProperties' in selection.schema
    assert selection.schema['additionalProperties'] is False
