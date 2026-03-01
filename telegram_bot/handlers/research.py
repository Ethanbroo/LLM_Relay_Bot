# telegram_bot/handlers/research.py
"""
Research request flow handler.

Routes RESEARCH intent through PipelineAdapter.run_research() for
information gathering and synthesis without producing code artifacts.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.message_utils import send_long_message
from telegram_bot.pipeline_adapter import PipelineAdapter
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


async def handle_research(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Handle RESEARCH intent via PipelineAdapter."""
    effective_message = update.effective_message or update.callback_query.message

    adapter: PipelineAdapter = context.bot_data["pipeline_adapter"]

    await effective_message.reply_text(
        "\U0001f50d Researching that for you..."
    )

    try:
        result = await adapter.run_research(query=message_text)
        await send_long_message(
            update.effective_chat.id,
            context,
            text=result,
        )
    except Exception as e:
        logger.error("Research handler failed: %s", e, exc_info=True)
        await effective_message.reply_text(
            f"Sorry, the research request failed: {e}"
        )

    return BotState.IDLE
