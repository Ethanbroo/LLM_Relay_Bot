"""Countdown template.

"Top N" style countdown. Items are shown in reverse order with number
badges, building to #1. Each item gets a dramatic zoom-in reveal.

Common for "Top 5", rankings, recommendations, and listicle content.
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


class CountdownInput(TemplateInput):
    """Inputs for the countdown template."""
    items: list[dict] = Field(
        min_length=3, max_length=10,
        description="List of dicts with 'image', 'title', optional 'subtitle'"
    )
    countdown_title: Optional[str] = Field(
        default=None,
        description="Title shown at start, e.g. 'Top 5 Coffee Shops'"
    )
    music_path: Optional[str] = None
    seconds_per_item: float = Field(default=3.0, ge=2.0, le=8.0)
    number_font_size: int = Field(default=80, ge=40, le=150)
    title_font_size: int = Field(default=40, ge=20, le=80)
    number_color: str = Field(default="#FFD700", pattern=r"^#[0-9A-Fa-f]{6}$")
    reverse_order: bool = Field(
        default=True,
        description="True = show items N to 1 (standard countdown)"
    )


class Countdown(BaseTemplate):
    name = "countdown"
    description = "Top N style countdown with number badges"
    supported_platforms = ["instagram_reel", "tiktok", "youtube_short"]
    default_duration_seconds = 20
    min_images = 3
    max_images = 10

    def build_timeline(self, inputs: CountdownInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        item_ms = int(inputs.seconds_per_item * 1000)
        num_items = len(inputs.items)

        # Order: reverse for countdown (N → 1), or sequential (1 → N)
        ordered_items = list(reversed(inputs.items)) if inputs.reverse_order else inputs.items
        # Number labels: countdown = N, N-1, ..., 1 ; sequential = 1, 2, ..., N
        if inputs.reverse_order:
            numbers = list(range(num_items, 0, -1))
        else:
            numbers = list(range(1, num_items + 1))

        clips = []
        clip_idx = 0

        # Optional intro slide
        if inputs.countdown_title and ordered_items:
            first_img = ordered_items[0].get("image", "")
            clips.append(Clip(
                clip_id=f"clip_{clip_idx:03d}",
                source_type="image",
                source_path=str(first_img),
                duration_ms=2500,
                zoom_start=1.2,
                zoom_end=1.1,
                transition_out=TransitionType.ZOOM_IN,
                transition_out_duration_ms=600,
                text_overlays=[
                    TextOverlay(
                        text=inputs.countdown_title,
                        font_size=inputs.title_font_size + 8,
                        position=TextPosition.CENTER,
                        start_time_ms=200,
                        end_time_ms=2300,
                        fade_in_ms=400,
                        fade_out_ms=300,
                        animation=EasingFunction.EASE_OUT,
                    ),
                ],
                effects=[
                    ("gaussian_blur", {"radius": 8}),
                    ("vignette", {"strength": 0.6}),
                ],
            ))
            clip_idx += 1

        for i, item in enumerate(ordered_items):
            img_path = item.get("image", "")
            item_title = item.get("title", "")
            item_subtitle = item.get("subtitle", "")
            number = numbers[i]

            text_overlays = []

            # Number badge (top-left, large)
            text_overlays.append(TextOverlay(
                text=f"#{number}",
                font_size=inputs.number_font_size,
                color=inputs.number_color,
                position=TextPosition.TOP_LEFT,
                padding_x=30,
                padding_y=50,
                start_time_ms=100,
                end_time_ms=item_ms - 100,
                fade_in_ms=200,
                fade_out_ms=200,
                stroke_width=4,
                animation=EasingFunction.SPRING,
            ))

            # Item title (bottom)
            if item_title:
                text_overlays.append(TextOverlay(
                    text=item_title,
                    font_size=inputs.title_font_size,
                    position=TextPosition.BOTTOM_CENTER,
                    padding_y=100,
                    start_time_ms=300,
                    end_time_ms=item_ms - 200,
                    fade_in_ms=300,
                    fade_out_ms=200,
                ))

            # Subtitle
            if item_subtitle:
                text_overlays.append(TextOverlay(
                    text=item_subtitle,
                    font_size=inputs.title_font_size - 8,
                    color="#CCCCCC",
                    position=TextPosition.BOTTOM_CENTER,
                    padding_y=50,
                    start_time_ms=500,
                    end_time_ms=item_ms - 300,
                    fade_in_ms=300,
                    fade_out_ms=200,
                ))

            # #1 gets extra emphasis
            if number == 1:
                zoom_start, zoom_end = 1.0, 1.12
                effects = [("vignette", {"strength": 0.3})]
            else:
                zoom_start, zoom_end = 1.0, 1.06
                effects = []

            is_first = clip_idx == 0
            is_last = i == len(ordered_items) - 1

            clips.append(Clip(
                clip_id=f"clip_{clip_idx:03d}",
                source_type="image",
                source_path=str(img_path),
                duration_ms=item_ms,
                transition_in=TransitionType.NONE if is_first else TransitionType.ZOOM_IN,
                transition_in_duration_ms=500,
                transition_out=TransitionType.NONE if is_last else TransitionType.ZOOM_OUT,
                transition_out_duration_ms=500,
                zoom_start=zoom_start,
                zoom_end=zoom_end,
                text_overlays=text_overlays,
                effects=effects,
            ))
            clip_idx += 1

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.3,
                fade_in_ms=300,
                fade_out_ms=2000,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or inputs.countdown_title or "Countdown",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: CountdownInput) -> list[str]:
        errors = []
        if len(inputs.items) < self.min_images:
            errors.append(f"Need at least {self.min_images} items")
        for i, item in enumerate(inputs.items):
            if not isinstance(item, dict):
                errors.append(f"Item {i} must be a dict with 'image' and 'title' keys")
            elif "image" not in item:
                errors.append(f"Item {i} missing 'image' key")
        return errors
