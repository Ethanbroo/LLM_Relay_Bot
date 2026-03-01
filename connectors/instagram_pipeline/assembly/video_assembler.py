"""Video assembler — final composition for multi-clip content.

Concatenates clips with transitions, mixes audio, and validates
format specs for Instagram publishing requirements.
"""

import logging
import os
import tempfile
from typing import Optional

from ..generation.clip_sequencer import ClipSequencer
from ..audio.audio_mixer import AudioMixer
from ..quality.frame_extractor import FrameExtractor

logger = logging.getLogger(__name__)

# Instagram video requirements
INSTAGRAM_VIDEO_SPECS = {
    "reel": {
        "max_duration": 90,      # seconds
        "min_duration": 3,
        "max_file_size_mb": 250,
        "aspect_ratios": ["9:16"],
        "codec": "h264",
        "audio_codec": "aac",
        "max_width": 1080,
        "max_height": 1920,
    },
    "feed": {
        "max_duration": 60,
        "min_duration": 3,
        "max_file_size_mb": 250,
        "aspect_ratios": ["1:1", "4:5", "16:9"],
        "codec": "h264",
        "audio_codec": "aac",
        "max_width": 1080,
        "max_height": 1350,
    },
    "story": {
        "max_duration": 60,
        "min_duration": 1,
        "max_file_size_mb": 250,
        "aspect_ratios": ["9:16"],
        "codec": "h264",
        "audio_codec": "aac",
        "max_width": 1080,
        "max_height": 1920,
    },
}


class VideoAssembler:
    """Final video composition, format validation, and packaging."""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.sequencer = ClipSequencer(ffmpeg_path=ffmpeg_path)
        self.audio_mixer = AudioMixer(ffmpeg_path=ffmpeg_path)
        self.frame_extractor = FrameExtractor(ffmpeg_path=ffmpeg_path)
        self.ffmpeg = ffmpeg_path

    def assemble_multi_clip(
        self,
        clip_paths: list[str],
        output_path: str,
        transition: str = "crossfade",
        transition_duration: float = 0.5,
        audio_path: Optional[str] = None,
        normalize_audio: bool = True,
    ) -> str:
        """Assemble multiple clips into a single video.

        Args:
            clip_paths: Ordered list of clip file paths
            output_path: Where to write the final video
            transition: Transition type between clips
            transition_duration: Duration of transitions
            audio_path: Optional separate audio track to mix in
            normalize_audio: Whether to normalize to -14 LUFS

        Returns:
            Path to assembled video
        """
        logger.info(
            "Assembling %d clips with %s transitions",
            len(clip_paths), transition,
        )

        # Step 1: Concatenate clips
        concat_path = output_path
        if audio_path:
            # Need intermediate file if we're also mixing audio
            concat_path = tempfile.mktemp(suffix=".mp4", prefix="concat_")

        self.sequencer.concatenate_clips(
            clip_paths, concat_path, transition, transition_duration
        )

        # Step 2: Mix in audio if provided
        if audio_path:
            self.audio_mixer.add_audio_to_video(
                concat_path, audio_path, output_path
            )
            os.unlink(concat_path)

        # Step 3: Normalize audio
        if normalize_audio:
            normalized_path = output_path + ".normalized.mp4"
            try:
                self.audio_mixer.normalize_audio(
                    output_path, normalized_path, target_lufs=-14
                )
                os.replace(normalized_path, output_path)
            except Exception as e:
                logger.warning("Audio normalization failed: %s", e)
                if os.path.exists(normalized_path):
                    os.unlink(normalized_path)

        # Log final stats
        info = self.frame_extractor.get_video_info(output_path)
        logger.info(
            "Video assembled: %.1fs, %dx%d, %s",
            info["duration"], info["width"], info["height"], output_path,
        )

        return output_path

    def validate_for_instagram(
        self, video_path: str, post_type: str = "reel"
    ) -> tuple[bool, list[str]]:
        """Validate video meets Instagram publishing requirements.

        Args:
            video_path: Path to video file
            post_type: "reel", "feed", or "story"

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        specs = INSTAGRAM_VIDEO_SPECS.get(post_type, INSTAGRAM_VIDEO_SPECS["reel"])
        issues = []

        info = self.frame_extractor.get_video_info(video_path)

        # Duration check
        duration = info.get("duration", 0)
        if duration < specs["min_duration"]:
            issues.append(
                f"Video too short: {duration:.1f}s (min {specs['min_duration']}s)"
            )
        if duration > specs["max_duration"]:
            issues.append(
                f"Video too long: {duration:.1f}s (max {specs['max_duration']}s)"
            )

        # File size check
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if file_size_mb > specs["max_file_size_mb"]:
            issues.append(
                f"File too large: {file_size_mb:.1f}MB (max {specs['max_file_size_mb']}MB)"
            )

        # Resolution check
        width = info.get("width", 0)
        height = info.get("height", 0)
        if width > specs["max_width"]:
            issues.append(f"Width too large: {width} (max {specs['max_width']})")
        if height > specs["max_height"]:
            issues.append(f"Height too large: {height} (max {specs['max_height']})")

        is_valid = len(issues) == 0
        if is_valid:
            logger.info("Video passes Instagram %s validation", post_type)
        else:
            logger.warning(
                "Video fails Instagram %s validation: %s",
                post_type, "; ".join(issues),
            )

        return is_valid, issues

    def extract_thumbnail(
        self, video_path: str, output_path: Optional[str] = None
    ) -> str:
        """Extract first frame as thumbnail for video posts."""
        return self.frame_extractor.extract_first_frame(video_path, output_path)
