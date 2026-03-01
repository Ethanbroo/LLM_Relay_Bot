"""Text Story template.

Animated text on colored/gradient backgrounds. Each slide shows a line
of text that fades in, pauses, and transitions to the next. No images
required — purely text-driven.

Common for storytelling, announcements, teasers, and poetry reels.
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


class TextStoryInput(TemplateInput):
    """Inputs for the text story template."""
    lines: list[str] = Field(
        min_length=3, max_length=15,
        description="Text lines shown one per slide"
    )
    background_colors: list[str] = Field(
        default_factory=list,
        description="Hex colors for each slide background. Cycles if fewer than lines."
    )
    text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    font_size: int = Field(default=56, ge=24, le=120)
    music_path: Optional[str] = None
    seconds_per_line: float = Field(default=2.5, ge=1.5, le=6.0)
    transition: TransitionType = TransitionType.FADE


class TextStory(BaseTemplate):
    name = "text_story"
    description = "Animated text on colored backgrounds"
    supported_platforms = ["instagram_reel", "instagram_story", "tiktok"]
    default_duration_seconds = 15
    min_images = 0
    max_images = 0

    def build_timeline(self, inputs: TextStoryInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        line_ms = int(inputs.seconds_per_line * 1000)

        # Default color palette if none provided
        default_colors = [
            "#1a1a2e", "#16213e", "#0f3460", "#533483",
            "#e94560", "#2b2d42", "#264653", "#2a9d8f",
        ]
        colors = inputs.background_colors if inputs.background_colors else default_colors

        clips = []
        for i, line in enumerate(inputs.lines):
            bg_color = colors[i % len(colors)]

            # Each clip is an AI-generated solid color frame
            # We use source_type="ai_generated" with a None source_path
            # The compositor will show the background_color when no image is found
            clips.append(Clip(
                clip_id=f"clip_{i:03d}",
                source_type="ai_generated",
                ai_prompt=f"solid color background {bg_color}",
                duration_ms=line_ms,
                transition_in=TransitionType.NONE if i == 0 else inputs.transition,
                transition_in_duration_ms=400,
                transition_out=TransitionType.NONE if i == len(inputs.lines) - 1 else inputs.transition,
                transition_out_duration_ms=400,
                text_overlays=[
                    TextOverlay(
                        text=line,
                        font_size=inputs.font_size,
                        color=inputs.text_color,
                        position=TextPosition.CENTER,
                        start_time_ms=200,
                        end_time_ms=line_ms - 200,
                        fade_in_ms=500,
                        fade_out_ms=300,
                        animation=EasingFunction.EASE_OUT,
                        stroke_width=0,
                    ),
                ],
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
            title=inputs.title or "Story",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            background_color=colors[0],
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: TextStoryInput) -> list[str]:
        errors = []
        if len(inputs.lines) < 3:
            errors.append("Need at least 3 text lines")
        for i, line in enumerate(inputs.lines):
            if not line.strip():
                errors.append(f"Line {i} is empty")
        return errors
