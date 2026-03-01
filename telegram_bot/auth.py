"""
Authentication and authorization for the Telegram bot.

The @restricted decorator gates every handler that processes user content.
It checks the Telegram user_id against an allowlist loaded from the
TELEGRAM_ALLOWED_USERS environment variable.

Security layers (in order):
  1. Nginx TLS termination + Telegram IP whitelist (Phase 2 / VPS)
  2. PTB secret_token header validation (automatic in webhook mode)
  3. This @restricted decorator (user_id allowlist)
"""

from __future__ import annotations

import functools
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Parse comma-separated user IDs from environment at module load time.
# This set is immutable for the lifetime of the process. To add users,
# update .env and restart the bot container.
ALLOWED_USER_IDS: frozenset[int] = frozenset(
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
)

# The bot owner always has access, even if the env var is misconfigured.
# This prevents a lockout scenario where a typo in .env blocks everyone.
BOT_OWNER_ID: int = int(os.environ.get("TELEGRAM_BOT_OWNER_ID", "0"))


def restricted(func):
    """Decorator that rejects messages from users not in the allowlist.

    This must wrap every handler that processes user content. It runs before
    any LLM calls or file operations, so unauthorized users never trigger
    API costs or side effects.

    The decorator preserves the wrapped function's metadata via functools.wraps,
    which is required because python-telegram-bot introspects handler function
    signatures for type checking and error reporting.
    """

    @functools.wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user = update.effective_user

        # No user identity available (shouldn't happen in private chats,
        # but defensive coding for channel posts or anonymous admin messages)
        if user is None:
            logger.warning(
                "Received update with no effective_user: %s", update.update_id
            )
            return

        # Check against both the allowlist and the owner override
        if user.id not in ALLOWED_USER_IDS and user.id != BOT_OWNER_ID:
            logger.warning(
                "Unauthorized access attempt: user_id=%d, username=%s, name=%s",
                user.id,
                user.username or "N/A",
                user.full_name,
            )
            # Send a single rejection message. Do NOT reveal what the bot does
            # or why access was denied — this minimizes information leakage.
            if update.effective_message:
                await update.effective_message.reply_text(
                    "\u26d4 This bot is private. Access is not available."
                )
            return

        # Authorized — proceed to the actual handler
        return await func(update, context, *args, **kwargs)

    return wrapper
