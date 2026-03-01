"""Photo Montage template.

Photos displayed with dynamic transitions — starts with quick cuts
and slows down for featured shots. Creates a collage-to-fullscreen
feel. Great for event recaps and photo dumps.

Common for travel, events, weddings, and "photo dump" reels.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
)


class PhotoMontageInput(TemplateInput):
    """Inputs for the photo montage template."""
    images: list[str] = Field(min_length=5, max_length=20)
    title_text: Optional[str] = Field(default=None, description="Title shown at start")
    end_text: Optional[str] = Field(default=None, description="Text shown on final frame")
    music_path: Optional[str] = None
    fast_cut_count: int = Field(
        default=0, ge=0,
        description="Number of initial images shown with fast cuts (0 = auto)"
    )
    use_film_grain: bool = False


class PhotoMontage(BaseTemplate):
    name = "photo_montage"
    description = "Dynamic photo montage with speed variation"
    supported_platforms = ["instagram_reel", "tiktok", "youtube_short"]
    default_duration_seconds = 20
    min_images = 5
    max_images = 20

    def build_timeline(self, inputs: PhotoMontageInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        num_images = len(inputs.images)

        # Auto-determine fast cut count: ~40% of images
        fast_count = inputs.fast_cut_count or max(2, num_images * 2 // 5)
        fast_count = min(fast_count, num_images - 2)

        # Timing: fast cuts = 800ms, normal = 2500ms, featured (last 2) = 3500ms
        fast_ms = 800
        normal_ms = 2500
        featured_ms = 3500

        # Transition types to cycle through
        transitions = [
            TransitionType.DISSOLVE,
            TransitionType.WIPE_LEFT,
            TransitionType.ZOOM_IN,
            TransitionType.SLIDE_RIGHT,
            TransitionType.FADE,
            TransitionType.WIPE_UP,
        ]

        clips = []
        for i, img_path in enumerate(inputs.images):
            # Determine duration based on position
            if i < fast_count:
                duration_ms = fast_ms
            elif i >= num_images - 2:
                duration_ms = featured_ms
            else:
                duration_ms = normal_ms

            text_overlays = []

            # Title on first image
            if i == 0 and inputs.title_text:
                text_overlays.append(TextOverlay(
                    text=inputs.title_text,
                    font_size=52,
                    position=TextPosition.CENTER,
                    start_time_ms=100,
                    end_time_ms=duration_ms - 100,
                    fade_in_ms=200,
                    fade_out_ms=200,
                ))

            # End text on last image
            if i == num_images - 1 and inputs.end_text:
                text_overlays.append(TextOverlay(
                    text=inputs.end_text,
                    font_size=44,
                    position=TextPosition.BOTTOM_CENTER,
                    start_time_ms=500,
                    end_time_ms=duration_ms - 200,
                    fade_in_ms=400,
                    fade_out_ms=0,
                ))

            # Ken Burns varies with speed — fast = more zoom, slow = subtle
            if i < fast_count:
                zoom_start, zoom_end = 1.0, 1.15
            else:
                zoom_start, zoom_end = 1.0, 1.05

            # Effects for cinematic feel
            effects = []
            if inputs.use_film_grain:
                effects.append(("film_grain", {"intensity": 0.12}))

            is_first = i == 0
            is_last = i == num_images - 1
            t = transitions[i % len(transitions)]

            clips.append(Clip(
                clip_id=f"clip_{i:03d}",
                source_type="image",
                source_path=str(img_path),
                duration_ms=duration_ms,
                transition_in=TransitionType.NONE if is_first else t,
                transition_in_duration_ms=300 if i < fast_count else 500,
                transition_out=TransitionType.NONE if is_last else transitions[(i + 1) % len(transitions)],
                transition_out_duration_ms=300 if i < fast_count else 500,
                zoom_start=zoom_start,
                zoom_end=zoom_end,
                text_overlays=text_overlays,
                effects=effects,
            ))

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.35,
                fade_in_ms=300,
                fade_out_ms=2000,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or "Photo Montage",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: PhotoMontageInput) -> list[str]:
        errors = []
        if len(inputs.images) < self.min_images:
            errors.append(f"Need at least {self.min_images} images for a montage")
        return errors
