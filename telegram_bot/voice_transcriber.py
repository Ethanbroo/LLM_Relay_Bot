"""
Whisper integration for voice message transcription.

Voice messages follow a three-step pipeline:
  1. Download OGG from Telegram
  2. Transcribe with OpenAI Whisper
  3. Re-enter the text pipeline through the classifier

The Whisper API accepts OGG/Opus files directly — no format conversion
needed. A typical 10-second Telegram voice message costs approximately
$0.001 to transcribe ($0.006 per minute of audio).

Even at 50 voice messages per day (unlikely), the monthly cost would
be approximately $1.50, which is negligible.
"""

from __future__ import annotations

import logging
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Lazy-initialized client — avoids crashing at import time when
# OPENAI_API_KEY is not set (voice is optional).
_openai_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Get or create the OpenAI client. Deferred to first use."""
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI()
    return _openai_client


async def transcribe_voice(voice_file_path: Path) -> str:
    """Transcribe an OGG voice file using OpenAI Whisper.

    The Whisper API accepts OGG/Opus files directly — no format conversion
    needed. A typical 10-second Telegram voice message costs approximately
    $0.001 to transcribe.

    Args:
        voice_file_path: Path to the downloaded .ogg file on disk.

    Returns:
        The transcribed text as a string.

    Raises:
        openai.APIError: If the Whisper API call fails.
    """
    client = _get_client()
    with open(voice_file_path, "rb") as audio_file:
        transcription = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text",
            # language="en",  # Uncomment to force English (improves accuracy)
        )

    logger.info(
        "Transcribed voice message: %d bytes \u2192 %d chars",
        voice_file_path.stat().st_size,
        len(transcription),
    )
    return transcription.strip()
