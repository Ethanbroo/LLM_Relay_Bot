# audio/tts_provider.py

import os
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class AudioStrategy(str, Enum):
    """How audio is sourced for a given video post."""

    KLING_NATIVE = "kling_native"
    # Let Kling generate audio as part of video generation.
    # Prompt includes dialogue/sound direction inline.
    # No separate audio file needed.

    ELEVENLABS_TTS = "elevenlabs_tts"
    # Generate audio separately via ElevenLabs, pass URL to Kling Avatar.
    # Used when brand voice consistency is critical.

    EXTERNAL_AUDIO = "external_audio"
    # User-supplied audio file (music track, gameplay audio).
    # Used for gameplay_overlay and music-driven content.

    SILENT = "silent"
    # No audio. For static images or intentionally silent clips.


class TTSConfig(BaseModel):
    """ElevenLabs configuration. API key stored in macOS Keychain."""

    voice_id: str = ""            # Cloned character voice ID
    model_id: str = "eleven_multilingual_v2"
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    output_format: str = "mp3_44100_128"


class AudioRouter:
    """Selects audio strategy based on content format."""

    STRATEGY_MAP = {
        "static_image":      AudioStrategy.SILENT,
        "avatar_talking":    AudioStrategy.ELEVENLABS_TTS,
        "narrative_reel":    AudioStrategy.KLING_NATIVE,
        "cinematic_clip":    AudioStrategy.KLING_NATIVE,
        "gameplay_overlay":  AudioStrategy.EXTERNAL_AUDIO,
    }

    def select_strategy(self, content_format: str) -> AudioStrategy:
        return self.STRATEGY_MAP.get(content_format, AudioStrategy.KLING_NATIVE)


class ElevenLabsClient:
    """Generates TTS audio via ElevenLabs API."""

    BASE_URL = "https://api.elevenlabs.io/v1"

    def __init__(self, config: TTSConfig):
        self.config = config
        self.api_key = self._get_api_key()

    def _get_api_key(self) -> str:
        # Check environment variable first, then macOS Keychain
        key = os.environ.get("ELEVENLABS_API_KEY", "")
        if key:
            return key
        import subprocess
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "elevenlabs-api-key", "-w"],
            capture_output=True, text=True
        )
        return result.stdout.strip()

    async def generate_speech(self, text: str, output_path: str) -> str:
        """Generate speech audio file. Returns local file path."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/text-to-speech/{self.config.voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                params={
                    "output_format": self.config.output_format,
                },
                json={
                    "text": text,
                    "model_id": self.config.model_id,
                    "voice_settings": {
                        "stability": self.config.stability,
                        "similarity_boost": self.config.similarity_boost,
                        "style": self.config.style,
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(response.content)

            return output_path

    async def generate_and_upload(self, text: str) -> str:
        """Generate speech and upload to fal.ai storage for use as audio_url.
        Returns publicly accessible URL."""
        import tempfile
        import fal_client

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            local_path = await self.generate_speech(text, tmp.name)
            # Upload to fal.ai storage so Kling Avatar can access it
            url = fal_client.upload_file(local_path)
            os.unlink(local_path)
            return url
