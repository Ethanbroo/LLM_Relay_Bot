"""Multi-clip sequencing for long-form video content.

Chains multiple Kling-generated clips into a single video.
Uses last-frame-as-first-frame technique for visual continuity,
and FFmpeg for crossfade transitions between clips.

NOTE: Prefer generate_single_multishot() for narrative reels.
Uses Kling O3's native multi-shot in a single API call for better
identity consistency, lower cost, and no frame-chaining drift.
The chained concatenate_clips() approach produces identity drift after 2+ clips.
Only use concatenation for content longer than 6 shots (exceeding O3's limit).
"""

import logging
import os
import subprocess
import tempfile
from typing import Optional

from ..quality.frame_extractor import FrameExtractor
from ..audio.audio_mixer import AudioMixer

logger = logging.getLogger(__name__)


class ClipSequencer:
    """Chains multiple video clips into a single continuous video.

    Supports:
    - Single multi-shot generation via O3 (preferred for narrative reels)
    - Last-frame chaining (extract last frame of clip N, use as ref for clip N+1)
    - Crossfade transitions between clips
    - Audio continuity across clips
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path
        self.frame_extractor = FrameExtractor(ffmpeg_path=ffmpeg_path)
        self.audio_mixer = AudioMixer(ffmpeg_path=ffmpeg_path)

    async def generate_single_multishot(self, intent) -> "VideoGenerationResult":
        """Preferred over concatenate_clips() for narrative reels.

        Uses Kling O3's native multi-shot in a single API call.
        Better identity consistency, lower cost, no frame-chaining drift.

        Only falls back to chained generation if multi_prompt fails.

        Args:
            intent: VideoIntent with shot_list populated (1-6 shots)

        Returns:
            VideoGenerationResult from a single O3 generation
        """
        from ..brief.models import ContentFormat
        from .video_generator import VideoGenerator

        if not intent.shot_list or len(intent.shot_list) > 6:
            raise ValueError("multi-shot requires 1-6 shots")

        intent.content_format = ContentFormat.NARRATIVE_REEL
        intent.duration = sum(s.duration for s in intent.shot_list)

        generator = VideoGenerator()
        return await generator.generate_with_budget(intent)

    def get_chain_reference(self, previous_clip_path: str) -> str:
        """Extract the last frame from a clip to use as reference for the next.

        Returns path to the extracted frame image.
        """
        return self.frame_extractor.extract_last_frame(previous_clip_path)

    def concatenate_clips(
        self,
        clip_paths: list[str],
        output_path: str,
        transition: str = "crossfade",
        transition_duration: float = 0.5,
    ) -> str:
        """Concatenate multiple video clips with transitions.

        Args:
            clip_paths: Ordered list of clip file paths
            output_path: Where to write the final video
            transition: Transition type ("crossfade", "cut")
            transition_duration: Duration of transition in seconds

        Returns:
            Path to the output file
        """
        if not clip_paths:
            raise ValueError("No clips to concatenate")

        if len(clip_paths) == 1:
            # Single clip — just copy
            import shutil
            shutil.copy2(clip_paths[0], output_path)
            return output_path

        if transition == "cut":
            return self._concat_with_cut(clip_paths, output_path)
        elif transition == "crossfade":
            return self._concat_with_crossfade(
                clip_paths, output_path, transition_duration
            )
        else:
            logger.warning("Unknown transition '%s', falling back to cut", transition)
            return self._concat_with_cut(clip_paths, output_path)

    def _concat_with_cut(self, clip_paths: list[str], output_path: str) -> str:
        """Simple concatenation with hard cuts."""
        list_file = tempfile.mktemp(suffix=".txt")
        with open(list_file, "w") as f:
            for path in clip_paths:
                f.write(f"file '{path}'\n")

        cmd = [
            self.ffmpeg,
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
            "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        os.unlink(list_file)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concatenation failed: {result.stderr}")

        logger.info(
            "Concatenated %d clips with hard cuts: %s",
            len(clip_paths), output_path,
        )
        return output_path

    def _build_crossfade_filter(
        self, offset: float, duration: float, has_audio: bool
    ) -> str:
        """Build filter_complex string for video xfade + audio acrossfade."""
        if has_audio:
            return (
                f"[0:v][1:v]xfade=transition=fade:duration={duration}:offset={offset}[vout];"
                f"[0:a][1:a]acrossfade=d={duration}[aout]"
            )
        return f"[0:v][1:v]xfade=transition=fade:duration={duration}:offset={offset}[vout]"

    def _concat_with_crossfade(
        self,
        clip_paths: list[str],
        output_path: str,
        duration: float = 0.5,
    ) -> str:
        """Concatenate clips with crossfade transitions using xfade + acrossfade filters."""
        if len(clip_paths) == 2:
            # Compute offset: crossfade starts at (first_clip_duration - fade_duration)
            first_info = self.frame_extractor.get_video_info(clip_paths[0])
            offset = first_info["duration"] - duration
            has_audio = first_info.get("has_audio", False)

            filter_str = self._build_crossfade_filter(offset, duration, has_audio)
            map_args = ["-map", "[vout]"]
            if has_audio:
                map_args += ["-map", "[aout]"]

            cmd = [
                self.ffmpeg,
                "-i", clip_paths[0],
                "-i", clip_paths[1],
                "-filter_complex", filter_str,
                *map_args,
                "-c:v", "libx264",
                "-c:a", "aac",
                output_path,
                "-y",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning(
                    "Crossfade failed, falling back to cut: %s", result.stderr
                )
                return self._concat_with_cut(clip_paths, output_path)
            return output_path

        # Multi-clip: chain crossfades pairwise
        # First pass: crossfade clips 0+1 → temp
        # Second pass: crossfade temp+2 → temp2, etc.
        current = clip_paths[0]
        temp_files = []

        for i in range(1, len(clip_paths)):
            temp_out = tempfile.mktemp(suffix=".mp4", prefix=f"xfade_{i}_")
            temp_files.append(temp_out)

            # Compute offset from current clip's duration
            current_info = self.frame_extractor.get_video_info(current)
            offset = current_info["duration"] - duration
            has_audio = current_info.get("has_audio", False)

            filter_str = self._build_crossfade_filter(offset, duration, has_audio)
            map_args = ["-map", "[vout]"]
            if has_audio:
                map_args += ["-map", "[aout]"]

            cmd = [
                self.ffmpeg,
                "-i", current,
                "-i", clip_paths[i],
                "-filter_complex", filter_str,
                *map_args,
                "-c:v", "libx264",
                "-c:a", "aac",
                temp_out,
                "-y",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning(
                    "Crossfade step %d failed, falling back to cut", i
                )
                # Clean up temp files
                for tf in temp_files:
                    if os.path.exists(tf):
                        os.unlink(tf)
                return self._concat_with_cut(clip_paths, output_path)

            current = temp_out

        # Move final temp to output
        import shutil
        shutil.move(current, output_path)

        # Clean up intermediate temps
        for tf in temp_files[:-1]:  # Last one was moved
            if os.path.exists(tf):
                os.unlink(tf)

        logger.info(
            "Concatenated %d clips with crossfade (%.1fs): %s",
            len(clip_paths), duration, output_path,
        )
        return output_path

    def get_total_duration(self, clip_paths: list[str]) -> float:
        """Get total duration of all clips combined."""
        total = 0.0
        for path in clip_paths:
            info = self.frame_extractor.get_video_info(path)
            total += info.get("duration", 0)
        return total
