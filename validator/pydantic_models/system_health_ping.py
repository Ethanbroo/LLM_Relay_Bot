"""Pydantic model for system.health_ping action."""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional


class SystemHealthPingAction(BaseModel):
    """
    System health ping action payload.

    Minimal payload for health checks.
    """

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
    )

    echo: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Optional string to echo back"
    )
