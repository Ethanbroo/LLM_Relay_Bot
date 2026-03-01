"""
Tests for progress.py (ProgressReporter).

Tests the ProgressReporter's throttling logic, format output,
and error handling on BadRequest.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_bot.progress import (
    ProgressReporter,
    ExecutionProgress,
    MIN_EDIT_INTERVAL,
)


def _make_reporter() -> tuple[ProgressReporter, MagicMock]:
    """Create a ProgressReporter with a mock context."""
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    reporter = ProgressReporter(chat_id=12345, context=context)
    return reporter, context


def test_execution_progress_defaults():
    p = ExecutionProgress()
    assert p.current_phase == "Initializing"
    assert p.phase_number == 0
    assert p.total_phases == 0
    assert p.turn_count == 0
    assert p.max_turns == 0
    assert p.files_created == []
    assert p.files_modified == []
    assert p.elapsed_seconds == 0.0
    assert p.cost_usd == 0.0
    assert p.is_paused is False
    assert p.last_agent_message == ""


def test_format_progress_initial():
    reporter, _ = _make_reporter()
    reporter._title = "Test Build"
    text = reporter._format_progress()

    assert "Test Build" in text
    assert "Initializing" in text
    assert "0/0" in text  # turns
    assert "none yet" in text  # files


def test_format_progress_with_data():
    reporter, _ = _make_reporter()
    reporter._title = "Landing Page"
    reporter.progress.current_phase = "Code Generation"
    reporter.progress.phase_number = 3
    reporter.progress.total_phases = 5
    reporter.progress.turn_count = 24
    reporter.progress.max_turns = 50
    reporter.progress.files_created = ["a.py", "b.py", "c.py"]
    reporter.progress.files_modified = ["d.py"]
    reporter.progress.elapsed_seconds = 252  # 4m 12s
    reporter.progress.cost_usd = 0.0283

    text = reporter._format_progress()

    assert "Landing Page" in text
    assert "Code Generation" in text
    assert "3/5" in text
    assert "24/50" in text
    assert "3 created" in text
    assert "1 modified" in text
    assert "4m 12s" in text
    assert "$0.0283" in text


def test_format_progress_with_agent_message():
    reporter, _ = _make_reporter()
    reporter._title = "Build"
    reporter.progress.last_agent_message = "Designing the component hierarchy..."

    text = reporter._format_progress()
    assert "Designing the component hierarchy..." in text


def test_format_progress_truncates_long_agent_message():
    reporter, _ = _make_reporter()
    reporter._title = "Build"
    reporter.progress.last_agent_message = "A" * 200

    text = reporter._format_progress()
    assert "..." in text


def test_format_progress_paused():
    reporter, _ = _make_reporter()
    reporter._title = "Build"
    reporter.progress.is_paused = True

    text = reporter._format_progress()
    assert "PAUSED" in text


def test_format_progress_bar():
    reporter, _ = _make_reporter()
    reporter._title = "Build"
    reporter.progress.phase_number = 6
    reporter.progress.total_phases = 10

    text = reporter._format_progress()
    # 60% = 6 filled blocks
    assert "\u2588" * 6 in text


def test_format_short_elapsed():
    reporter, _ = _make_reporter()
    reporter._title = "Build"
    reporter.progress.elapsed_seconds = 45

    text = reporter._format_progress()
    assert "45s" in text


@pytest.mark.asyncio
async def test_start_sends_message():
    reporter, context = _make_reporter()

    mock_message = MagicMock()
    context.bot.send_message = AsyncMock(return_value=mock_message)

    await reporter.start("Test Project")

    context.bot.send_message.assert_awaited_once()
    assert reporter._message == mock_message
    assert reporter._title == "Test Project"


@pytest.mark.asyncio
async def test_update_throttles():
    reporter, _ = _make_reporter()
    reporter._message = MagicMock()
    reporter._message.edit_text = AsyncMock(return_value=reporter._message)
    reporter._last_edit_time = time.monotonic()  # Just edited

    await reporter.update()

    # Should be throttled — no edit call
    reporter._message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_without_message_is_noop():
    reporter, _ = _make_reporter()
    reporter._message = None

    # Should not raise
    await reporter.update()


@pytest.mark.asyncio
async def test_finish_edits_message():
    reporter, _ = _make_reporter()
    mock_message = MagicMock()
    mock_message.edit_text = AsyncMock(return_value=mock_message)
    reporter._message = mock_message

    await reporter.finish("Build complete!")

    mock_message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_finish_fallback_on_bad_request():
    from telegram.error import BadRequest

    reporter, context = _make_reporter()
    mock_message = MagicMock()
    mock_message.edit_text = AsyncMock(side_effect=BadRequest("message gone"))
    reporter._message = mock_message
    context.bot.send_message = AsyncMock()

    await reporter.finish("Build complete!")

    # Should fall back to sending a new message
    context.bot.send_message.assert_awaited_once()


def test_min_edit_interval_value():
    assert MIN_EDIT_INTERVAL == 2.0
