# telegram_bot/message_utils.py
"""
Utilities for sending messages that respect Telegram's 4,096-character limit.

Provides send_long_message() which splits at paragraph boundaries and
attaches the inline keyboard only to the last chunk. Also provides
edit_long_message() which truncates for edits (since you can't split
an edit into multiple messages).

This module is imported by virtually every handler that sends text to
the user. It should be treated as a core utility alongside auth.py
and config.py.
"""

import logging
from typing import Optional

from telegram import InlineKeyboardMarkup, Message
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# 4000 instead of 4096 leaves headroom for Markdown formatting
# overhead that might expand during rendering (bold markers, etc).
MAX_MESSAGE_LENGTH = 4000


async def send_long_message(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = "Markdown",
) -> Message:
    """Send a message, splitting into multiple if it exceeds 4096 chars.

    Splitting strategy (in priority order):
      1. Paragraph boundary (double newline)
      2. Sentence boundary (". " or ".\n")
      3. Word boundary (space)
      4. Hard cut at character limit

    The inline keyboard is attached only to the LAST message in the
    sequence. Earlier chunks get a "continued..." footer so the user
    knows more is coming.

    Returns the last Message object sent (the one with the keyboard).
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    chunks = _split_text(text, MAX_MESSAGE_LENGTH)
    last_message = None

    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        markup = reply_markup if is_last else None

        # Add continuation indicator to non-final chunks
        if not is_last:
            chunk = chunk + "\n\n_continued\u2026_"

        last_message = await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode=parse_mode,
            reply_markup=markup,
        )

    return last_message


async def edit_long_message(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = "Markdown",
) -> Message:
    """Edit a message, truncating if text exceeds 4096 chars.

    Unlike send_long_message, edits cannot be split into multiple
    messages. Truncation is the only option. This is acceptable
    because the primary use case (ProgressReporter) should never
    exceed the limit. For completion summaries that might be long,
    handlers should use send_long_message instead of editing.

    Silently handles "message is not modified" errors (same text
    sent twice in a row).
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        try:
            return await message.edit_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return message
            raise

    truncated = text[: MAX_MESSAGE_LENGTH - 40] + "\n\n_\u2026message truncated_"
    try:
        return await message.edit_text(
            text=truncated,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return message
        raise


def _split_text(text: str, max_length: int) -> list[str]:
    """Split text into chunks that each fit within max_length."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_length:
        split_at = _find_split_point(remaining, max_length)
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks


def _find_split_point(text: str, max_length: int) -> int:
    """Find the best character index to split at, within max_length.

    Preference order: paragraph break > sentence end > word break > hard cut.
    The min_split threshold (1/3 of max_length) prevents pathologically
    short first chunks when a good split point happens to be very early.
    """
    search_region = text[:max_length]
    min_split = max_length // 3

    # Paragraph boundary (double newline) — strongest break
    last_para = search_region.rfind("\n\n")
    if last_para > min_split:
        return last_para + 2

    # Sentence boundary — next best
    for delimiter in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
        last_sentence = search_region.rfind(delimiter)
        if last_sentence > min_split:
            return last_sentence + len(delimiter)

    # Word boundary — avoid splitting mid-word
    last_space = search_region.rfind(" ")
    if last_space > min_split:
        return last_space + 1

    # Hard cut — last resort
    return max_length
