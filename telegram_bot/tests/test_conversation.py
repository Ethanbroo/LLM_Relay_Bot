"""
Tests for conversation.py (ConversationHandler).

Tests that the ConversationHandler is correctly configured with all
expected states, entry points, fallbacks, and settings.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
)

from telegram_bot.states import BotState


def _get_handler():
    """Import and build the conversation handler."""
    from telegram_bot.conversation import create_conversation_handler
    return create_conversation_handler()


def test_handler_is_conversation_handler():
    handler = _get_handler()
    assert isinstance(handler, ConversationHandler)


def test_all_states_present():
    handler = _get_handler()
    expected_states = {
        BotState.AWAITING_INTENT_CLARIFICATION,
        BotState.AWAITING_BUILD_VS_EDIT,
        BotState.AWAITING_PROJECT_SELECTION,
        BotState.AWAITING_CRITICAL_QUESTIONS,
        BotState.AWAITING_ANCHOR_APPROVAL,
        BotState.EXECUTING,
        BotState.AWAITING_CHECKPOINT_APPROVAL,
        BotState.AWAITING_HUMAN_DECISION,
        BotState.AWAITING_DELIVERY_ACTION,
        BotState.AWAITING_QUICK_FIX_CONFIRM,
        ConversationHandler.TIMEOUT,
    }
    actual_states = set(handler.states.keys())
    assert expected_states == actual_states


def test_entry_points_count():
    handler = _get_handler()
    # /start, /help, /cost, /status, /sessions, /cancel, text, voice, files
    assert len(handler.entry_points) == 9


def test_fallbacks_count():
    handler = _get_handler()
    # /cancel, /start, /help, /cost
    assert len(handler.fallbacks) == 4


def test_configuration_settings():
    handler = _get_handler()
    assert handler.allow_reentry is True
    assert handler.per_chat is True
    assert handler.per_user is True
    assert handler.per_message is False
    assert handler.conversation_timeout == 1800
    assert handler.name == "main_conversation"
    assert handler.persistent is False


def test_bot_state_values():
    """Verify gap-numbered state values."""
    assert BotState.IDLE == 0
    assert BotState.AWAITING_INTENT_CLARIFICATION == 10
    assert BotState.AWAITING_BUILD_VS_EDIT == 11
    assert BotState.AWAITING_PROJECT_SELECTION == 12
    assert BotState.AWAITING_CRITICAL_QUESTIONS == 20
    assert BotState.AWAITING_ANCHOR_APPROVAL == 21
    assert BotState.EXECUTING == 30
    assert BotState.AWAITING_CHECKPOINT_APPROVAL == 31
    assert BotState.AWAITING_HUMAN_DECISION == 32
    assert BotState.AWAITING_DELIVERY_ACTION == 40
    assert BotState.AWAITING_QUICK_FIX_CONFIRM == 41


def test_bot_state_count():
    assert len(BotState) == 11


def test_timeout_state_has_handlers():
    handler = _get_handler()
    timeout_handlers = handler.states[ConversationHandler.TIMEOUT]
    assert len(timeout_handlers) == 2  # MessageHandler + CallbackQueryHandler
