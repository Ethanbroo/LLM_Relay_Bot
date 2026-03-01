"""Instagram Reel Quote template.

A background image with an animated quote overlaid. The quote fades in
with optional author attribution. Includes subtle Ken Burns and a
cinematic vignette effect.

Common for motivational, branding, and thought-leadership content.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
    EasingFunction,
)


class QuoteInput(TemplateInput):
    """Inputs for the quote reel template."""
    background_image: str = Field(description="Path to background image")
    quote_text: str = Field(min_length=1, max_length=300)
    author: Optional[str] = Field(default=None, max_length=100)
    music_path: Optional[str] = None
    quote_font_size: int = Field(default=56, ge=24, le=120)
    author_font_size: int = Field(default=32, ge=16, le=80)
    quote_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    use_vignette: bool = True
    use_film_grain: bool = False


class InstagramReelQuote(BaseTemplate):
    name = "instagram_reel_quote"
    description = "Background image with animated quote text overlay"
    supported_platforms = ["instagram_reel", "instagram_story", "tiktok"]
    default_duration_seconds = 8
    min_images = 1
    max_images = 1

    def build_timeline(self, inputs: QuoteInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        duration_s = inputs.duration_seconds or self.default_duration_seconds
        duration_ms = duration_s * 1000

        # Quote appears after 1 second, fades in over 800ms
        quote_start = 1000
        quote_end = duration_ms - 500

        text_overlays = [
            TextOverlay(
                text=inputs.quote_text,
                font_size=inputs.quote_font_size,
                color=inputs.quote_color,
                position=TextPosition.CENTER,
                start_time_ms=quote_start,
                end_time_ms=quote_end,
                fade_in_ms=800,
                fade_out_ms=600,
                animation=EasingFunction.EASE_OUT,
                stroke_width=3,
            ),
        ]

        if inputs.author:
            text_overlays.append(TextOverlay(
                text=f"— {inputs.author}",
                font_size=inputs.author_font_size,
                color=inputs.quote_color,
                position=TextPosition.BOTTOM_CENTER,
                padding_y=200,
                start_time_ms=quote_start + 600,
                end_time_ms=quote_end,
                fade_in_ms=600,
                fade_out_ms=600,
                animation=EasingFunction.EASE_OUT,
                stroke_width=2,
            ))

        # Build effect chain
        effects = []
        if inputs.use_vignette:
            effects.append(("vignette", {"strength": 0.5, "radius": 0.7}))
        if inputs.use_film_grain:
            effects.append(("film_grain", {"intensity": 0.15, "monochrome": True}))

        clips = [
            Clip(
                clip_id="clip_000",
                source_type="image",
                source_path=str(inputs.background_image),
                duration_ms=duration_ms,
                zoom_start=1.0,
                zoom_end=1.05,
                text_overlays=text_overlays,
                effects=effects,
            ),
        ]

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.2,
                fade_in_ms=500,
                fade_out_ms=1500,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or "Quote",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: QuoteInput) -> list[str]:
        errors = []
        if not inputs.quote_text.strip():
            errors.append("Quote text cannot be empty")
        return errors
