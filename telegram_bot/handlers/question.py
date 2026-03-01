# telegram_bot/handlers/question.py
"""
Q&A about built code handler (Path C).

Routes QUESTION intent through PipelineAdapter.run_question() for
single-shot LLM calls with optional session context.

Section 4 changes:
  - Uses SessionManager to find latest session for active project
  - Falls back to user_data session_id if SessionManager unavailable
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.message_utils import send_long_message
from telegram_bot.pipeline_adapter import PipelineAdapter
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


async def handle_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Handle QUESTION intent via PipelineAdapter."""
    effective_message = update.effective_message or update.callback_query.message

    adapter: PipelineAdapter = context.bot_data["pipeline_adapter"]
    project_name = (
        context.user_data.get("selected_project")
        or context.user_data.get("last_project")
    )

    # Section 4: Use SessionManager to find latest session
    session_id = None
    session_manager = context.bot_data.get("session_manager")
    if session_manager and project_name:
        session = await session_manager.get_latest_for_project(project_name)
        if session:
            session_id = session.session_id

    # Fallback to user_data
    if not session_id:
        session_id = context.user_data.get("last_session_id")

    await effective_message.reply_text("\u2753 Looking into that...")

    try:
        answer = await adapter.run_question(
            question=message_text,
            project_name=project_name,
            session_id=session_id,
        )
        await send_long_message(
            update.effective_chat.id,
            context,
            text=answer,
        )
    except Exception as e:
        logger.error("Question handler failed: %s", e, exc_info=True)
        await effective_message.reply_text(
            f"Sorry, I couldn't answer that: {e}"
        )

    return BotState.IDLE
