"""Picture-in-Picture overlay using FFmpeg. No AI generation costs.

Replaces Kling O1 Edit for gameplay overlay content.
Composites a small AI character reaction clip over gameplay footage.
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class PiPCompositor:
    """Picture-in-Picture overlay using FFmpeg. No AI generation costs."""

    POSITION_MAP = {
        "bottom_right": "W-w-20:H-h-20",
        "bottom_left": "20:H-h-20",
        "top_right": "W-w-20:20",
        "top_left": "20:20",
    }

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    def validate_inputs(self, background_video: str, pip_video: str):
        """Check both files exist and are valid video via ffprobe."""
        for path in (background_video, pip_video):
            if not os.path.exists(path):
                raise FileNotFoundError(f"Video file not found: {path}")
            result = subprocess.run(
                [self.ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error", path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise ValueError(f"Invalid video file {path}: {result.stderr}")

    def overlay(
        self,
        background_video: str,
        pip_video: str,
        output_path: str,
        pip_position: str = "bottom_right",
        pip_scale: float = 0.30,
        target_aspect: str = "9:16",
        pip_border: int = 2,
        pip_radius: int = 12,
    ) -> str:
        """Overlay pip_video in corner of background_video.

        Args:
            background_video: Path to background gameplay video
            pip_video: Path to AI character reaction clip
            output_path: Where to write the composited video
            pip_position: Corner placement
            pip_scale: Fraction of output width (0.30 = 30%)
            target_aspect: Output aspect ratio
            pip_border: Border width around PiP (pixels)
            pip_radius: Corner radius for PiP (pixels)

        Returns:
            Path to output file
        """
        self.validate_inputs(background_video, pip_video)

        # Parse target dimensions
        if target_aspect == "9:16":
            out_w, out_h = 1080, 1920
        elif target_aspect == "1:1":
            out_w, out_h = 1080, 1080
        else:
            out_w, out_h = 1920, 1080

        pip_w = int(out_w * pip_scale)

        overlay_pos = self.POSITION_MAP.get(pip_position, "W-w-20:H-h-20")

        # Build filter chain
        # 1. Scale + crop background to target aspect
        # 2. Scale PiP to pip_scale * output_width, maintain aspect ratio
        # 3. Overlay PiP onto background
        filter_complex = (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}[bg];"
            f"[1:v]scale={pip_w}:-1[pip];"
            f"[bg][pip]overlay={overlay_pos}[out]"
        )

        cmd = [
            self.ffmpeg,
            "-i", background_video,
            "-i", pip_video,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path,
            "-y",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg PiP overlay failed: {result.stderr}")

        logger.info(
            "PiP composite: %s + %s → %s (pip_scale=%.0f%%, pos=%s)",
            background_video, pip_video, output_path,
            pip_scale * 100, pip_position,
        )
        return output_path

    def overlay_with_border(
        self,
        background_video: str,
        pip_video: str,
        output_path: str,
        pip_position: str = "bottom_right",
        pip_scale: float = 0.30,
        target_aspect: str = "9:16",
        border_color: str = "white",
        border_width: int = 3,
    ) -> str:
        """Overlay with a colored border around PiP for visibility."""
        self.validate_inputs(background_video, pip_video)

        if target_aspect == "9:16":
            out_w, out_h = 1080, 1920
        elif target_aspect == "1:1":
            out_w, out_h = 1080, 1080
        else:
            out_w, out_h = 1920, 1080

        pip_w = int(out_w * pip_scale)
        overlay_pos = self.POSITION_MAP.get(pip_position, "W-w-20:H-h-20")

        # Add border by padding the PiP clip
        filter_complex = (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}[bg];"
            f"[1:v]scale={pip_w}:-1,"
            f"pad=iw+{border_width * 2}:ih+{border_width * 2}:"
            f"{border_width}:{border_width}:color={border_color}[pip];"
            f"[bg][pip]overlay={overlay_pos}[out]"
        )

        cmd = [
            self.ffmpeg,
            "-i", background_video,
            "-i", pip_video,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path,
            "-y",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg PiP overlay with border failed: {result.stderr}")

        logger.info(
            "PiP composite with %s border: %s → %s",
            border_color, pip_video, output_path,
        )
        return output_path
