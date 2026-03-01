"""Timeline engine: converts Storyboard (LLM output) into Timeline (rendering spec).

This module is the bridge between the creative layer (LLM agents) and the
rendering layer (compositor + encoder).
"""

import uuid
import logging
from typing import Optional

from .schemas import (
    Storyboard, Timeline, Clip, Resolution, AspectRatio,
    TransitionType, TextOverlay, TextPosition, AudioTrack,
    VideoFormat, CodecPreset, EasingFunction
)

logger = logging.getLogger(__name__)

# Platform-specific defaults
PLATFORM_DEFAULTS = {
    "instagram_reel": {
        "aspect_ratio": AspectRatio.PORTRAIT_9_16,
        "max_duration_s": 90,
        "fps": 30,
        "format": VideoFormat.MP4,
    },
    "instagram_story": {
        "aspect_ratio": AspectRatio.PORTRAIT_9_16,
        "max_duration_s": 60,
        "fps": 30,
        "format": VideoFormat.MP4,
    },
    "tiktok": {
        "aspect_ratio": AspectRatio.PORTRAIT_9_16,
        "max_duration_s": 180,
        "fps": 30,
        "format": VideoFormat.MP4,
    },
    "youtube_short": {
        "aspect_ratio": AspectRatio.PORTRAIT_9_16,
        "max_duration_s": 60,
        "fps": 30,
        "format": VideoFormat.MP4,
    },
    "instagram_feed": {
        "aspect_ratio": AspectRatio.SQUARE,
        "max_duration_s": 60,
        "fps": 30,
        "format": VideoFormat.MP4,
    },
    "youtube": {
        "aspect_ratio": AspectRatio.LANDSCAPE_16_9,
        "max_duration_s": 600,
        "fps": 30,
        "format": VideoFormat.MP4,
    },
}


def storyboard_to_timeline(
    storyboard: Storyboard,
    log_daemon=None,
) -> Timeline:
    """Convert an LLM-generated storyboard into a renderable Timeline.

    Args:
        storyboard: Validated Storyboard from the content agent
        log_daemon: Optional LogDaemon for audit events

    Returns:
        Timeline ready for rendering

    Raises:
        ValueError: If storyboard references unknown platforms or invalid scenes
    """
    platform = PLATFORM_DEFAULTS.get(storyboard.target_platform)
    if platform is None:
        raise ValueError(
            f"Unknown platform '{storyboard.target_platform}'. "
            f"Supported: {list(PLATFORM_DEFAULTS.keys())}"
        )

    resolution = Resolution.from_aspect_ratio(platform["aspect_ratio"])
    timeline_id = f"tl_{uuid.uuid4().hex[:12]}"

    scene_count = len(storyboard.scenes)
    if scene_count == 0:
        raise ValueError("Storyboard must have at least one scene")

    # Clamp to platform max duration
    clamped_duration = min(
        storyboard.target_duration_seconds,
        platform["max_duration_s"]
    )
    target_ms = clamped_duration * 1000
    base_duration_per_scene = target_ms // scene_count

    clips = []
    for i, scene in enumerate(storyboard.scenes):
        clip_id = f"clip_{i:03d}"

        # Determine transition based on position
        transition_in = TransitionType.NONE if i == 0 else TransitionType.DISSOLVE
        transition_out = TransitionType.NONE if i == scene_count - 1 else TransitionType.DISSOLVE

        # Scene-specific duration override (LLM can suggest)
        duration_ms = scene.get("duration_ms", base_duration_per_scene)
        duration_ms = max(1000, min(duration_ms, 30_000))

        # Determine source type
        if scene.get("image_path"):
            source_type = "image"
            source_path = scene["image_path"]
            ai_prompt = None
        elif scene.get("prompt"):
            source_type = "ai_generated"
            source_path = None
            ai_prompt = scene["prompt"]
        else:
            raise ValueError(f"Scene {i} must have either 'image_path' or 'prompt'")

        # Ken Burns effect for static images (subtle motion)
        zoom_start = scene.get("zoom_start", 1.0)
        zoom_end = scene.get("zoom_end", 1.05)

        # Text overlays for this scene
        text_overlays = []
        if scene.get("text"):
            text_overlays.append(TextOverlay(
                text=scene["text"],
                font_size=scene.get("font_size", 48),
                position=TextPosition(scene.get("text_position", "bottom_center")),
                start_time_ms=0,
                end_time_ms=duration_ms,
                color=scene.get("text_color", "#FFFFFF"),
            ))

        clips.append(Clip(
            clip_id=clip_id,
            source_type=source_type,
            source_path=source_path,
            ai_prompt=ai_prompt,
            character_id=scene.get("character_id"),
            duration_ms=duration_ms,
            transition_in=transition_in,
            transition_in_duration_ms=500,
            transition_out=transition_out,
            transition_out_duration_ms=500,
            zoom_start=zoom_start,
            zoom_end=zoom_end,
            text_overlays=text_overlays,
        ))

    # Audio tracks
    audio_tracks = []
    if storyboard.music_mood:
        audio_tracks.append(AudioTrack(
            source_path="__RESOLVE_BY_MOOD__",
            start_time_ms=0,
            volume=0.3,
            fade_in_ms=1000,
            fade_out_ms=2000,
        ))

    timeline = Timeline(
        timeline_id=timeline_id,
        title=storyboard.concept,
        description=storyboard.concept,
        resolution=resolution,
        fps=platform["fps"],
        clips=clips,
        audio_tracks=audio_tracks,
        output_format=platform["format"],
        codec_preset=CodecPreset.STANDARD,
    )

    if log_daemon:
        log_daemon.ingest_event(
            event_type="VIDEO_TIMELINE_CREATED",
            actor="video_pipeline.timeline",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "timeline_id": timeline_id,
                "platform": storyboard.target_platform,
                "clip_count": len(clips),
                "total_duration_ms": timeline.total_duration_ms,
                "resolution": f"{resolution.width}x{resolution.height}",
            }
        )

    logger.info(
        "Timeline created: %s (%d clips, %dms, %dx%d)",
        timeline_id, len(clips), timeline.total_duration_ms,
        resolution.width, resolution.height
    )

    return timeline
