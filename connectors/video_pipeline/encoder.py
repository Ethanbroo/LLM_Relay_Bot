"""FFmpeg-based video encoder.

Encodes a sequence of PIL Image frames into a video file using FFmpeg.
Also handles audio muxing (combining video + audio tracks).

FFmpeg is called as a subprocess -- it must be installed on the system.
Install: brew install ffmpeg (macOS) or apt-get install ffmpeg (Ubuntu)
"""

import subprocess
import shutil
import logging
from pathlib import Path
from typing import Optional, Generator
from PIL import Image

from .schemas import Timeline, VideoFormat, CodecPreset
from .constants import MP4_CRF, WEBM_CRF, MP4_AUDIO_BITRATE

logger = logging.getLogger(__name__)


def check_ffmpeg() -> bool:
    """Verify FFmpeg is installed and accessible."""
    return shutil.which("ffmpeg") is not None


def _get_codec_args(fmt: VideoFormat, preset: CodecPreset) -> list[str]:
    """Get FFmpeg codec arguments for the target format."""
    if fmt == VideoFormat.MP4:
        return [
            "-c:v", "libx264",
            "-preset", preset.value,
            "-crf", str(MP4_CRF),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
    elif fmt == VideoFormat.WEBM:
        return [
            "-c:v", "libvpx-vp9",
            "-crf", str(WEBM_CRF),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    elif fmt == VideoFormat.GIF:
        return [
            "-vf", "fps=15,scale=480:-1:flags=lanczos",
        ]
    else:
        raise ValueError(f"Unsupported format for encoding: {fmt}")


def encode_video(
    frame_generator: Generator[Image.Image, None, None],
    timeline: Timeline,
    output_path: Path,
    audio_path: Optional[Path] = None,
    log_daemon=None,
) -> Path:
    """Encode frames into a video file.

    Pipes raw frames to FFmpeg via stdin for maximum throughput (no disk I/O
    for intermediate frames).

    Args:
        frame_generator: Yields PIL Images in sequence, one per frame
        timeline: Timeline specification (for FPS, resolution, format)
        output_path: Where to write the output video file
        audio_path: Optional mixed audio file to mux with video
        log_daemon: Optional LogDaemon for audit events

    Returns:
        Path to the encoded video file

    Raises:
        RuntimeError: If FFmpeg is not installed or encoding fails
    """
    if not check_ffmpeg():
        raise RuntimeError(
            "FFmpeg not found. Install it:\n"
            "  macOS: brew install ffmpeg\n"
            "  Ubuntu: sudo apt-get install ffmpeg\n"
            "  Windows: choco install ffmpeg"
        )

    w = timeline.resolution.width
    h = timeline.resolution.height

    # Build FFmpeg command
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{w}x{h}",
        "-pix_fmt", "rgb24",
        "-r", str(timeline.fps),
        "-i", "-",
    ]

    # Add audio input if provided
    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
        cmd.extend(["-c:a", "aac", "-b:a", MP4_AUDIO_BITRATE])
        cmd.extend(["-shortest"])

    # Add codec args
    cmd.extend(_get_codec_args(timeline.output_format, timeline.codec_preset))

    # Output path
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd.append(str(output_path))

    if log_daemon:
        log_daemon.ingest_event(
            event_type="VIDEO_ENCODE_STARTED",
            actor="video_pipeline.encoder",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "output_path": str(output_path),
                "resolution": f"{w}x{h}",
                "fps": timeline.fps,
                "format": timeline.output_format.value,
                "preset": timeline.codec_preset.value,
            }
        )

    logger.info(
        "Starting FFmpeg encode: %s (%dx%d @ %dfps, %s %s)",
        output_path, w, h, timeline.fps,
        timeline.output_format.value, timeline.codec_preset.value
    )

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    frame_count = 0
    try:
        for frame in frame_generator:
            if frame.mode != "RGB":
                frame = frame.convert("RGB")
            if frame.size != (w, h):
                frame = frame.resize((w, h), Image.LANCZOS)

            raw_bytes = frame.tobytes()
            process.stdin.write(raw_bytes)
            frame_count += 1

            # Progress logging every second of video
            if frame_count % timeline.fps == 0:
                seconds_rendered = frame_count / timeline.fps
                logger.info("Encoded %.1fs (%d frames)", seconds_rendered, frame_count)
    finally:
        process.stdin.close()

    stdout, stderr = process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace")
        logger.error("FFmpeg failed (exit %d): %s", process.returncode, error_msg[:500])
        if log_daemon:
            log_daemon.ingest_event(
                event_type="VIDEO_ENCODE_FAILED",
                actor="video_pipeline.encoder",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload={"error": error_msg[:500]}
            )
        raise RuntimeError(f"FFmpeg encoding failed (exit {process.returncode}): {error_msg}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg produced empty output at {output_path}")

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    duration_seconds = round(frame_count / timeline.fps, 2)

    logger.info(
        "Encode complete: %s (%.2f MB, %.1fs, %d frames)",
        output_path, file_size_mb, duration_seconds, frame_count
    )

    if log_daemon:
        log_daemon.ingest_event(
            event_type="VIDEO_ENCODE_COMPLETED",
            actor="video_pipeline.encoder",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "output_path": str(output_path),
                "frame_count": frame_count,
                "file_size_mb": round(file_size_mb, 2),
                "duration_seconds": duration_seconds,
            }
        )

    return output_path
