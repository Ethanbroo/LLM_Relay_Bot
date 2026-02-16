"""Closed error enum for blog draft creation capability.

All errors must use this enum - no other error shapes allowed.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class BlogErrorCode(str, Enum):
    """Closed enumeration of error codes."""

    # Validation errors
    ERR_VALIDATION = "ERR_VALIDATION"

    # Policy errors
    ERR_POLICY_DENY = "ERR_POLICY_DENY"

    # Secret errors
    ERR_SECRET_UNAVAILABLE = "ERR_SECRET_UNAVAILABLE"

    # Connector errors
    ERR_CONNECTOR_NOT_REGISTERED = "ERR_CONNECTOR_NOT_REGISTERED"
    ERR_CONNECTOR_IDEMPOTENCY_HIT = "ERR_CONNECTOR_IDEMPOTENCY_HIT"

    # Rate limiting
    ERR_RATE_LIMITED = "ERR_RATE_LIMITED"

    # HTTP errors
    ERR_HTTP = "ERR_HTTP"

    # WordPress-specific
    ERR_NON_UNIQUE_MATCH = "ERR_NON_UNIQUE_MATCH"
    ERR_SLUG_COLLISION_EXHAUSTED = "ERR_SLUG_COLLISION_EXHAUSTED"
    ERR_TAG_LIMIT_EXCEEDED = "ERR_TAG_LIMIT_EXCEEDED"
    ERR_TAG_CREATE_LIMIT_EXCEEDED = "ERR_TAG_CREATE_LIMIT_EXCEEDED"

    # Unsplash-specific
    ERR_IMAGE_LOW_CONFIDENCE = "ERR_IMAGE_LOW_CONFIDENCE"


@dataclass
class BlogError:
    """Standardized error structure.

    All failures MUST return this shape.
    """
    code: BlogErrorCode
    message: str
    retryable: bool

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable
            }
        }
