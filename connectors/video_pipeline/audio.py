"""Audio mixing and processing for video pipeline.

Handles background music, voiceover sync, volume control, and fade effects.
Uses pydub for audio manipulation and FFmpeg for format conversion.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from .schemas import Timeline, AudioTrack

logger = logging.getLogger(__name__)


def _check_pydub():
    """Verify pydub is importable."""
    try:
        from pydub import AudioSegment  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_audio_tracks(timeline: Timeline, audio_library_dir: Optional[Path] = None) -> list[dict]:
    """Resolve audio track source paths.

    Handles the __RESOLVE_BY_MOOD__ sentinel by looking up audio files
    in the audio library directory.

    Args:
        timeline: Timeline with audio tracks
        audio_library_dir: Directory containing categorized audio files.
                          Expected structure: audio_library_dir/{mood}/*.mp3

    Returns:
        List of dicts with resolved paths and metadata.
        Each dict has: source_path, start_time_ms, volume, fade_in_ms, fade_out_ms
    """
    resolved = []

    for track in timeline.audio_tracks:
        if track.source_path == "__RESOLVE_BY_MOOD__":
            if audio_library_dir is None:
                logger.warning("Audio track has mood sentinel but no audio library provided, skipping")
                continue

            # For now, log that mood-based resolution is not yet implemented
            logger.info("Mood-based audio resolution placeholder -- requires audio library setup")
            continue

        resolved.append({
            "source_path": track.source_path,
            "start_time_ms": track.start_time_ms,
            "trim_start_ms": track.trim_start_ms,
            "trim_end_ms": track.trim_end_ms,
            "volume": track.volume,
            "fade_in_ms": track.fade_in_ms,
            "fade_out_ms": track.fade_out_ms,
        })

    return resolved


def mix_audio(
    tracks: list[dict],
    total_duration_ms: int,
    output_path: Path,
    log_daemon=None,
) -> Optional[Path]:
    """Mix multiple audio tracks into a single file.

    Args:
        tracks: Resolved audio tracks from resolve_audio_tracks()
        total_duration_ms: Total video duration for trimming
        output_path: Where to save the mixed audio file
        log_daemon: Optional LogDaemon for audit events

    Returns:
        Path to mixed audio file, or None if no tracks to mix
    """
    if not tracks:
        return None

    if not _check_pydub():
        logger.warning("pydub not installed, skipping audio mixing. Install: pip install pydub")
        return None

    from pydub import AudioSegment

    # Create a silent base track of the target duration
    mixed = AudioSegment.silent(duration=total_duration_ms)

    for track_info in tracks:
        source_path = Path(track_info["source_path"])
        if not source_path.exists():
            logger.warning("Audio file not found: %s, skipping", source_path)
            continue

        try:
            audio = AudioSegment.from_file(str(source_path))
        except Exception as e:
            logger.warning("Failed to load audio %s: %s, skipping", source_path, e)
            continue

        # Trim source
        trim_start = track_info.get("trim_start_ms", 0)
        trim_end = track_info.get("trim_end_ms")
        if trim_end is not None:
            audio = audio[trim_start:trim_end]
        elif trim_start > 0:
            audio = audio[trim_start:]

        # Apply volume adjustment (1.0 = no change)
        volume = track_info.get("volume", 1.0)
        if volume != 1.0:
            db_change = 20 * (volume - 1.0)  # Approximate dB change
            audio = audio + db_change

        # Apply fade in/out
        fade_in = track_info.get("fade_in_ms", 0)
        fade_out = track_info.get("fade_out_ms", 0)
        if fade_in > 0:
            audio = audio.fade_in(fade_in)
        if fade_out > 0:
            audio = audio.fade_out(fade_out)

        # Overlay at the specified start time
        start_time = track_info.get("start_time_ms", 0)
        mixed = mixed.overlay(audio, position=start_time)

    # Trim to video duration
    mixed = mixed[:total_duration_ms]

    # Export
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mixed.export(str(output_path), format="mp3", bitrate="192k")

    logger.info("Audio mixed: %s (%.1fs)", output_path, total_duration_ms / 1000)

    if log_daemon:
        log_daemon.ingest_event(
            event_type="VIDEO_AUDIO_MIXED",
            actor="video_pipeline.audio",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "output_path": str(output_path),
                "track_count": len(tracks),
                "duration_ms": total_duration_ms,
            }
        )

    return output_path
