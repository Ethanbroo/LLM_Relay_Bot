"""Avatar-specific video generation logic.

Handles the AVATAR_TALKING format:
1. Generate TTS audio via ElevenLabs (branded voice)
2. Upload character reference + audio to fal.ai storage
3. Call Kling Avatar v2 Pro for lip-synced video
4. Normalize audio to Instagram standard (-14 LUFS)
"""

import logging
from typing import Optional

from ..brief.models import VideoIntent, ContentFormat
from ..audio.tts_provider import ElevenLabsClient, TTSConfig
from ..audio.audio_mixer import AudioMixer
from ..character.models import CharacterProfile
from .video_generator import VideoGenerator, VideoGenerationResult

logger = logging.getLogger(__name__)


class AvatarGenerator:
    """Generates talking-head avatar videos.

    Flow:
    1. ElevenLabs TTS → audio file
    2. Upload audio + character image to fal.ai
    3. Kling Avatar v2 Pro → lip-synced video
    4. Audio normalization
    """

    def __init__(
        self,
        character: CharacterProfile,
        tts_config: Optional[TTSConfig] = None,
    ):
        self.character = character
        self.video_generator = VideoGenerator(character=character)
        self.audio_mixer = AudioMixer()

        # Set up TTS with character's voice
        if tts_config is None:
            tts_config = TTSConfig(voice_id=character.voice_id or "")
        self.tts_client = ElevenLabsClient(config=tts_config)

    async def generate_avatar_video(
        self,
        dialogue_text: str,
        character_image_url: str,
        duration: int = 10,
        aspect_ratio: str = "9:16",
    ) -> VideoGenerationResult:
        """Generate a talking-head avatar video.

        Args:
            dialogue_text: What the character says (fed to TTS)
            character_image_url: URL of the character reference image
            duration: Target video duration in seconds
            aspect_ratio: Output aspect ratio

        Returns:
            VideoGenerationResult with video path
        """
        logger.info(
            "Generating avatar video: %d chars of dialogue, %ds duration",
            len(dialogue_text), duration,
        )

        # Step 1: Generate TTS audio and upload to fal.ai
        audio_url = await self.tts_client.generate_and_upload(dialogue_text)
        logger.info("TTS audio uploaded: %s", audio_url)

        # Step 2: Build intent and generate via Kling Avatar
        intent = VideoIntent(
            content_format=ContentFormat.AVATAR_TALKING,
            prompt=dialogue_text,
            character_refs=[character_image_url],
            audio_url=audio_url,
            duration=duration,
            aspect_ratio=aspect_ratio,
            native_audio=False,  # Using ElevenLabs, not Kling native
            character_id=self.character.character_id,
        )

        result = await self.video_generator.generate_with_budget(intent)

        # Step 3: Normalize audio if we got a video back
        if result.video_path and not result.is_static_fallback:
            normalized_path = result.video_path.replace(".mp4", "_normalized.mp4")
            try:
                self.audio_mixer.normalize_audio(
                    result.video_path,
                    normalized_path,
                    target_lufs=-14,
                )
                # Replace original with normalized
                import os
                os.replace(normalized_path, result.video_path)
                logger.info("Audio normalized to -14 LUFS")
            except Exception as e:
                logger.warning("Audio normalization failed: %s", e)

        return result

    async def generate_from_intent(
        self, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Generate avatar video from a pre-built VideoIntent.

        If the intent doesn't have an audio_url, generates TTS first.
        """
        if not intent.audio_url and intent.prompt:
            # Generate TTS from the prompt/caption
            intent.audio_url = await self.tts_client.generate_and_upload(
                intent.prompt
            )

        return await self.video_generator.generate_with_budget(intent)
