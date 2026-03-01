"""Kling O1 Edit wrapper for video-to-video transformation (DEPRECATED).

This module is deprecated. Use gameplay.pip_compositor.PiPCompositor instead.
Kling O1 Edit produces poor results for gameplay overlays and costs $0.168/s.
PiPCompositor uses FFmpeg for $0 cost with better control.
"""

import logging
import subprocess
import tempfile
import warnings
from typing import Optional

from ..brief.models import VideoIntent, ContentFormat
from ..character.models import CharacterProfile
from ..generation.video_generator import VideoGenerator, VideoGenerationResult

logger = logging.getLogger(__name__)


class OverlayCompositor:
    """Wraps Kling O1 Edit for gameplay overlay content.

    DEPRECATED: Use gameplay.pip_compositor.PiPCompositor instead.
    """

    def __init__(
        self,
        character: CharacterProfile,
        ffmpeg_path: str = "ffmpeg",
    ):
        self.character = character
        self.ffmpeg = ffmpeg_path
        self.video_generator = VideoGenerator(character=character)

    async def compose_overlay(
        self,
        gameplay_clip_path: str,
        transformation_prompt: str,
        character_image_url: str,
        target_aspect: str = "9:16",
        duration: int = 15,
    ) -> VideoGenerationResult:
        """Generate AI character overlay on gameplay footage.

        DEPRECATED: Use PiPCompositor.overlay() instead.
        """
        warnings.warn(
            "OverlayCompositor.compose_overlay() is deprecated. "
            "Use gameplay.pip_compositor.PiPCompositor.overlay() instead. "
            "Kling O1 Edit produces poor results for gameplay overlays.",
            DeprecationWarning,
            stacklevel=2,
        )
        from .pip_compositor import PiPCompositor
        compositor = PiPCompositor(ffmpeg_path=self.ffmpeg)

        # Generate a reaction clip via the cheapest provider, then PiP overlay
        from ..generation.video_generator import VideoGenerator
        generator = VideoGenerator(character=self.character)

        reaction_intent = VideoIntent(
            content_format=ContentFormat.CINEMATIC_CLIP,
            prompt=transformation_prompt,
            character_refs=[character_image_url],
            shot_list=[],
            voice_ids=[],
            hashtag_set=set(),
            duration=duration,
            aspect_ratio="9:16",
            native_audio=False,
            character_id=self.character.character_id,
        )
        reaction_clip = await generator.generate(reaction_intent)

        output_path = tempfile.mktemp(suffix=".mp4", prefix="deprecated_overlay_")
        compositor.overlay(
            background_video=gameplay_clip_path,
            pip_video=reaction_clip.video_path,
            output_path=output_path,
            pip_position="bottom_right",
            pip_scale=0.30,
            target_aspect=target_aspect,
        )

        return VideoGenerationResult(
            video_path=output_path,
            generation_time_s=0.0,
            cost_usd=reaction_clip.cost_usd,
            provider="ffmpeg+deprecated",
            endpoint="deprecated_overlay",
            duration_seconds=float(duration),
            has_audio=True,
        )

    def _adapt_aspect_ratio(
        self, input_path: str, target_aspect: str
    ) -> str:
        """Convert landscape video to portrait with letterboxing/cropping."""
        from ..quality.frame_extractor import FrameExtractor

        extractor = FrameExtractor()
        info = extractor.get_video_info(input_path)

        width = info.get("width", 1920)
        height = info.get("height", 1080)

        if target_aspect == "9:16":
            target_w, target_h = 1080, 1920
        elif target_aspect == "1:1":
            target_w, target_h = 1080, 1080
        elif target_aspect == "16:9":
            return input_path
        else:
            return input_path

        current_ratio = width / height
        target_ratio = target_w / target_h

        if abs(current_ratio - target_ratio) < 0.05:
            return input_path

        output_path = tempfile.mktemp(suffix=".mp4", prefix="adapted_")

        if current_ratio > target_ratio:
            new_width = int(height * target_ratio)
            crop_filter = f"crop={new_width}:{height}"
        else:
            new_height = int(width / target_ratio)
            crop_filter = f"crop={width}:{new_height}"

        scale_filter = f"scale={target_w}:{target_h}"

        cmd = [
            self.ffmpeg,
            "-i", input_path,
            "-vf", f"{crop_filter},{scale_filter}",
            "-c:a", "copy",
            output_path,
            "-y",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(
                "Aspect ratio adaptation failed: %s. Using original.",
                result.stderr,
            )
            return input_path

        logger.info(
            "Adapted %dx%d → %dx%d: %s",
            width, height, target_w, target_h, output_path,
        )
        return output_path
