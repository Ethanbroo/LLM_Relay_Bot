"""Multi-platform video output.

Takes a single storyboard or timeline and renders it for multiple platforms
in a single pipeline run. One storyboard -> multiple outputs (Instagram Reel,
TikTok, YouTube Short, Instagram Story, etc.).

Handles:
- Aspect ratio adaptation (reframing via compositor, not just cropping)
- Platform-specific duration limits (clamps to max)
- Platform-specific text safe zones (repositions overlays)
- Platform-specific codec requirements

Usage:
    from connectors.video_pipeline.multi_platform import render_multi_platform

    outputs = render_multi_platform(
        storyboard=storyboard,
        platforms=["instagram_reel", "tiktok", "youtube_short"],
        image_cache=image_cache,
        output_dir=Path("output/video"),
    )
    # outputs = {"instagram_reel": Path(...), "tiktok": Path(...), ...}
"""

import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from .schemas import (
    Timeline, Storyboard, AspectRatio, Resolution,
    TextPosition, VideoFormat, CodecPreset,
)
from .timeline import storyboard_to_timeline, PLATFORM_DEFAULTS
from .compositor import FrameCompositor
from .encoder import encode_video, check_ffmpeg
from .audio import resolve_audio_tracks, mix_audio

logger = logging.getLogger(__name__)


# Platform-specific safe zones: percentage of frame covered by platform UI.
# Text overlays inside these zones get repositioned to avoid being hidden
# behind usernames, like buttons, comment boxes, etc.
SAFE_ZONES = {
    "instagram_reel": {
        "bottom_percent": 0.25,  # Username, caption, buttons
        "top_percent": 0.10,     # Status bar, close button
        "right_percent": 0.08,   # Like/comment/share buttons
    },
    "instagram_story": {
        "bottom_percent": 0.20,  # Reply bar, username
        "top_percent": 0.12,     # Status bar, profile icon
        "right_percent": 0.05,
    },
    "tiktok": {
        "bottom_percent": 0.20,  # Caption, buttons
        "top_percent": 0.08,     # Status bar
        "right_percent": 0.12,   # Larger interaction panel (like, comment, share, etc.)
    },
    "youtube_short": {
        "bottom_percent": 0.15,  # Title, subscribe
        "top_percent": 0.05,     # Status bar
        "right_percent": 0.05,
    },
    "instagram_feed": {
        "bottom_percent": 0.0,
        "top_percent": 0.0,
        "right_percent": 0.0,
    },
    "youtube": {
        "bottom_percent": 0.0,
        "top_percent": 0.0,
        "right_percent": 0.0,
    },
}

# Platform-specific codec preferences
PLATFORM_CODECS = {
    "instagram_reel": {"format": VideoFormat.MP4, "preset": CodecPreset.STANDARD},
    "instagram_story": {"format": VideoFormat.MP4, "preset": CodecPreset.STANDARD},
    "tiktok": {"format": VideoFormat.MP4, "preset": CodecPreset.STANDARD},
    "youtube_short": {"format": VideoFormat.MP4, "preset": CodecPreset.STANDARD},
    "instagram_feed": {"format": VideoFormat.MP4, "preset": CodecPreset.STANDARD},
    "youtube": {"format": VideoFormat.MP4, "preset": CodecPreset.HIGH_QUALITY},
}


def adapt_timeline_for_platform(
    timeline: Timeline,
    platform: str,
) -> Timeline:
    """Create a platform-adapted copy of a timeline.

    Adjusts:
    - Resolution / aspect ratio
    - Duration (clamp to platform max)
    - Text overlay positions (safe zones)
    - Codec / format settings

    The original timeline is not modified.

    Args:
        timeline: Source timeline
        platform: Target platform identifier

    Returns:
        New Timeline adapted for the platform

    Raises:
        ValueError: If platform is unsupported
    """
    defaults = PLATFORM_DEFAULTS.get(platform)
    if defaults is None:
        raise ValueError(
            f"Unsupported platform '{platform}'. "
            f"Supported: {sorted(PLATFORM_DEFAULTS.keys())}"
        )

    # Deep copy via model serialization
    adapted = timeline.model_copy(deep=True)

    # Adapt resolution
    target_ratio = defaults["aspect_ratio"]
    if adapted.resolution.width != Resolution.from_aspect_ratio(target_ratio).width:
        adapted.resolution = Resolution.from_aspect_ratio(target_ratio)

    # Adapt FPS
    adapted.fps = defaults.get("fps", 30)

    # Adapt format/codec
    codec_prefs = PLATFORM_CODECS.get(platform, {})
    if codec_prefs:
        adapted.output_format = codec_prefs.get("format", adapted.output_format)
        adapted.codec_preset = codec_prefs.get("preset", adapted.codec_preset)

    # Clamp duration: drop clips from the end if timeline exceeds max duration
    max_duration_ms = defaults["max_duration_s"] * 1000
    while adapted.total_duration_ms > max_duration_ms and len(adapted.clips) > 1:
        adapted.clips.pop()

    # Adjust text overlays for safe zones
    safe_zone = SAFE_ZONES.get(platform, {})
    _adjust_text_for_safe_zone(adapted, safe_zone)

    return adapted


