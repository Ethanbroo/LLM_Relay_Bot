"""TikTok Before/After template.

Two-image reveal: starts with the "before" image, then wipes to reveal
the "after" image. Includes text labels and a dramatic transition.

Common for fitness transformations, room makeovers, editing showcases.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
)


class BeforeAfterInput(TemplateInput):
    """Inputs for the before/after template."""
    before_image: str = Field(description="Path to 'before' image")
    after_image: str = Field(description="Path to 'after' image")
    before_label: str = Field(default="BEFORE")
    after_label: str = Field(default="AFTER")
    music_path: Optional[str] = None
    transition: TransitionType = TransitionType.WIPE_RIGHT
    before_duration_seconds: float = Field(default=3.0, ge=1.0, le=10.0)
    after_duration_seconds: float = Field(default=4.0, ge=1.0, le=10.0)
    label_font_size: int = Field(default=60, ge=24, le=120)
    use_zoom: bool = True


class TikTokBeforeAfter(BaseTemplate):
    name = "tiktok_before_after"
    description = "Split screen before/after reveal with wipe transition"
    supported_platforms = ["tiktok", "instagram_reel", "youtube_short"]
    default_duration_seconds = 7
    min_images = 2
    max_images = 2

    def build_timeline(self, inputs: BeforeAfterInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        before_ms = int(inputs.before_duration_seconds * 1000)
        after_ms = int(inputs.after_duration_seconds * 1000)

        # "Before" clip — slight zoom out to build anticipation
        before_clip = Clip(
            clip_id="clip_before",
            source_type="image",
            source_path=str(inputs.before_image),
            duration_ms=before_ms,
            zoom_start=1.08 if inputs.use_zoom else 1.0,
            zoom_end=1.0,
            transition_out=inputs.transition,
            transition_out_duration_ms=800,
            text_overlays=[
                TextOverlay(
                    text=inputs.before_label,
                    font_size=inputs.label_font_size,
                    position=TextPosition.TOP_CENTER,
                    padding_y=80,
                    start_time_ms=200,
                    end_time_ms=before_ms - 200,
                    fade_in_ms=300,
                    fade_out_ms=300,
                ),
            ],
        )

        # "After" clip — zoom in for impact
        after_clip = Clip(
            clip_id="clip_after",
            source_type="image",
            source_path=str(inputs.after_image),
            duration_ms=after_ms,
            zoom_start=1.0,
            zoom_end=1.06 if inputs.use_zoom else 1.0,
            transition_in=inputs.transition,
            transition_in_duration_ms=800,
            text_overlays=[
                TextOverlay(
                    text=inputs.after_label,
                    font_size=inputs.label_font_size,
                    position=TextPosition.TOP_CENTER,
                    padding_y=80,
                    start_time_ms=400,
                    end_time_ms=after_ms,
                    fade_in_ms=400,
                    fade_out_ms=0,
                ),
            ],
        )

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.35,
                fade_in_ms=300,
                fade_out_ms=1000,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or "Before & After",
            resolution=resolution,
            fps=30,
            clips=[before_clip, after_clip],
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: BeforeAfterInput) -> list[str]:
        errors = []
        if not inputs.before_image:
            errors.append("before_image is required")
        if not inputs.after_image:
            errors.append("after_image is required")
        return errors
