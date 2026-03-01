"""
Shared keyboard utilities.

Common patterns and helpers used across multiple keyboard modules.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def single_button_keyboard(
    text: str, callback_data: str
) -> InlineKeyboardMarkup:
    """Create a keyboard with a single button. Useful for confirmations."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text, callback_data=callback_data)],
    ])


def yes_no_keyboard(
    prefix: str,
    yes_text: str = "\u2705 Yes",
    no_text: str = "\u274c No",
) -> InlineKeyboardMarkup:
    """Create a simple yes/no keyboard.

    Callback data: "{prefix}_yes", "{prefix}_no"
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(yes_text, callback_data=f"{prefix}_yes"),
            InlineKeyboardButton(no_text, callback_data=f"{prefix}_no"),
        ],
    ])


def remove_keyboard_markup() -> None:
    """Return None to indicate no keyboard (removes any existing one).

    In PTB, passing reply_markup=None or not passing it at all
    leaves the keyboard as-is. To explicitly remove an inline keyboard,
    you edit the message with reply_markup=InlineKeyboardMarkup([]).
    This helper documents that pattern.
    """
    return InlineKeyboardMarkup([])
