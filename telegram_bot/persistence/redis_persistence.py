"""Redis-backed persistence for python-telegram-bot ConversationHandler state.

Persists conversation states, user_data, and chat_data to Redis so that
container restarts don't lose in-progress conversation state.

bot_data and callback_data are NOT persisted — bot_data is managed manually
in post_init, and callback_data is ephemeral.
"""

import json
import logging
from collections import defaultdict
from typing import Any

from telegram.ext import BasePersistence, PersistenceInput

logger = logging.getLogger(__name__)

CONVERSATION_TTL = 7 * 86400     # 7 days
USER_DATA_TTL = 30 * 86400       # 30 days
CHAT_DATA_TTL = 30 * 86400       # 30 days


class RedisPersistence(BasePersistence):
    """Redis-backed persistence for ConversationHandler state."""

    def __init__(self, redis_client, key_prefix: str = "ptb"):
        super().__init__(
            store_data=PersistenceInput(
                bot_data=False,      # Don't persist bot_data — we manage it manually
                chat_data=True,      # Persist per-chat data
                user_data=True,      # Persist per-user data
                callback_data=False, # Don't persist callback query data
            ),
        )
        self.redis = redis_client
        self.prefix = key_prefix

    # ── Conversations (the critical one) ─────────────────────

    async def get_conversations(self, name: str) -> dict:
        """Load all conversation states for a named ConversationHandler."""
        raw = await self.redis.hgetall(f"{self.prefix}:conversations:{name}")
        result = {}
        for key_str, state_str in raw.items():
            try:
                key = self._deserialize_key(key_str)
                state = int(state_str)
                result[key] = state
            except (ValueError, TypeError):
                continue
        return result

    async def update_conversation(
        self, name: str, key: tuple, new_state: int | None,
    ) -> None:
        """Update a single conversation state."""
        key_str = self._serialize_key(key)
        redis_key = f"{self.prefix}:conversations:{name}"

        if new_state is None:
            # Conversation ended — remove entry
            await self.redis.hdel(redis_key, key_str)
        else:
            await self.redis.hset(redis_key, key_str, str(new_state))
            await self.redis.expire(redis_key, CONVERSATION_TTL)

    # ── User Data ────────────────────────────────────────────

    async def get_user_data(self) -> dict[int, dict]:
        """Load all user_data dicts."""
        result = defaultdict(dict)
        async for key in self.redis.scan_iter(f"{self.prefix}:userdata:*"):
            try:
                user_id = int(key.split(":")[-1])
                raw = await self.redis.hgetall(key)
                result[user_id] = self._deserialize_values(raw)
            except (ValueError, TypeError):
                continue
        return dict(result)

    async def update_user_data(self, user_id: int, data: dict) -> None:
        """Update user_data for a single user."""
        redis_key = f"{self.prefix}:userdata:{user_id}"
        if data:
            await self.redis.hset(redis_key, mapping=self._serialize_values(data))
            await self.redis.expire(redis_key, USER_DATA_TTL)
        else:
            await self.redis.delete(redis_key)

    # ── Chat Data ────────────────────────────────────────────

    async def get_chat_data(self) -> dict[int, dict]:
        """Load all chat_data dicts."""
        result = defaultdict(dict)
        async for key in self.redis.scan_iter(f"{self.prefix}:chatdata:*"):
            try:
                chat_id = int(key.split(":")[-1])
                raw = await self.redis.hgetall(key)
                result[chat_id] = self._deserialize_values(raw)
            except (ValueError, TypeError):
                continue
        return dict(result)

    async def update_chat_data(self, chat_id: int, data: dict) -> None:
        """Update chat_data for a single chat."""
        redis_key = f"{self.prefix}:chatdata:{chat_id}"
        if data:
            await self.redis.hset(redis_key, mapping=self._serialize_values(data))
            await self.redis.expire(redis_key, CHAT_DATA_TTL)
        else:
            await self.redis.delete(redis_key)

    # ── Bot Data (disabled — we manage this manually) ────────

    async def get_bot_data(self) -> dict:
        return {}

    async def update_bot_data(self, data: dict) -> None:
        pass

    # ── Callback Data (disabled) ─────────────────────────────

    async def get_callback_data(self) -> None:
        return None

    async def update_callback_data(self, data) -> None:
        pass

    # ── Lifecycle ────────────────────────────────────────────

    async def refresh_user_data(self, user_id: int, user_data: dict) -> dict:
        return user_data

    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> dict:
        return chat_data

    async def refresh_bot_data(self, bot_data: dict) -> dict:
        return bot_data

    async def drop_user_data(self, user_id: int) -> None:
        await self.redis.delete(f"{self.prefix}:userdata:{user_id}")

    async def drop_chat_data(self, chat_id: int) -> None:
        await self.redis.delete(f"{self.prefix}:chatdata:{chat_id}")

    async def flush(self) -> None:
        """Called on shutdown. All writes are immediate, so nothing to flush."""
        pass

    # ── Serialization helpers ────────────────────────────────

    def _serialize_key(self, key: tuple) -> str:
        """Serialize a conversation key tuple to a string."""
        return "|".join(str(k) for k in key)

    def _deserialize_key(self, key_str: str) -> tuple:
        """Deserialize a conversation key string back to a tuple."""
        parts = key_str.split("|")
        return tuple(int(p) for p in parts)

    def _serialize_values(self, data: dict) -> dict[str, str]:
        """Serialize dict values to strings for Redis hash storage."""
        result = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                result[str(k)] = json.dumps(v)
            else:
                result[str(k)] = str(v)
        return result

    def _deserialize_values(self, raw: dict[str, str]) -> dict[str, Any]:
        """Deserialize Redis hash values back to Python types."""
        result = {}
        for k, v in raw.items():
            # Try JSON first (for dicts/lists)
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                # Try int
                try:
                    result[k] = int(v)
                except ValueError:
                    # Try float
                    try:
                        result[k] = float(v)
                    except ValueError:
                        result[k] = v
        return result
