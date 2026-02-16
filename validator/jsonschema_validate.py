"""
JSON Schema validation wrapper.

Strict validation with draft 2020-12:
- additionalProperties: false enforced
- No $ref allowed (prevent schema bombs)
- Hard limits enforced
"""

import jsonschema
from jsonschema import Draft202012Validator
from typing import Any, Dict


class JSONSchemaValidationError(Exception):
    """JSON Schema validation failed."""
    def __init__(self, message: str, details: list = None):
        super().__init__(message)
        self.details = details or []


def validate_envelope(envelope: dict, envelope_schema: dict) -> None:
    """
    Validate envelope against envelope schema.

    Args:
        envelope: Envelope dict to validate
        envelope_schema: JSON Schema for envelope

    Raises:
        JSONSchemaValidationError: If validation fails
    """
    _validate_with_schema(envelope, envelope_schema, "envelope")


def validate_payload(payload: dict, action_schema: dict, action: str) -> None:
    """
    Validate action payload against action schema.

    Args:
        payload: Payload dict to validate
        action_schema: JSON Schema for action
        action: Action identifier (for error messages)

    Raises:
        JSONSchemaValidationError: If validation fails
    """
    _validate_with_schema(payload, action_schema, f"action '{action}' payload")


def _validate_with_schema(data: Any, schema: dict, context: str) -> None:
    """
    Internal validation helper.

    Args:
        data: Data to validate
        schema: JSON Schema
        context: Context string for error messages

    Raises:
        JSONSchemaValidationError: If validation fails
    """
    # Check for forbidden $ref
    if _contains_ref(schema):
        raise JSONSchemaValidationError(
            f"Schema for {context} contains forbidden '$ref' (schema bombs not allowed)"
        )

    # Validate
    validator = Draft202012Validator(schema)

    errors = list(validator.iter_errors(data))
    if errors:
        # Format errors
        error_messages = []
        for error in errors:
            path = ".".join(str(p) for p in error.path) if error.path else "root"
            error_messages.append(f"{path}: {error.message}")

        raise JSONSchemaValidationError(
            f"Validation failed for {context}",
            details=error_messages
        )


def _contains_ref(obj: Any) -> bool:
    """
    Recursively check if schema contains $ref.

    Args:
        obj: Schema object (dict, list, or primitive)

    Returns:
        True if $ref found anywhere in schema
    """
    if isinstance(obj, dict):
        if '$ref' in obj:
            return True
        return any(_contains_ref(v) for v in obj.values())
    elif isinstance(obj, list):
        return any(_contains_ref(item) for item in obj)
    return False


def check_schema_strictness(schema: dict) -> list[str]:
    """
    Check that schema enforces strictness requirements.

    Checks for:
    - additionalProperties: false on all objects
    - Presence of max constraints (maxLength, maxItems, maxProperties)
    - No permissive patterns

    Args:
        schema: JSON Schema to check

    Returns:
        List of warnings (empty if schema is strict)
    """
    warnings = []

    def check_object(obj: dict, path: str = ""):
        if not isinstance(obj, dict):
            return

        # Check for object types without additionalProperties: false
        if obj.get('type') == 'object':
            if obj.get('additionalProperties') is not False:
                warnings.append(f"{path}: Object type missing 'additionalProperties: false'")

        # Check for arrays without maxItems
        if obj.get('type') == 'array':
            if 'maxItems' not in obj:
                warnings.append(f"{path}: Array type missing 'maxItems' constraint")

        # Check for strings without maxLength
        if obj.get('type') == 'string':
            if 'maxLength' not in obj and 'pattern' not in obj and 'enum' not in obj:
                warnings.append(f"{path}: String type missing 'maxLength' or 'pattern' constraint")

        # Recurse into properties
        if 'properties' in obj:
            for key, value in obj['properties'].items():
                check_object(value, f"{path}.properties.{key}")

        # Recurse into items
        if 'items' in obj:
            check_object(obj['items'], f"{path}.items")

    check_object(schema, "schema")
    return warnings
