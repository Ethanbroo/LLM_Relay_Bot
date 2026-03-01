"""Tutorial Steps template.

Numbered steps with demonstration images. Each step gets a number badge,
title, and optional description. Steps transition with slide animations.

Common for how-to content, recipes, DIY, and educational reels.
"""

import uuid
from typing import Optional
from pydantic import Field

from ..base import BaseTemplate, TemplateInput
from ...schemas import (
    Timeline, Clip, TextOverlay, AudioTrack, Resolution,
    AspectRatio, TransitionType, TextPosition, VideoFormat, CodecPreset,
)


class TutorialStepsInput(TemplateInput):
    """Inputs for the tutorial steps template."""
    step_images: list[str] = Field(min_length=3, max_length=10)
    step_titles: list[str] = Field(
        min_length=1,
        description="Title for each step, e.g. ['Prep ingredients', 'Mix batter', ...]"
    )
    step_descriptions: list[str] = Field(
        default_factory=list,
        description="Optional longer description per step"
    )
    intro_title: Optional[str] = Field(default=None, description="Title shown before steps")
    music_path: Optional[str] = None
    seconds_per_step: float = Field(default=3.5, ge=2.0, le=8.0)
    number_font_size: int = Field(default=72, ge=36, le=120)
    title_font_size: int = Field(default=40, ge=20, le=80)


class TutorialSteps(BaseTemplate):
    name = "tutorial_steps"
    description = "Numbered steps with demonstrations and titles"
    supported_platforms = ["instagram_reel", "tiktok", "youtube_short"]
    default_duration_seconds = 20
    min_images = 3
    max_images = 10

    def build_timeline(self, inputs: TutorialStepsInput) -> Timeline:
        resolution = Resolution.from_aspect_ratio(
            inputs.aspect_ratio or AspectRatio.PORTRAIT_9_16
        )

        step_ms = int(inputs.seconds_per_step * 1000)
        clips = []
        clip_idx = 0

        # Optional intro clip (reuses first image as blurred background)
        if inputs.intro_title:
            clips.append(Clip(
                clip_id=f"clip_{clip_idx:03d}",
                source_type="image",
                source_path=str(inputs.step_images[0]),
                duration_ms=2000,
                zoom_start=1.2,
                zoom_end=1.15,
                transition_out=TransitionType.FADE,
                transition_out_duration_ms=500,
                text_overlays=[
                    TextOverlay(
                        text=inputs.intro_title,
                        font_size=inputs.title_font_size + 12,
                        position=TextPosition.CENTER,
                        start_time_ms=200,
                        end_time_ms=1800,
                        fade_in_ms=400,
                        fade_out_ms=300,
                    ),
                ],
                effects=[("gaussian_blur", {"radius": 6})],
            ))
            clip_idx += 1

        for i, img_path in enumerate(inputs.step_images):
            step_num = i + 1
            text_overlays = []

            # Step number badge (top-left)
            text_overlays.append(TextOverlay(
                text=f"Step {step_num}",
                font_size=inputs.number_font_size,
                position=TextPosition.TOP_LEFT,
                padding_x=30,
                padding_y=50,
                start_time_ms=100,
                end_time_ms=step_ms - 100,
                fade_in_ms=200,
                fade_out_ms=200,
                stroke_width=4,
                color="#FFD700",
            ))

            # Step title (bottom-center)
            if i < len(inputs.step_titles) and inputs.step_titles[i]:
                text_overlays.append(TextOverlay(
                    text=inputs.step_titles[i],
                    font_size=inputs.title_font_size,
                    position=TextPosition.BOTTOM_CENTER,
                    padding_y=100,
                    start_time_ms=300,
                    end_time_ms=step_ms - 200,
                    fade_in_ms=300,
                    fade_out_ms=200,
                ))

            # Optional description (below title)
            if i < len(inputs.step_descriptions) and inputs.step_descriptions[i]:
                text_overlays.append(TextOverlay(
                    text=inputs.step_descriptions[i],
                    font_size=inputs.title_font_size - 8,
                    position=TextPosition.BOTTOM_CENTER,
                    padding_y=50,
                    start_time_ms=600,
                    end_time_ms=step_ms - 300,
                    fade_in_ms=300,
                    fade_out_ms=200,
                    color="#CCCCCC",
                ))

            is_first = clip_idx == 0
            is_last = i == len(inputs.step_images) - 1

            # Alternate slide directions for visual rhythm
            if i % 2 == 0:
                t_in = TransitionType.SLIDE_LEFT
                t_out = TransitionType.SLIDE_LEFT
            else:
                t_in = TransitionType.SLIDE_RIGHT
                t_out = TransitionType.SLIDE_RIGHT

            clips.append(Clip(
                clip_id=f"clip_{clip_idx:03d}",
                source_type="image",
                source_path=str(img_path),
                duration_ms=step_ms,
                transition_in=TransitionType.NONE if is_first else t_in,
                transition_in_duration_ms=500,
                transition_out=TransitionType.NONE if is_last else t_out,
                transition_out_duration_ms=500,
                zoom_start=1.0,
                zoom_end=1.04,
                text_overlays=text_overlays,
            ))
            clip_idx += 1

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
            title=inputs.title or "Tutorial",
            resolution=resolution,
            fps=30,
            clips=clips,
            audio_tracks=audio_tracks,
            output_format=VideoFormat.MP4,
            codec_preset=CodecPreset.STANDARD,
        )

    def validate_inputs(self, inputs: TutorialStepsInput) -> list[str]:
        errors = []
        if len(inputs.step_images) < self.min_images:
            errors.append(f"Need at least {self.min_images} step images")
        if len(inputs.step_titles) < len(inputs.step_images):
            errors.append(
                f"Need at least {len(inputs.step_images)} step titles, "
                f"got {len(inputs.step_titles)}"
            )
        return errors
