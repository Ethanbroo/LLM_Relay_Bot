"""
ProgressReporter — single-message update pattern for pipeline execution.

During execution, the bot sends one message and then edits it repeatedly
to show current progress. This avoids spamming the chat with dozens of
messages for a single build. The ProgressReporter class manages this pattern.

The reporter throttles edits to MIN_EDIT_INTERVAL seconds to respect
Telegram's rate limits (~6 edits/second observed safe rate, but we
throttle to one every 2 seconds for reliability and to avoid flicker).

The progress reporter doesn't parse the Claude Code stream directly —
it receives structured updates from the executor. The interface is:

    reporter.progress.current_phase = "Code Generation"
    reporter.progress.turn_count = event_data["num_turns"]
    reporter.progress.files_created.append(tool_use["input"]["file_path"])
    reporter.progress.last_agent_message = text_block["text"][:200]
    reporter.progress.cost_usd = running_cost_total
    await reporter.update()
"""

from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from telegram import Message, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter

from telegram_bot.keyboards.execution import execution_control_keyboard

logger = logging.getLogger(__name__)

# Minimum interval between edit_text() calls, in seconds.
# Telegram rate-limits message edits; community-observed safe rate
# is ~6 edits/second, but we throttle to one every 2 seconds
# for reliability and to avoid flicker.
MIN_EDIT_INTERVAL = 2.0


@dataclass
class ExecutionProgress:
    """Mutable state tracking execution progress.

    Updated by the pipeline executor as phases complete. The
    ProgressReporter reads this state when generating the display text.
    """
    current_phase: str = "Initializing"
    phase_number: int = 0
    total_phases: int = 0
    turn_count: int = 0
    max_turns: int = 0
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0
    is_paused: bool = False
    last_agent_message: str = ""    # Most recent text output from Claude


class ProgressReporter:
    """Manages a single Telegram message that shows execution progress.

    The reporter throttles updates to avoid hitting Telegram's rate limits.
    It formats progress data into a compact, readable message with phase
    info, file counts, elapsed time, and cost.

    Usage:
        reporter = ProgressReporter(chat_id, context)
        await reporter.start("Building: Cardinal Sales Landing Page")

        # During execution, update the progress state:
        reporter.progress.current_phase = "Architecture"
        reporter.progress.phase_number = 2
        await reporter.update()

        # When done:
        await reporter.finish("Build complete!")
    """

    def __init__(self, chat_id: int, context):
        self._chat_id = chat_id
        self._context = context
        self._message: Optional[Message] = None
        self._last_edit_time: float = 0.0
        self._title: str = ""
        self.progress = ExecutionProgress()

    async def start(self, title: str) -> None:
        """Send the initial progress message."""
        self._title = title
        text = self._format_progress()
        keyboard = execution_control_keyboard(is_paused=False)
        self._message = await self._context.bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        self._last_edit_time = time.monotonic()

    async def update(self) -> None:
        """Edit the progress message with current state.

        Throttles to MIN_EDIT_INTERVAL to respect Telegram rate limits.
        If the message hasn't changed (same text), the edit is skipped
        to avoid BadRequest errors from Telegram.
        """
        if self._message is None:
            return

        # Throttle
        now = time.monotonic()
        if (now - self._last_edit_time) < MIN_EDIT_INTERVAL:
            return

        text = self._format_progress()
        keyboard = execution_control_keyboard(is_paused=self.progress.is_paused)

        try:
            self._message = await self._message.edit_text(
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            self._last_edit_time = time.monotonic()
        except BadRequest as e:
            # "Message is not modified" — text unchanged, safe to ignore
            if "not modified" in str(e).lower():
                pass
            else:
                logger.warning("Progress edit failed: %s", e)
        except RetryAfter as e:
            # Rate limited — wait and don't retry (next update cycle will catch up)
            logger.warning("Rate limited on progress edit, waiting %ds", e.retry_after)
            await asyncio.sleep(e.retry_after)

    async def finish(self, summary: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> None:
        """Replace the progress message with a final summary."""
        if self._message is None:
            return

        try:
            self._message = await self._message.edit_text(
                text=summary,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except BadRequest:
            # If edit fails, send a new message instead
            await self._context.bot.send_message(
                chat_id=self._chat_id,
                text=summary,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

    def _format_progress(self) -> str:
        """Generate the progress display text.

        Format:
        ⚙️ Building: Cardinal Sales Landing Page
        ═══════════════════════
        📍 Phase: Architecture (2/5)
        🔄 Turns: 12/50
        📁 Files: 3 created, 1 modified
        ⏱ Elapsed: 2m 34s
        💰 Cost: $0.0142
        ─────────────────────
        💬 "Designing the component hierarchy..."
        """
        p = self.progress

        # Phase progress bar: [██████░░░░] 60%
        if p.total_phases > 0:
            pct = int((p.phase_number / p.total_phases) * 100)
            filled = int(pct / 10)
            bar = "\u2588" * filled + "\u2591" * (10 - filled)
            phase_line = f"\U0001f4cd Phase: {p.current_phase} ({p.phase_number}/{p.total_phases}) [{bar}]"
        else:
            phase_line = f"\U0001f4cd Phase: {p.current_phase}"

        # Elapsed time formatting
        mins, secs = divmod(int(p.elapsed_seconds), 60)
        elapsed_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        # File counts
        file_parts = []
        if p.files_created:
            file_parts.append(f"{len(p.files_created)} created")
        if p.files_modified:
            file_parts.append(f"{len(p.files_modified)} modified")
        file_str = ", ".join(file_parts) if file_parts else "none yet"

        # Pause indicator
        status_emoji = "\u23f8" if p.is_paused else "\u2699\ufe0f"
        status_suffix = " (PAUSED)" if p.is_paused else ""

        # Last agent message (truncated)
        agent_line = ""
        if p.last_agent_message:
            truncated = p.last_agent_message[:120]
            if len(p.last_agent_message) > 120:
                truncated += "..."
            agent_line = f"\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\U0001f4ac _{truncated}_"

        return (
            f"{status_emoji} *Building: {self._title}*{status_suffix}\n"
            f"\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n"
            f"{phase_line}\n"
            f"\U0001f504 Turns: {p.turn_count}/{p.max_turns}\n"
            f"\U0001f4c1 Files: {file_str}\n"
            f"\u23f1 Elapsed: {elapsed_str}\n"
            f"\U0001f4b0 Cost: ${p.cost_usd:.4f}"
            f"{agent_line}"
        )
