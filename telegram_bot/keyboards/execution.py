"""
Execution control keyboard.

Appears at the bottom of the progress message during pipeline execution.
Provides pause/resume, skip phase, and cancel controls.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def execution_control_keyboard(
    is_paused: bool = False,
) -> InlineKeyboardMarkup:
    """Persistent control buttons during execution.

    Callback data format: "exec_pause", "exec_resume", "exec_skip", "exec_cancel"
    """
    pause_btn = (
        InlineKeyboardButton("\u25b6\ufe0f Resume", callback_data="exec_resume")
        if is_paused
        else InlineKeyboardButton("\u23f8 Pause", callback_data="exec_pause")
    )
    return InlineKeyboardMarkup([
        [
            pause_btn,
            InlineKeyboardButton("\u23ed Skip Phase", callback_data="exec_skip"),
            InlineKeyboardButton("\u23f9 Cancel", callback_data="exec_cancel"),
        ],
    ])


def checkpoint_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown at pipeline checkpoints.

    Callback data format: "ckpt_continue", "ckpt_adjust"
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 Continue", callback_data="ckpt_continue"),
            InlineKeyboardButton("\u270f\ufe0f Adjust", callback_data="ckpt_adjust"),
        ],
    ])
