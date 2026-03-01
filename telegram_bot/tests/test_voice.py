"""
Tests for voice_transcriber.py (Whisper integration).

Tests the transcription function with mocked OpenAI client responses.
Does not make real API calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_transcribe_voice_returns_text(tmp_path):
    voice_file = tmp_path / "test.ogg"
    voice_file.write_bytes(b"\x00" * 100)

    mock_response = "Build me a landing page for Cardinal Sales"

    with patch("telegram_bot.voice_transcriber.openai_client") as mock_client:
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)

        from telegram_bot.voice_transcriber import transcribe_voice
        result = await transcribe_voice(voice_file)

    assert result == "Build me a landing page for Cardinal Sales"


@pytest.mark.asyncio
async def test_transcribe_voice_strips_whitespace(tmp_path):
    voice_file = tmp_path / "test.ogg"
    voice_file.write_bytes(b"\x00" * 100)

    mock_response = "  some text with spaces  \n"

    with patch("telegram_bot.voice_transcriber.openai_client") as mock_client:
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)

        from telegram_bot.voice_transcriber import transcribe_voice
        result = await transcribe_voice(voice_file)

    assert result == "some text with spaces"


@pytest.mark.asyncio
async def test_transcribe_voice_empty_result(tmp_path):
    voice_file = tmp_path / "test.ogg"
    voice_file.write_bytes(b"\x00" * 100)

    mock_response = "   "

    with patch("telegram_bot.voice_transcriber.openai_client") as mock_client:
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)

        from telegram_bot.voice_transcriber import transcribe_voice
        result = await transcribe_voice(voice_file)

    assert result == ""


@pytest.mark.asyncio
async def test_transcribe_voice_uses_whisper_model(tmp_path):
    voice_file = tmp_path / "test.ogg"
    voice_file.write_bytes(b"\x00" * 100)

    with patch("telegram_bot.voice_transcriber.openai_client") as mock_client:
        mock_client.audio.transcriptions.create = AsyncMock(return_value="text")

        from telegram_bot.voice_transcriber import transcribe_voice
        await transcribe_voice(voice_file)

    call_kwargs = mock_client.audio.transcriptions.create.call_args
    assert call_kwargs.kwargs["model"] == "whisper-1"
    assert call_kwargs.kwargs["response_format"] == "text"
