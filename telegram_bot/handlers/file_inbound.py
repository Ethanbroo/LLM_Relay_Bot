"""
Inbound file handling.

Downloads files attached to Telegram messages (documents, photos, audio,
video) to the VPS workspace and makes them available to the pipeline.
If accompanying text is present, it's classified and used as the build
instruction; otherwise the user is prompted to describe what they want.

Telegram's download limit is 20 MB. Files larger than this cannot be
downloaded via the Bot API — get_file() will fail. The handler catches
this and tells the user to use an alternative upload method.
"""

from __future__ import annotations

import logging
import uuid

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from telegram_bot.states import BotState
from telegram_bot.media import download_telegram_file

logger = logging.getLogger(__name__)


async def handle_inbound_file(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle incoming files (documents, photos, audio, video).

    Downloads the file, determines its type and purpose, and either
    attaches it to the current build session or starts a new one.
    If the message includes caption text, that text is classified
    and used as the build instruction.
    """
    message = update.message

    # Generate or reuse session ID
    session_id = context.user_data.get("session_id")
    if not session_id:
        session_id = uuid.uuid4().hex[:12]
        context.user_data["session_id"] = session_id

    try:
        file_path = await download_telegram_file(message, session_id, context)
    except BadRequest as e:
        if "file is too big" in str(e).lower():
            await message.reply_text(
                "\u26a0\ufe0f That file is too large for Telegram's 20 MB download limit.\n\n"
                "Please upload it via SCP, Google Drive link, or another method "
                "and share the path or URL here."
            )
            return ConversationHandler.END
        raise

    if file_path is None:
        await message.reply_text(
            "\U0001f4ce I couldn't process that file. "
            "Please try again or describe what you'd like done."
        )
        return ConversationHandler.END

    # Store the file path for downstream handlers
    inbound_files = context.user_data.setdefault("inbound_files", [])
    inbound_files.append(str(file_path))

    # Check for caption text (instructions accompanying the file)
    caption = message.caption or ""

    if caption.strip():
        # Classify the caption as if it were a freeform message
        await message.reply_text(
            f"\U0001f4ce File saved: `{file_path.name}`\n"
            f"Processing your instructions...",
            parse_mode="Markdown",
        )
        # Route through the classifier with the caption text
        from telegram_bot.handlers.start import handle_freeform_message
        context.user_data["transcribed_voice"] = None  # Not a voice message
        return await handle_freeform_message(update, context, override_text=caption)

    # No caption — ask the user what they want done with the file
    await message.reply_text(
        f"\U0001f4ce File saved: `{file_path.name}`\n\n"
        "What would you like me to do with this file? "
        "Type your instructions below.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END
