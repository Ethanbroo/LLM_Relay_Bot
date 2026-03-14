"""
Browser task handler — /task command and browser agent execution flow.

Handles states:
  - BROWSER_TASK_RUNNING: Agent is executing a browser task
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.browser_agent import BrowserAgent, TaskResult
from telegram_bot.browser_client import BrowserClient
from telegram_bot.cookie_vault import load_session, save_session
from telegram_bot.message_utils import send_long_message
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


async def handle_task_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Entry point for /task command. Starts a browser automation task."""
    if not update.message or not update.message.text:
        return BotState.IDLE

    # Extract task description (everything after /task)
    text = update.message.text
    if text.startswith("/task"):
        task_description = text[5:].strip()
    else:
        task_description = text.strip()

    if not task_description:
        await update.message.reply_text(
            "Please describe the browser task.\n\n"
            "Example: /task search Google for plumbing backflow preventer specs\n"
            "Example: /task check my Gmail for emails from Next Supply"
        )
        return BotState.IDLE

    context.user_data["browser_task"] = task_description

    await update.message.reply_text(
        f"Starting browser task...\n\n"
        f"Task: {task_description[:200]}"
    )

    # Launch the browser agent as a background task
    chat_id = update.effective_chat.id

    async def _run_browser_task():
        try:
            client = BrowserClient()
            try:
                agent = BrowserAgent(
                    browser_client=client,
                    on_progress=lambda step, msg, _: _send_progress(
                        chat_id, context, step, msg
                    ),
                )
                context.user_data["browser_agent"] = agent

                result = await agent.run(task_description)
                await _deliver_result(chat_id, context, result)
            finally:
                await client.close()
        except Exception as e:
            logger.error("Browser task failed: %s", e, exc_info=True)
            await send_long_message(
                chat_id, context, text=f"Browser task failed: {e}"
            )

    context.user_data["browser_task_handle"] = asyncio.create_task(
        _run_browser_task()
    )
    return BotState.BROWSER_TASK_RUNNING


async def handle_browser_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle cancel during a running browser task."""
    agent: BrowserAgent | None = context.user_data.get("browser_agent")
    if agent:
        agent.cancel()

    task_handle = context.user_data.get("browser_task_handle")
    if task_handle and not task_handle.done():
        await update.effective_message.reply_text(
            "Cancelling browser task..."
        )
    else:
        await update.effective_message.reply_text(
            "No active browser task to cancel."
        )

    return BotState.BROWSER_TASK_RUNNING  # Will transition to IDLE when task finishes


async def handle_message_during_browser_task(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle text messages while a browser task is running."""
    await update.message.reply_text(
        "A browser task is currently running. "
        "Send /cancel to stop it, or wait for it to complete."
    )
    return BotState.BROWSER_TASK_RUNNING


async def _send_progress(
    chat_id: int, context: ContextTypes.DEFAULT_TYPE, step: int, message: str
):
    """Send a progress update to the user (throttled)."""
    # Only send every 5 steps to avoid spamming
    if step % 5 == 1 or step == 1:
        try:
            await context.bot.send_message(chat_id, f"Browser: {message}")
        except Exception:
            pass  # Non-critical


async def _deliver_result(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    result: TaskResult,
):
    """Send the task result to the user via Telegram."""
    if result.success:
        text = f"Browser task complete ({result.steps_taken} steps)\n\n"
        if result.summary:
            text += f"{result.summary}\n"
        if result.data:
            text += f"\n{result.data[:3000]}"
    else:
        text = f"Browser task failed ({result.steps_taken} steps)\n\n"
        if result.reason:
            text += f"Reason: {result.reason}\n"
        if result.suggestion:
            text += f"Suggestion: {result.suggestion}\n"
        if result.error:
            text += f"Error: {result.error}\n"

    await send_long_message(chat_id, context, text=text)

    # Send screenshot if available
    if result.screenshot_b64:
        try:
            import base64
            img_bytes = base64.b64decode(result.screenshot_b64)
            await context.bot.send_photo(chat_id, photo=img_bytes)
        except Exception:
            logger.warning("Failed to send screenshot", exc_info=True)

    # Clean up user_data
    context.user_data.pop("browser_agent", None)
    context.user_data.pop("browser_task_handle", None)
    context.user_data.pop("browser_task", None)
