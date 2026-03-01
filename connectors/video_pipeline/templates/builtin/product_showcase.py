"""Product Showcase template.

Rotating product shots with feature/spec text overlays. Each image
gets a zoom-in with a feature callout, ending with a CTA slide.

Common for e-commerce, product launches, and brand marketing.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
)


class ProductShowcaseInput(TemplateInput):
    """Inputs for the product showcase template."""
    product_images: list[str] = Field(min_length=2, max_length=8)
    product_name: str = Field(min_length=1, max_length=100)
    features: list[str] = Field(
        default_factory=list,
        description="One feature text per image. Shown as overlay."
    )
    cta_text: str = Field(default="Shop Now", max_length=50)
    price: Optional[str] = Field(default=None, max_length=30)
    music_path: Optional[str] = None
    seconds_per_shot: float = Field(default=2.5, ge=1.5, le=6.0)
    feature_font_size: int = Field(default=36, ge=16, le=80)


class ProductShowcase(BaseTemplate):
    name = "product_showcase"
    description = "Rotating product shots with feature/spec overlays"
    supported_platforms = ["instagram_reel", "tiktok", "instagram_story"]
    default_duration_seconds = 15
    min_images = 2
    max_images = 8

    def build_timeline(self, inputs: ProductShowcaseInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        shot_ms = int(inputs.seconds_per_shot * 1000)
        clips = []

        for i, img_path in enumerate(inputs.product_images):
            text_overlays = []

            # Product name on every slide (top)
            text_overlays.append(TextOverlay(
                text=inputs.product_name,
                font_size=inputs.feature_font_size + 8,
                position=TextPosition.TOP_CENTER,
                padding_y=60,
                start_time_ms=200,
                end_time_ms=shot_ms - 100,
                fade_in_ms=300,
                fade_out_ms=200,
            ))

            # Feature callout (bottom)
            if i < len(inputs.features) and inputs.features[i]:
                text_overlays.append(TextOverlay(
                    text=inputs.features[i],
                    font_size=inputs.feature_font_size,
                    position=TextPosition.BOTTOM_CENTER,
                    padding_y=80,
                    start_time_ms=400,
                    end_time_ms=shot_ms - 200,
                    fade_in_ms=400,
                    fade_out_ms=300,
                ))

            # Alternate zoom patterns for visual interest
            patterns = [
                (1.0, 1.1, 0.0, 0.0),   # Zoom in center
                (1.1, 1.0, 0.0, 0.0),   # Zoom out center
                (1.05, 1.05, -0.3, 0.3), # Pan left to right
                (1.05, 1.05, 0.3, -0.3), # Pan right to left
            ]
            zoom_start, zoom_end, pan_start, pan_end = patterns[i % len(patterns)]

            is_first = i == 0
            is_last = i == len(inputs.product_images) - 1

            clips.append(Clip(
                clip_id=f"clip_{i:03d}",
                source_type="image",
                source_path=str(img_path),
                duration_ms=shot_ms,
                transition_in=TransitionType.NONE if is_first else TransitionType.ZOOM_IN,
                transition_in_duration_ms=500,
                transition_out=TransitionType.NONE if is_last else TransitionType.ZOOM_OUT,
                transition_out_duration_ms=500,
                zoom_start=zoom_start,
                zoom_end=zoom_end,
                pan_x_start=pan_start,
                pan_x_end=pan_end,
                text_overlays=text_overlays,
            ))

        # Add CTA as final text overlay on last clip
        if clips and inputs.cta_text:
            cta_parts = [inputs.cta_text]
            if inputs.price:
                cta_parts.append(inputs.price)
            clips[-1].text_overlays.append(TextOverlay(
                text=" | ".join(cta_parts),
                font_size=inputs.feature_font_size + 4,
                position=TextPosition.CENTER,
                start_time_ms=shot_ms // 2,
                end_time_ms=shot_ms,
                fade_in_ms=500,
                fade_out_ms=0,
                color="#FFD700",
            ))

        audio_tracks = []
        if inputs.music_path:
            audio_tracks.append(AudioTrack(
                source_path=str(inputs.music_path),
                start_time_ms=0,
                volume=0.25,
                fade_in_ms=300,
                fade_out_ms=1500,
            ))

        return Timeline(
            timeline_id=f"tl_{uuid.uuid4().hex[:12]}",
            title=inputs.title or f"{inputs.product_name} Showcase",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: ProductShowcaseInput) -> list[str]:
        errors = []
        if len(inputs.product_images) < self.min_images:
            errors.append(f"Need at least {self.min_images} product images")
        if inputs.features and len(inputs.features) > len(inputs.product_images):
            errors.append("More features than images")
        return errors
