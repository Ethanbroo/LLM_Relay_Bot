"""Instagram Reel Slideshow template.

3-7 images shown as a slideshow with transitions, per-image captions,
Ken Burns motion, and optional background music.

This is the most common Reel format for product/lifestyle accounts.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
)


class SlideshowInput(TemplateInput):
    """Inputs specific to the slideshow template."""
    images: list[str] = Field(min_length=3, max_length=7)
    captions: list[str] = Field(
        default_factory=list,
        description="One caption per image. Empty string = no text for that slide."
    )
    music_path: Optional[str] = None
    transition: TransitionType = TransitionType.DISSOLVE
    seconds_per_image: float = Field(default=3.0, ge=1.5, le=8.0)
    caption_position: TextPosition = TextPosition.BOTTOM_CENTER
    caption_font_size: int = Field(default=42, ge=16, le=100)
    use_ken_burns: bool = True


class InstagramReelSlideshow(BaseTemplate):
    name = "instagram_reel_slideshow"
    description = "3-7 image slideshow with transitions and captions"
    supported_platforms = ["instagram_reel", "tiktok", "youtube_short"]
    default_duration_seconds = 15
    min_images = 3
    max_images = 7

    def build_timeline(self, inputs: SlideshowInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        duration_ms = int(inputs.seconds_per_image * 1000)
        clips = []

        for i, img_path in enumerate(inputs.images):
            text_overlays = []
            if i < len(inputs.captions) and inputs.captions[i]:
                text_overlays.append(TextOverlay(
                    text=inputs.captions[i],
                    font_size=inputs.caption_font_size,
                    position=inputs.caption_position,
                    start_time_ms=300,
                    end_time_ms=duration_ms - 300,
                ))

            # Ken Burns: alternate zoom-in and zoom-out
            if inputs.use_ken_burns:
                if i % 2 == 0:
                    zoom_start, zoom_end = 1.0, 1.08
                else:
                    zoom_start, zoom_end = 1.08, 1.0
            else:
                zoom_start = zoom_end = 1.0

            is_first = i == 0
            is_last = i == len(inputs.images) - 1

            clips.append(Clip(
                clip_id=f"clip_{i:03d}",
                source_type="image",
                source_path=str(img_path),
                duration_ms=duration_ms,
                transition_in=TransitionType.NONE if is_first else inputs.transition,
                transition_in_duration_ms=600,
                transition_out=TransitionType.NONE if is_last else inputs.transition,
                transition_out_duration_ms=600,
                zoom_start=zoom_start,
                zoom_end=zoom_end,
                text_overlays=text_overlays,
            ))

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.3,
                fade_in_ms=500,
                fade_out_ms=1500,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or "Slideshow",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: SlideshowInput) -> list[str]:
        errors = []
        if len(inputs.images) < self.min_images:
            errors.append(f"Need at least {self.min_images} images, got {len(inputs.images)}")
        if len(inputs.images) > self.max_images:
            errors.append(f"Maximum {self.max_images} images, got {len(inputs.images)}")
        if inputs.captions and len(inputs.captions) > len(inputs.images):
            errors.append("More captions than images")
        return errors
