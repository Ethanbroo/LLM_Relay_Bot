"""
Approval manager for browser agent human-in-the-loop flow.

Manages pending approval requests using asyncio.Event for synchronization
between the ReAct loop (which waits) and Telegram callback handlers (which
resolve). Also handles sending approval messages and processing callbacks.

Key components:
  - PendingApproval: represents a single pending approval with an asyncio.Event
  - ApprovalManager: tracks all pending approvals across active tasks
  - send_approval_request: sends the screenshot + action summary to Telegram
  - handle_approval_callback: processes Approve/Reject/Cancel button presses
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "300"))


class PendingApproval:
    """Represents a pending approval request."""

    __slots__ = ("task_id", "action_id", "event", "approved", "cancelled", "created_at")

    def __init__(self, task_id: str, action_id: str):
        self.task_id = task_id
        self.action_id = action_id
        self.event = asyncio.Event()
        self.approved: bool | None = None
        self.cancelled: bool = False
        self.created_at = datetime.now(timezone.utc)

    def resolve(self, approved: bool | None = None, cancelled: bool = False):
        """Resolve the approval request and unblock the waiting coroutine."""
        self.approved = approved
        self.cancelled = cancelled
        self.event.set()


class ApprovalManager:
    """Manages pending approval requests across all active tasks."""

    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}

    def create(self, task_id: str, action_id: str) -> PendingApproval:
        key = f"{task_id}:{action_id}"
        pending = PendingApproval(task_id, action_id)
        self._pending[key] = pending
        return pending

    def get_pending(self, task_id: str, action_id: str) -> PendingApproval | None:
        key = f"{task_id}:{action_id}"
        return self._pending.get(key)

    def remove(self, task_id: str, action_id: str):
        key = f"{task_id}:{action_id}"
        self._pending.pop(key, None)

    def get_pending_for_task(self, task_id: str) -> list[PendingApproval]:
        return [p for p in self._pending.values() if p.task_id == task_id]

    def cancel_all_for_task(self, task_id: str):
        """Cancel all pending approvals for a task."""
        for pending in self.get_pending_for_task(task_id):
            pending.resolve(cancelled=True)
        keys_to_remove = [
            k for k, v in self._pending.items() if v.task_id == task_id
        ]
        for k in keys_to_remove:
            del self._pending[k]


def generate_action_id() -> str:
    """Generate a short unique ID for an action."""
    return secrets.token_hex(4)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def send_approval_request(
    bot,
    chat_id: int,
    task_id: str,
    action_id: str,
    screenshot_b64: str | None,
    current_url: str,
    action_summary: str,
    reasoning: str,
    action_name: str,
) -> int:
    """Send an approval request to the user. Returns the message ID."""

    caption_parts = [
        "Browser Agent -- Approval Required\n",
        f"URL: {_truncate(current_url, 80)}",
        f"Action: {action_summary}\n",
        f"Reasoning: {_truncate(reasoning, 200)}",
    ]
    caption = "\n".join(caption_parts)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Approve",
                callback_data=f"ba_approve:{task_id}:{action_id}",
            ),
            InlineKeyboardButton(
                "Reject",
                callback_data=f"ba_reject:{task_id}:{action_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "Cancel Task",
                callback_data=f"ba_cancel:{task_id}:{action_id}",
            ),
        ],
    ])

    if screenshot_b64:
        try:
            screenshot_bytes = base64.b64decode(screenshot_b64)
            message = await bot.send_photo(
                chat_id=chat_id,
                photo=screenshot_bytes,
                caption=caption[:1024],  # Telegram photo caption limit
                reply_markup=keyboard,
            )
            return message.message_id
        except Exception:
            logger.warning("Failed to send screenshot with approval, falling back to text")

    # Fallback: text-only approval message
    message = await bot.send_message(
        chat_id=chat_id,
        text=caption,
        reply_markup=keyboard,
    )
    return message.message_id


async def wait_for_approval(
    pending: PendingApproval,
    timeout_seconds: int = APPROVAL_TIMEOUT_SECONDS,
) -> str:
    """Wait for user to respond to an approval request.

    Returns: 'approved', 'rejected', 'cancelled', or 'timeout'
    """
    try:
        await asyncio.wait_for(pending.event.wait(), timeout=timeout_seconds)
        if pending.cancelled:
            return "cancelled"
        return "approved" if pending.approved else "rejected"
    except asyncio.TimeoutError:
        return "timeout"


async def handle_approval_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle Approve/Reject/Cancel button presses for browser agent."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return

    action, task_id, action_id = parts

    approval_manager: ApprovalManager | None = context.bot_data.get("approval_manager")
    if not approval_manager:
        await query.edit_message_caption(
            caption="System error: approval manager not available.",
            reply_markup=None,
        )
        return

    pending = approval_manager.get_pending(task_id, action_id)
    if pending is None:
        # Try to edit caption; fall back to editing text for text-only messages
        try:
            await query.edit_message_caption(
                caption="This approval request has expired.",
                reply_markup=None,
            )
        except Exception:
            try:
                await query.edit_message_text(
                    text="This approval request has expired.",
                    reply_markup=None,
                )
            except Exception:
                pass
        return

    if action == "ba_approve":
        pending.resolve(approved=True)
        status_text = "Approved"
    elif action == "ba_reject":
        pending.resolve(approved=False)
        status_text = "Rejected"
    elif action == "ba_cancel":
        pending.resolve(cancelled=True)
        status_text = "Task Cancelled"
    else:
        return

    # Update the message to show the decision and remove buttons
    original_caption = ""
    try:
        original_caption = query.message.caption or ""
        await query.edit_message_caption(
            caption=f"{original_caption}\n\n{status_text}",
            reply_markup=None,
        )
    except Exception:
        try:
            original_text = query.message.text or ""
            await query.edit_message_text(
                text=f"{original_text}\n\n{status_text}",
                reply_markup=None,
            )
        except Exception:
            pass
