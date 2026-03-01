# telegram_bot/handlers/conversational.py
"""
Conversational / small talk handler.

Handles messages classified as CONVERSATIONAL — greetings, meta-questions
about the bot, and anything that doesn't fit the other five categories.

Routes through PipelineAdapter.run_conversational() for dynamic responses
(VPS mode) or returns a static response (mock mode).
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.message_utils import send_long_message
from telegram_bot.pipeline_adapter import PipelineAdapter
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


async def handle_conversational(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Handle CONVERSATIONAL intent via PipelineAdapter."""
    effective_message = update.effective_message or update.callback_query.message

    adapter: PipelineAdapter = context.bot_data["pipeline_adapter"]

    try:
        response = await adapter.run_conversational(message=message_text)
        await send_long_message(
            update.effective_chat.id,
            context,
            text=response,
        )
    except Exception as e:
        logger.error("Conversational handler failed: %s", e, exc_info=True)
        await effective_message.reply_text(
            "Hey! I'm the LLM Relay Bot. Send me a description of what "
            "you want to build and I'll take care of the rest.\n\n"
            "Type /help for usage examples."
        )

    return BotState.IDLE
