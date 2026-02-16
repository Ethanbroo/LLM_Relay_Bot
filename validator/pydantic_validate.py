"""
Pydantic validation layer.

Maps action types to Pydantic models and validates payloads.
"""

from pydantic import ValidationError
from typing import Any

from .pydantic_models import (
    Envelope,
    FsReadAction,
    FsListDirAction,
    SystemHealthPingAction,
)


class PydanticValidationError(Exception):
    """Pydantic validation failed."""
    def __init__(self, message: str, details: list = None):
        super().__init__(message)
        self.details = details or []


# Action to model mapping
ACTION_MODELS = {
    'fs.read': FsReadAction,
    'fs.list_dir': FsListDirAction,
    'system.health_ping': SystemHealthPingAction,
}


def validate_envelope(envelope_dict: dict) -> Envelope:
    """
    Validate envelope with Pydantic.

    Args:
        envelope_dict: Envelope dictionary

    Returns:
        Validated Envelope model

    Raises:
        PydanticValidationError: If validation fails
    """
    try:
        return Envelope(**envelope_dict)
    except ValidationError as e:
        errors = [f"{err['loc'][0] if err['loc'] else 'root'}: {err['msg']}" for err in e.errors()]
        raise PydanticValidationError(
            "Envelope validation failed",
            details=errors
        )


def validate_payload(payload: dict, action: str) -> Any:
    """
    Validate action payload with Pydantic.

    Args:
        payload: Payload dictionary
        action: Action identifier

    Returns:
        Validated Pydantic model instance

    Raises:
        PydanticValidationError: If validation fails or action unknown
    """
    if action not in ACTION_MODELS:
        raise PydanticValidationError(
            f"Unknown action '{action}' (no Pydantic model registered)",
            details=[f"Registered actions: {list(ACTION_MODELS.keys())}"]
        )

    model_class = ACTION_MODELS[action]

    try:
        return model_class(**payload)
    except ValidationError as e:
        errors = [f"{'.'.join(str(loc) for loc in err['loc']) if err['loc'] else 'root'}: {err['msg']}" for err in e.errors()]
        raise PydanticValidationError(
            f"Payload validation failed for action '{action}'",
            details=errors
        )


def get_supported_actions() -> list[str]:
    """Get list of actions with registered Pydantic models."""
    return list(ACTION_MODELS.keys())
