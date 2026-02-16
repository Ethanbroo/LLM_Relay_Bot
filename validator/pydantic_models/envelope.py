"""Pydantic model for envelope validation."""

from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime
from typing import Any
import re


class Envelope(BaseModel):
    """
    Canonical message envelope.

    Strict validation with no additional properties allowed.
    """

    model_config = ConfigDict(
        extra='forbid',  # Reject unknown fields
        frozen=True,  # Immutable
        strict=True,  # Strict type checking
    )

    envelope_version: str = Field(
        pattern=r'^1\.0\.0$',
        description="Envelope schema version"
    )

    message_id: str = Field(
        description="UUID v7 (time-ordered) message identifier"
    )

    timestamp: str = Field(
        description="ISO 8601 timestamp"
    )

    sender: str = Field(
        min_length=1,
        max_length=128,
        pattern=r'^[a-zA-Z0-9_\-\.]+$',
        description="Sender principal ID"
    )

    recipient: str = Field(
        min_length=1,
        max_length=128,
        pattern=r'^[a-zA-Z0-9_\-\.]+$',
        description="Recipient principal ID"
    )

    action: str = Field(
        min_length=1,
        max_length=128,
        pattern=r'^[a-z0-9_]+\.[a-z0-9_]+$',
        description="Action identifier (family.action)"
    )

    action_version: str = Field(
        pattern=r'^\d+\.\d+\.\d+$',
        description="Action schema version (semver)"
    )

    payload: dict[str, Any] = Field(
        description="Action-specific payload"
    )

    @field_validator('message_id')
    @classmethod
    def validate_uuid_v7(cls, v: str) -> str:
        """Validate UUID v7 format (time-ordered)."""
        # Basic UUID format check
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        if not re.match(uuid_pattern, v, re.IGNORECASE):
            raise ValueError(f"Invalid UUID v7 format: {v}")
        return v.lower()

    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Validate ISO 8601 timestamp format."""
        try:
            datetime.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"Invalid ISO 8601 timestamp: {v}") from e
        return v
