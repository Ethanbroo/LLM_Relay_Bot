"""Day in the Life template.

Time-stamped scenes showing a day's activities. Each image gets a
timestamp label and optional caption. Casual, authentic vibe with
warm color grading.

Common for personal branding, lifestyle, and "a day in my life" content.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
)


class DayInLifeInput(TemplateInput):
    """Inputs for the day-in-life template."""
    images: list[str] = Field(min_length=4, max_length=12)
    timestamps: list[str] = Field(
        default_factory=list,
        description="Time labels for each scene, e.g. ['6:00 AM', '8:30 AM', ...]"
    )
    captions: list[str] = Field(
        default_factory=list,
        description="Activity description for each scene"
    )
    music_path: Optional[str] = None
    seconds_per_scene: float = Field(default=2.5, ge=1.5, le=6.0)
    use_warm_tone: bool = True


class DayInLife(BaseTemplate):
    name = "day_in_life"
    description = "Time-stamped scenes with casual lifestyle vibe"
    supported_platforms = ["instagram_reel", "tiktok", "youtube_short"]
    default_duration_seconds = 20
    min_images = 4
    max_images = 12

    def build_timeline(self, inputs: DayInLifeInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        scene_ms = int(inputs.seconds_per_scene * 1000)
        clips = []

        for i, img_path in enumerate(inputs.images):
            text_overlays = []

            # Timestamp in top-left
            if i < len(inputs.timestamps) and inputs.timestamps[i]:
                text_overlays.append(TextOverlay(
                    text=inputs.timestamps[i],
                    font_size=52,
                    position=TextPosition.TOP_LEFT,
                    padding_x=30,
                    padding_y=60,
                    start_time_ms=100,
                    end_time_ms=scene_ms - 100,
                    fade_in_ms=200,
                    fade_out_ms=200,
                    stroke_width=3,
                ))

            # Caption in bottom-center
            if i < len(inputs.captions) and inputs.captions[i]:
                text_overlays.append(TextOverlay(
                    text=inputs.captions[i],
                    font_size=36,
                    position=TextPosition.BOTTOM_CENTER,
                    padding_y=60,
                    start_time_ms=300,
                    end_time_ms=scene_ms - 200,
                    fade_in_ms=300,
                    fade_out_ms=200,
                ))

            # Gentle Ken Burns with varied directions
            pan_directions = [
                (0.0, 0.0),    # center
                (-0.2, 0.0),   # slight left
                (0.2, 0.0),    # slight right
                (0.0, -0.1),   # slight up
            ]
            px, py = pan_directions[i % len(pan_directions)]

            # Warm tone effect for lifestyle feel
            effects = []
            if inputs.use_warm_tone:
                effects.append(("warm_tone", {"intensity": 0.1}))

            is_first = i == 0
            is_last = i == len(inputs.images) - 1

            clips.append(Clip(
                clip_id=f"clip_{i:03d}",
                source_type="image",
                source_path=str(img_path),
                duration_ms=scene_ms,
                transition_in=TransitionType.NONE if is_first else TransitionType.FADE,
                transition_in_duration_ms=400,
                transition_out=TransitionType.NONE if is_last else TransitionType.FADE,
                transition_out_duration_ms=400,
                zoom_start=1.0,
                zoom_end=1.06,
                pan_x_start=px,
                pan_x_end=-px,
                pan_y_start=py,
                pan_y_end=-py,
                text_overlays=text_overlays,
                effects=effects,
            ))

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.3,
                fade_in_ms=500,
                fade_out_ms=2000,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or "A Day in My Life",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: DayInLifeInput) -> list[str]:
        errors = []
        if len(inputs.images) < self.min_images:
            errors.append(f"Need at least {self.min_images} scene images")
        if inputs.timestamps and len(inputs.timestamps) > len(inputs.images):
            errors.append("More timestamps than images")
        if inputs.captions and len(inputs.captions) > len(inputs.images):
            errors.append("More captions than images")
        return errors
