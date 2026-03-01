"""
Semantic anchor approve/restart/edit keyboard.

Shown after the Semantic Anchor Agent produces its summary paragraph.
Three options: approve (proceed to execution), restart (regenerate
questions), or edit (revise the anchor text manually).
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def anchor_approval_keyboard() -> InlineKeyboardMarkup:
    """Three-button keyboard for semantic anchor approval.

    Callback data format: "anchor_approve", "anchor_restart", "anchor_edit"
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 Approve", callback_data="anchor_approve"),
            InlineKeyboardButton("\U0001f504 Restart", callback_data="anchor_restart"),
        ],
        [
            InlineKeyboardButton("\u270f\ufe0f Edit anchor text", callback_data="anchor_edit"),
        ],
    ])
