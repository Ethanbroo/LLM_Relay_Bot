"""
Build vs. edit disambiguation keyboard.

Shown when the classifier detects a message that could be either a new
build or an edit to an existing project (e.g., "I need a contact form"
— is this a new project or adding to an existing one?).
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_vs_edit_keyboard() -> InlineKeyboardMarkup:
    """Two-button keyboard: new build or edit existing.

    Callback data format: "bve_new" or "bve_edit"
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f195 New project", callback_data="bve_new"),
            InlineKeyboardButton("\u270f\ufe0f Edit existing", callback_data="bve_edit"),
        ],
    ])
