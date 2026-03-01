"""
Tests for auth.py (@restricted decorator).

Tests the @restricted decorator with allowed users, blocked users,
missing user identity, and owner override.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set env vars BEFORE importing auth module (it reads them at import time)
_TEST_OWNER_ID = "999999"
_TEST_ALLOWED = "111111,222222"


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Patch environment variables for all tests in this module."""
    monkeypatch.setenv("TELEGRAM_BOT_OWNER_ID", _TEST_OWNER_ID)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", _TEST_ALLOWED)


def _reimport_auth():
    """Force re-import of auth module to pick up patched env vars."""
    import importlib
    import telegram_bot.auth as auth_module
    importlib.reload(auth_module)
    return auth_module


def _make_update(user_id: int | None, username: str = "testuser") -> MagicMock:
    """Create a mock Update object with a user."""
    update = MagicMock()
    if user_id is not None:
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_user.username = username
        update.effective_user.full_name = f"Test User {user_id}"
    else:
        update.effective_user = None
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    update.update_id = 12345
    return update


@pytest.mark.asyncio
async def test_allowed_user_passes_through():
    auth = _reimport_auth()
    handler = AsyncMock(return_value=42)
    wrapped = auth.restricted(handler)

    update = _make_update(111111)
    context = MagicMock()

    result = await wrapped(update, context)

    handler.assert_awaited_once_with(update, context)
    assert result == 42


@pytest.mark.asyncio
async def test_owner_always_passes():
    auth = _reimport_auth()
    handler = AsyncMock(return_value=10)
    wrapped = auth.restricted(handler)

    update = _make_update(999999)
    context = MagicMock()

    result = await wrapped(update, context)

    handler.assert_awaited_once()
    assert result == 10


@pytest.mark.asyncio
async def test_unauthorized_user_rejected():
    auth = _reimport_auth()
    handler = AsyncMock()
    wrapped = auth.restricted(handler)

    update = _make_update(555555)
    context = MagicMock()

    result = await wrapped(update, context)

    handler.assert_not_awaited()
    update.effective_message.reply_text.assert_awaited_once()
    call_args = update.effective_message.reply_text.call_args[0][0]
    assert "\u26d4" in call_args
    assert result is None


@pytest.mark.asyncio
async def test_no_user_identity_returns_none():
    auth = _reimport_auth()
    handler = AsyncMock()
    wrapped = auth.restricted(handler)

    update = _make_update(None)
    context = MagicMock()

    result = await wrapped(update, context)

    handler.assert_not_awaited()
    assert result is None


@pytest.mark.asyncio
async def test_second_allowed_user_passes():
    auth = _reimport_auth()
    handler = AsyncMock(return_value=7)
    wrapped = auth.restricted(handler)

    update = _make_update(222222)
    context = MagicMock()

    result = await wrapped(update, context)

    handler.assert_awaited_once()
    assert result == 7


@pytest.mark.asyncio
async def test_decorator_preserves_function_name():
    auth = _reimport_auth()

    async def my_handler(update, context):
        pass

    wrapped = auth.restricted(my_handler)
    assert wrapped.__name__ == "my_handler"
