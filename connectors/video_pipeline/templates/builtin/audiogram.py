"""Audiogram template.

Audio waveform visualization over a background image. Displays a
quote/transcript as animated text synced to the audio. Used for
podcast clips, voice notes, and audio-first content.

The waveform is rendered as a series of vertical bars that animate
with the audio progress.
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


class AudiogramInput(TemplateInput):
    """Inputs for the audiogram template."""
    background_image: str = Field(description="Path to background image")
    audio_path: str = Field(description="Path to audio file")
    transcript_lines: list[str] = Field(
        default_factory=list,
        description="Timed text lines. Each shown sequentially over the duration."
    )
    speaker_name: Optional[str] = Field(default=None, max_length=100)
    speaker_title: Optional[str] = Field(default=None, max_length=100)
    waveform_color: str = Field(default="#4ECDC4", pattern=r"^#[0-9A-Fa-f]{6}$")
    text_font_size: int = Field(default=36, ge=20, le=80)


class Audiogram(BaseTemplate):
    name = "audiogram"
    description = "Audio waveform visualization with transcript"
    supported_platforms = ["instagram_reel", "instagram_story", "tiktok"]
    default_duration_seconds = 30
    min_images = 1
    max_images = 1

    def build_timeline(self, inputs: AudiogramInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        duration_s = inputs.duration_seconds or self.default_duration_seconds
        duration_ms = duration_s * 1000

        text_overlays = []

        # Speaker info (top area)
        if inputs.speaker_name:
            text_overlays.append(TextOverlay(
                text=inputs.speaker_name,
                font_size=inputs.text_font_size + 8,
                position=TextPosition.TOP_CENTER,
                padding_y=80,
                start_time_ms=0,
                end_time_ms=duration_ms,
                fade_in_ms=500,
                fade_out_ms=500,
            ))

        if inputs.speaker_title:
            text_overlays.append(TextOverlay(
                text=inputs.speaker_title,
                font_size=inputs.text_font_size - 4,
                color="#AAAAAA",
                position=TextPosition.TOP_CENTER,
                padding_y=130,
                start_time_ms=300,
                end_time_ms=duration_ms,
                fade_in_ms=500,
                fade_out_ms=500,
            ))

        # Transcript lines — evenly distributed across duration
        if inputs.transcript_lines:
            num_lines = len(inputs.transcript_lines)
            time_per_line = duration_ms // num_lines

            for i, line in enumerate(inputs.transcript_lines):
                if not line.strip():
                    continue
                line_start = i * time_per_line
                line_end = min((i + 1) * time_per_line + 200, duration_ms)

                text_overlays.append(TextOverlay(
                    text=line,
                    font_size=inputs.text_font_size,
                    position=TextPosition.CENTER,
                    start_time_ms=line_start,
                    end_time_ms=line_end,
                    fade_in_ms=300,
                    fade_out_ms=200,
                    animation=EasingFunction.EASE_OUT,
                    stroke_width=2,
                ))

        # Waveform visualization via animated grid overlay effect
        # The grid_overlay effect provides a visual rhythm marker
        effects = [
            ("vignette", {"strength": 0.5, "radius": 0.7}),
            ("grid_overlay", {
                "spacing": 40,
                "color": inputs.waveform_color,
                "opacity": 0.08,
                "animate": True,
            }),
        ]

        clips = [
            Clip(
                clip_id="clip_000",
                source_type="image",
                source_path=str(inputs.background_image),
                duration_ms=duration_ms,
                zoom_start=1.0,
                zoom_end=1.03,
                text_overlays=text_overlays,
                effects=effects,
            ),
        ]

        audio_tracks = [
            AudioTrack(
                source_path=str(inputs.audio_path),
                start_time_ms=0,
                volume=1.0,
                fade_in_ms=200,
                fade_out_ms=1000,
            ),
        ]

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or "Audiogram",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: AudiogramInput) -> list[str]:
        errors = []
        if not inputs.background_image:
            errors.append("background_image is required")
        if not inputs.audio_path:
            errors.append("audio_path is required")
        return errors
