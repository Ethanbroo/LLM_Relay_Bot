"""
Role-based permissions and safety tier management.

Provides:
  - Role enum (ADMIN / USER)
  - SafetyTier enum (SUPER_SAFE / MODERATELY_SAFE)
  - is_admin(user_id) check against BOT_OWNER_ID
  - PermissionsManager class for reading/writing user settings in Redis
"""

from __future__ import annotations

import json
import logging
from enum import Enum

from telegram_bot.auth import BOT_OWNER_ID

logger = logging.getLogger(__name__)


class Role(str, Enum):
    ADMIN = "ADMIN"
    USER = "USER"


class SafetyTier(str, Enum):
    SUPER_SAFE = "SUPER_SAFE"
    MODERATELY_SAFE = "MODERATELY_SAFE"


DEFAULT_SAFETY_TIER = SafetyTier.SUPER_SAFE


def get_user_role(user_id: int) -> Role:
    """Determine user role based on bot owner ID."""
    if user_id == BOT_OWNER_ID:
        return Role.ADMIN
    return Role.USER


def is_admin(user_id: int) -> bool:
    """Check if user_id is the bot admin."""
    return user_id == BOT_OWNER_ID


class PermissionsManager:
    """Reads/writes user settings (safety tier) from Redis."""

    SETTINGS_KEY_PREFIX = "settings"

    def __init__(self, redis_client):
        self.redis = redis_client

    def _key(self, user_id: int) -> str:
        return f"{self.SETTINGS_KEY_PREFIX}:{user_id}"

    async def get_safety_tier(self, user_id: int) -> SafetyTier:
        """Get user's safety tier. Defaults to SUPER_SAFE."""
        if not self.redis:
            return DEFAULT_SAFETY_TIER
        raw = await self.redis.get(self._key(user_id))
        if not raw:
            return DEFAULT_SAFETY_TIER
        try:
            data = json.loads(raw)
            return SafetyTier(data.get("safety_tier", DEFAULT_SAFETY_TIER.value))
        except (json.JSONDecodeError, ValueError):
            return DEFAULT_SAFETY_TIER

    async def set_safety_tier(self, user_id: int, tier: SafetyTier) -> None:
        """Set user's safety tier in Redis."""
        if not self.redis:
            return
        data = json.dumps({"safety_tier": tier.value})
        await self.redis.set(self._key(user_id), data)

    async def get_settings(self, user_id: int) -> dict:
        """Get full user settings dict."""
        tier = await self.get_safety_tier(user_id)
        return {
            "safety_tier": tier.value,
            "role": get_user_role(user_id).value,
        }
