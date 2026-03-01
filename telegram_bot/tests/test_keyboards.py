"""
Tests for keyboard factory functions.

Validates that all keyboard factory functions produce valid
InlineKeyboardMarkup objects, that callback_data values are unique
across keyboards (no collisions), and that all callback_data strings
are within the 64-byte limit.
"""

from __future__ import annotations

import pytest
from telegram import InlineKeyboardMarkup

from telegram_bot.classifier import Intent, Classification
from telegram_bot.keyboards.clarification import (
    build_intent_clarification_keyboard,
    build_all_intents_keyboard,
    critical_question_keyboard,
    INTENT_LABELS,
)
from telegram_bot.keyboards.disambiguation import build_vs_edit_keyboard
from telegram_bot.keyboards.anchor import anchor_approval_keyboard
from telegram_bot.keyboards.execution import (
    execution_control_keyboard,
    checkpoint_keyboard,
)
from telegram_bot.keyboards.delivery import delivery_keyboard, quick_fix_keyboard
from telegram_bot.keyboards.common import (
    single_button_keyboard,
    yes_no_keyboard,
    remove_keyboard_markup,
)


def _extract_callback_data(keyboard: InlineKeyboardMarkup) -> list[str]:
    """Extract all callback_data strings from a keyboard."""
    data = []
    for row in keyboard.inline_keyboard:
        for button in row:
            if button.callback_data:
                data.append(button.callback_data)
    return data


def _make_classification(
    intent: Intent = Intent.NEW_BUILD,
    confidence: float = 0.45,
    secondary: Intent | None = Intent.EDIT_FIX,
) -> Classification:
    return Classification(
        intent=intent,
        confidence=confidence,
        secondary_intent=secondary,
        raw_message="test message",
        latency_ms=100.0,
        needs_clarification=confidence < 0.60,
    )


# --- Intent Clarification ---

def test_clarification_keyboard_returns_markup():
    kb = build_intent_clarification_keyboard(_make_classification())
    assert isinstance(kb, InlineKeyboardMarkup)


def test_clarification_keyboard_has_primary_and_secondary():
    kb = build_intent_clarification_keyboard(_make_classification())
    data = _extract_callback_data(kb)
    assert "intent_NEW_BUILD" in data
    assert "intent_EDIT_FIX" in data
    assert "intent_OTHER" in data


def test_clarification_keyboard_no_secondary():
    kb = build_intent_clarification_keyboard(
        _make_classification(secondary=None)
    )
    data = _extract_callback_data(kb)
    assert "intent_NEW_BUILD" in data
    assert "intent_OTHER" in data
    assert len(data) == 2  # primary + "other"


def test_all_intents_keyboard():
    kb = build_all_intents_keyboard()
    data = _extract_callback_data(kb)
    assert len(data) == 6  # One for each Intent
    for intent in Intent:
        assert f"intent_{intent.value}" in data


def test_intent_labels_cover_all_intents():
    for intent in Intent:
        assert intent in INTENT_LABELS


# --- Critical Questions ---

def test_critical_question_keyboard():
    kb = critical_question_keyboard(0, 3, "Standard responsive design")
    assert isinstance(kb, InlineKeyboardMarkup)
    data = _extract_callback_data(kb)
    assert "defans_0" in data
    # First question should NOT have "skip all" button
    assert "defans_all" not in data


def test_critical_question_keyboard_skip_remaining():
    kb = critical_question_keyboard(1, 3, "Default answer")
    data = _extract_callback_data(kb)
    assert "defans_1" in data
    assert "defans_all" in data  # Available after first question


def test_critical_question_truncates_long_default():
    long_answer = "A" * 60
    kb = critical_question_keyboard(0, 1, long_answer)
    for row in kb.inline_keyboard:
        for button in row:
            if button.callback_data == "defans_0":
                assert "..." in button.text
                assert len(button.text) < 80


# --- Disambiguation ---

def test_build_vs_edit_keyboard():
    kb = build_vs_edit_keyboard()
    data = _extract_callback_data(kb)
    assert "bve_new" in data
    assert "bve_edit" in data
    assert len(data) == 2


# --- Anchor ---

def test_anchor_keyboard():
    kb = anchor_approval_keyboard()
    data = _extract_callback_data(kb)
    assert "anchor_approve" in data
    assert "anchor_restart" in data
    assert "anchor_edit" in data
    assert len(data) == 3


# --- Execution ---

def test_execution_keyboard_not_paused():
    kb = execution_control_keyboard(is_paused=False)
    data = _extract_callback_data(kb)
    assert "exec_pause" in data
    assert "exec_skip" in data
    assert "exec_cancel" in data
    assert "exec_resume" not in data


def test_execution_keyboard_paused():
    kb = execution_control_keyboard(is_paused=True)
    data = _extract_callback_data(kb)
    assert "exec_resume" in data
    assert "exec_pause" not in data


def test_checkpoint_keyboard():
    kb = checkpoint_keyboard()
    data = _extract_callback_data(kb)
    assert "ckpt_continue" in data
    assert "ckpt_adjust" in data


# --- Delivery ---

def test_delivery_keyboard_all_options():
    kb = delivery_keyboard(
        github_pr_url="https://github.com/test/pr/1",
        preview_url="https://preview.test.com",
        has_downloadable_files=True,
        has_documentation=True,
        is_deployable=True,
    )
    data = _extract_callback_data(kb)
    assert "dlvr_download" in data
    assert "dlvr_docs" in data
    assert "dlvr_deploy" in data


def test_delivery_keyboard_minimal():
    kb = delivery_keyboard(
        has_downloadable_files=False,
        has_documentation=False,
        is_deployable=False,
    )
    data = _extract_callback_data(kb)
    assert len(data) == 0  # No callback buttons (URL buttons don't have callback_data)


def test_quick_fix_keyboard():
    kb = quick_fix_keyboard()
    data = _extract_callback_data(kb)
    assert "qfix_approve" in data
    assert "qfix_retry" in data
    assert "qfix_revert" in data


# --- Common ---

def test_single_button_keyboard():
    kb = single_button_keyboard("Click me", "test_click")
    data = _extract_callback_data(kb)
    assert data == ["test_click"]


def test_yes_no_keyboard():
    kb = yes_no_keyboard("confirm")
    data = _extract_callback_data(kb)
    assert "confirm_yes" in data
    assert "confirm_no" in data


def test_remove_keyboard_markup():
    result = remove_keyboard_markup()
    assert isinstance(result, InlineKeyboardMarkup)
    assert len(result.inline_keyboard) == 0


# --- Global: callback_data within 64 bytes ---

def test_all_callback_data_within_64_bytes():
    """Ensure no callback_data exceeds Telegram's 64-byte limit."""
    all_keyboards = [
        build_intent_clarification_keyboard(_make_classification()),
        build_all_intents_keyboard(),
        critical_question_keyboard(0, 3, "default"),
        critical_question_keyboard(1, 3, "default"),
        build_vs_edit_keyboard(),
        anchor_approval_keyboard(),
        execution_control_keyboard(False),
        execution_control_keyboard(True),
        checkpoint_keyboard(),
        delivery_keyboard(has_downloadable_files=True, has_documentation=True, is_deployable=True),
        quick_fix_keyboard(),
    ]
    for kb in all_keyboards:
        for data in _extract_callback_data(kb):
            assert len(data.encode("utf-8")) <= 64, f"callback_data too long: {data!r}"