def _adjust_text_for_safe_zone(timeline: Timeline, safe_zone: dict):
    """Move text overlays away from platform UI safe zones.

    Modifies the timeline in place. Text near the bottom is pushed up
    above the safe zone, etc.

    Args:
        timeline: Timeline to adjust (modified in place)
        safe_zone: Dict with bottom_percent, top_percent, right_percent
    """
    if not safe_zone:
        return

    h = timeline.resolution.height
    w = timeline.resolution.width

    bottom_px = round(h * safe_zone.get("bottom_percent", 0))
    top_px = round(h * safe_zone.get("top_percent", 0))
    right_px = round(w * safe_zone.get("right_percent", 0))

    all_overlays = []
    for clip in timeline.clips:
        all_overlays.extend(clip.text_overlays)
    all_overlays.extend(timeline.global_text_overlays)

    for overlay in all_overlays:
        pos = overlay.position.value

        # Push bottom text up above the safe zone
        if "bottom" in pos:
            overlay.padding_y = max(overlay.padding_y, bottom_px)

        # Push top text below the safe zone
        if "top" in pos:
            overlay.padding_y = max(overlay.padding_y, top_px)

        # Push right-aligned text left of the safe zone
        if "right" in pos:
            overlay.padding_x = max(overlay.padding_x, right_px)


def render_multi_platform(
    platforms: list[str],
    image_cache: dict[str, Image.Image],
    output_dir: Path,
    storyboard: Optional[Storyboard] = None,
    timeline: Optional[Timeline] = None,
    audio_library_dir: Optional[Path] = None,
    log_daemon=None,
) -> dict[str, Path]:
    """Render a storyboard or timeline for multiple platforms.

    Provide either a storyboard (will be converted to timeline per platform)
    or a timeline (will be adapted per platform).

    Args:
        platforms: List of platform identifiers (e.g. ["instagram_reel", "tiktok"])
        image_cache: Pre-loaded images keyed by clip_id
        output_dir: Directory to write output files
        storyboard: Source storyboard (alternative to timeline)
        timeline: Source timeline (alternative to storyboard)
        audio_library_dir: Directory for resolving audio tracks
        log_daemon: Optional LogDaemon for audit events

    Returns:
        Dict mapping platform name -> output video path

    Raises:
        ValueError: If neither storyboard nor timeline is provided
    """
    if storyboard is None and timeline is None:
        raise ValueError("Must provide either storyboard or timeline")

    if not check_ffmpeg():
        raise RuntimeError("FFmpeg is required for video encoding")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _audit(event_type: str, payload: dict):
        if log_daemon:
            log_daemon.ingest_event(
                event_type=event_type,
                actor="video_pipeline.multi_platform",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload=payload,
            )

    _audit("MULTI_PLATFORM_RENDER_STARTED", {
        "platforms": platforms,
        "source_type": "storyboard" if storyboard else "timeline",
    })

    outputs = {}
    errors = {}

    for platform in platforms:
        if platform not in PLATFORM_DEFAULTS:
            logger.warning("Skipping unsupported platform: %s", platform)
            _audit("MULTI_PLATFORM_SKIP", {
                "platform": platform,
                "reason": "unsupported",
            })
            continue

        try:
            # Build platform-specific timeline
            if storyboard is not None:
                # Create a platform-targeted copy of the storyboard
                platform_storyboard = storyboard.model_copy(deep=True)
                platform_storyboard.target_platform = platform

                # Clamp duration
                max_dur = PLATFORM_DEFAULTS[platform]["max_duration_s"]
                if platform_storyboard.target_duration_seconds > max_dur:
                    platform_storyboard.target_duration_seconds = max_dur

                platform_timeline = storyboard_to_timeline(
                    platform_storyboard, log_daemon=log_daemon
                )
            else:
                platform_timeline = adapt_timeline_for_platform(timeline, platform)

            # Resolve and mix audio
            audio_path = None
            resolved_tracks = resolve_audio_tracks(platform_timeline, audio_library_dir)
            if resolved_tracks:
                import tempfile
                audio_output = Path(tempfile.mkdtemp()) / f"{platform}_audio.mp3"
                audio_path = mix_audio(
                    tracks=resolved_tracks,
                    total_duration_ms=platform_timeline.total_duration_ms,
                    output_path=audio_output,
                    log_daemon=log_daemon,
                )

            # Render
            ext = platform_timeline.output_format.value
            output_filename = f"{platform_timeline.timeline_id}_{platform}.{ext}"
            output_path = output_dir / output_filename

            compositor = FrameCompositor(platform_timeline, image_cache)
            total_frames = platform_timeline.total_frames

            def frame_gen(comp=compositor, n=total_frames):
                for i in range(n):
                    yield comp.render_frame(i)

            result_path = encode_video(
                frame_generator=frame_gen(),
                timeline=platform_timeline,
                output_path=output_path,
                audio_path=Path(audio_path) if audio_path else None,
                log_daemon=log_daemon,
            )

            outputs[platform] = result_path

            _audit("MULTI_PLATFORM_RENDERED", {
                "platform": platform,
                "output_path": str(result_path),
                "resolution": f"{platform_timeline.resolution.width}x{platform_timeline.resolution.height}",
                "duration_ms": platform_timeline.total_duration_ms,
                "frame_count": total_frames,
            })

            logger.info(
                "Rendered %s: %s (%dx%d, %dms)",
                platform, result_path,
                platform_timeline.resolution.width,
                platform_timeline.resolution.height,
                platform_timeline.total_duration_ms,
            )

        except Exception as e:
            logger.error("Failed to render for %s: %s", platform, e)
            errors[platform] = str(e)
            _audit("MULTI_PLATFORM_FAILED", {
                "platform": platform,
                "error": str(e)[:200],
            })

    _audit("MULTI_PLATFORM_RENDER_COMPLETED", {
        "platforms_requested": platforms,
        "platforms_rendered": list(outputs.keys()),
        "platforms_failed": list(errors.keys()),
    })

    return outputs
