# telegram_bot/handlers/external.py
"""
External actions (Docs, Calendar, etc.) handler.

Routes EXTERNAL_ACTION intent. In Section 2, external service connectors
are not yet wired — this handler acknowledges the request and explains
what will be available in future phases.

Phase 3 will wire Google Docs, Calendar, email, and other connectors
via the existing connectors/ modules.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


async def handle_external(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Handle EXTERNAL_ACTION intent.

    Section 2: Acknowledges the request and explains limitations.
    Phase 3 will wire the real connector router.
    """
    effective_message = update.effective_message or update.callback_query.message

    # Check if any connectors are available in bot_data
    connectors = context.bot_data.get("connectors", {})

    if connectors:
        # Future: route to the appropriate connector
        await effective_message.reply_text(
            "\U0001f4e4 External service routing is available but "
            "not yet wired to this handler.\n\n"
            "Try rephrasing as a build request, e.g.:\n"
            '"Build me a Google Doc summarizing..."'
        )
    else:
        await effective_message.reply_text(
            "\U0001f4e4 External service actions (Google Docs, Calendar, etc.) "
            "will be fully supported in Phase 3.\n\n"
            "For now, I can help with:\n"
            "\u2022 Building new projects\n"
            "\u2022 Editing existing code\n"
            "\u2022 Answering questions about code\n"
            "\u2022 Research tasks"
        )

    return BotState.IDLE
