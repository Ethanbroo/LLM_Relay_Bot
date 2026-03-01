"""
Voice message -> transcription -> re-route handler.

Flow:
1. Show typing indicator (user sees "recording..." while we process)
2. Download the OGG file from Telegram
3. Transcribe with Whisper
4. Show the transcription to the user (so they can verify/correct)
5. Feed the transcribed text into the normal message classifier

This means voice messages have the same capability as text messages —
you can say "build me a landing page" by voice and it routes correctly.
"""

from __future__ import annotations

import os
import tempfile
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from telegram_bot.voice_transcriber import transcribe_voice
from telegram_bot.handlers.start import handle_freeform_message

logger = logging.getLogger(__name__)


async def handle_voice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle incoming voice messages.

    Downloads the OGG, transcribes via Whisper, shows the transcription
    for verification, then feeds the text into the normal classifier pipeline.

    Returns:
        The BotState from the classifier routing (same as a text message).
    """
    voice = update.message.voice

    # Show typing indicator while processing
    await update.message.chat.send_action(ChatAction.TYPING)

    # Download to a temp file
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        file_obj = await context.bot.get_file(voice.file_id)
        await file_obj.download_to_drive(tmp_path)

        # Transcribe
        transcribed_text = await transcribe_voice(tmp_path)

        if not transcribed_text:
            await update.message.reply_text(
                "\U0001f399\ufe0f I couldn't make out what you said. "
                "Could you try again or type it out?"
            )
            from telegram_bot.states import BotState
            return BotState.IDLE

        # Show transcription for verification
        await update.message.reply_text(
            f"\U0001f399\ufe0f I heard: _{transcribed_text}_\n\nProcessing...",
            parse_mode="Markdown",
        )

        # Re-route through the normal text pipeline.
        # We inject the transcribed text into the message context so the
        # classifier and downstream handlers see it as a normal text message.
        context.user_data["transcribed_voice"] = transcribed_text

        # Call the freeform message handler with the transcribed text
        return await handle_freeform_message(update, context, override_text=transcribed_text)

    finally:
        # Clean up temp file
        os.unlink(tmp_path)
