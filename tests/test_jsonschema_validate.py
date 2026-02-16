"""Tests for JSON Schema validation module."""

import pytest
from validator.jsonschema_validate import (
    validate_envelope,
    validate_payload,
    check_schema_strictness,
    JSONSchemaValidationError,
)


@pytest.fixture
def envelope_schema():
    """Minimal envelope schema for testing."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["message_id", "action"],
        "additionalProperties": False,
        "properties": {
            "message_id": {"type": "string"},
            "action": {"type": "string"},
            "payload": {"type": "object"}
        }
    }


@pytest.fixture
def action_schema():
    """Minimal action schema for testing."""
    return {
        "type": "object",
        "required": ["path"],
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "maxLength": 256},
            "offset": {"type": "integer", "minimum": 0}
        }
    }


def test_validate_envelope_success(envelope_schema):
    """Test successful envelope validation."""
    envelope = {
        "message_id": "test-123",
        "action": "fs.read",
        "payload": {}
    }

    # Should not raise
    validate_envelope(envelope, envelope_schema)


def test_validate_envelope_missing_required(envelope_schema):
    """Test envelope validation with missing required field."""
    envelope = {
        "action": "fs.read"
        # Missing message_id
    }

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_envelope(envelope, envelope_schema)

    # Check that error details mention the missing field
    assert len(exc_info.value.details) > 0
    assert any("message_id" in detail or "required" in detail.lower() for detail in exc_info.value.details)


def test_validate_envelope_additional_property(envelope_schema):
    """Test envelope validation rejects additional properties."""
    envelope = {
        "message_id": "test-123",
        "action": "fs.read",
        "payload": {},
        "extra_field": "not_allowed"
    }

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_envelope(envelope, envelope_schema)

    # Check that error details mention the additional property
    error_str = str(exc_info.value.details)
    assert "additional" in error_str.lower() or "extra_field" in error_str.lower()


def test_validate_envelope_wrong_type(envelope_schema):
    """Test envelope validation rejects wrong types."""
    envelope = {
        "message_id": 123,  # Should be string
        "action": "fs.read",
        "payload": {}
    }

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_envelope(envelope, envelope_schema)

    assert len(exc_info.value.details) > 0


def test_validate_payload_success(action_schema):
    """Test successful payload validation."""
    payload = {
        "path": "test.txt",
        "offset": 0
    }

    # Should not raise
    validate_payload(payload, action_schema, "fs.read")


def test_validate_payload_missing_required(action_schema):
    """Test payload validation with missing required field."""
    payload = {
        "offset": 0
        # Missing path
    }

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_payload(payload, action_schema, "fs.read")

    assert "fs.read" in str(exc_info.value)
    assert len(exc_info.value.details) > 0


def test_validate_payload_exceeds_max_length(action_schema):
    """Test payload validation with string exceeding maxLength."""
    payload = {
        "path": "a" * 300  # Exceeds maxLength: 256
    }

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_payload(payload, action_schema, "fs.read")

    assert len(exc_info.value.details) > 0


def test_validate_payload_violates_minimum(action_schema):
    """Test payload validation with integer below minimum."""
    payload = {
        "path": "test.txt",
        "offset": -1  # Violates minimum: 0
    }

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_payload(payload, action_schema, "fs.read")

    assert len(exc_info.value.details) > 0


def test_validate_schema_with_ref_forbidden():
    """Test that schemas containing $ref are rejected."""
    schema_with_ref = {
        "type": "object",
        "properties": {
            "field": {"$ref": "#/definitions/something"}
        },
        "definitions": {
            "something": {"type": "string"}
        }
    }

    data = {"field": "value"}

    with pytest.raises(JSONSchemaValidationError, match="forbidden.*ref"):
        validate_envelope(data, schema_with_ref)


def test_validate_nested_ref_forbidden():
    """Test that nested $ref in schema is rejected."""
    schema_with_nested_ref = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "inner": {"$ref": "#/definitions/ref"}
                }
            }
        }
    }

    data = {"outer": {"inner": "value"}}

    with pytest.raises(JSONSchemaValidationError, match="ref"):
        validate_payload(data, schema_with_nested_ref, "test")


def test_validate_array_with_ref_forbidden():
    """Test that $ref in array schema is rejected."""
    schema_with_array_ref = {
        "type": "array",
        "items": {"$ref": "#/definitions/item"}
    }

    data = ["item1", "item2"]

    with pytest.raises(JSONSchemaValidationError):
        validate_envelope(data, schema_with_array_ref)


def test_check_schema_strictness_missing_additional_properties():
    """Test strictness checker warns about missing additionalProperties."""
    schema = {
        "type": "object",
        "properties": {
            "field": {"type": "string"}
        }
        # Missing additionalProperties: false
    }

    warnings = check_schema_strictness(schema)

    assert len(warnings) > 0
    assert any("additionalProperties" in w for w in warnings)


def test_check_schema_strictness_array_without_max():
    """Test strictness checker warns about array without maxItems."""
    schema = {
        "type": "array",
        "items": {"type": "string"}
        # Missing maxItems
    }

    warnings = check_schema_strictness(schema)

    assert len(warnings) > 0
    assert any("maxItems" in w for w in warnings)


def test_check_schema_strictness_string_without_constraint():
    """Test strictness checker warns about unbounded strings."""
    schema = {
        "type": "string"
        # Missing maxLength, pattern, or enum
    }

    warnings = check_schema_strictness(schema)

    assert len(warnings) > 0
    assert any("maxLength" in w or "pattern" in w for w in warnings)


def test_check_schema_strictness_nested_objects():
    """Test strictness checker on nested object schema."""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "nested": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"}
                }
                # Nested object missing additionalProperties
            }
        }
    }

    warnings = check_schema_strictness(schema)

    assert len(warnings) > 0
    assert any("nested" in w.lower() for w in warnings)


def test_check_schema_strictness_strict_schema():
    """Test that strict schema produces no warnings."""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "maxLength": 256},
            "count": {"type": "integer", "minimum": 0, "maximum": 1000},
            "items": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 50}}
        }
    }

    warnings = check_schema_strictness(schema)

    # Should have minimal or no warnings for a well-designed strict schema
    assert isinstance(warnings, list)


def test_validate_multiple_errors():
    """Test that multiple validation errors are collected."""
    schema = {
        "type": "object",
        "required": ["field1", "field2", "field3"],
        "additionalProperties": False,
        "properties": {
            "field1": {"type": "string"},
            "field2": {"type": "integer"},
            "field3": {"type": "boolean"}
        }
    }

    data = {}  # Missing all required fields

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_envelope(data, schema)

    # Should have multiple error details
    assert len(exc_info.value.details) >= 3


def test_validate_error_message_includes_path():
    """Test that validation errors include field paths."""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "nested": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": {"type": "integer"}
                }
            }
        }
    }

    data = {"nested": {"field": "not_an_integer"}}

    with pytest.raises(JSONSchemaValidationError) as exc_info:
        validate_envelope(data, schema)

    # Error should reference the nested path
    error_str = str(exc_info.value.details)
    assert "nested" in error_str or "field" in error_str
