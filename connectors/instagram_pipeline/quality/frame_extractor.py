# quality/frame_extractor.py

import subprocess
import tempfile
import os
from pathlib import Path
from typing import Optional


class FrameExtractor:
    """Extracts frames from video using FFmpeg.

    Used by:
    - TemporalConsistencyGate: sample every N frames for identity check
    - ClipSequencer: extract last frame for chaining
    - GameplayClipAnalyzer: extract keyframes for scene detection
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path
        self._verify_ffmpeg()

    def _verify_ffmpeg(self):
        try:
            subprocess.run([self.ffmpeg, "-version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise RuntimeError("FFmpeg not found. Install via: brew install ffmpeg")

    def extract_at_intervals(
        self,
        video_path: str,
        interval_frames: int = 30,
        output_dir: Optional[str] = None,
        format: str = "png",
    ) -> list[str]:
        """Extract one frame every N frames. Returns list of file paths.

        For a 30fps video with interval_frames=30, this gives ~1 frame/second.
        For a 10-second clip, that's ~10 frames to verify identity against.
        """
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="frames_")

        output_pattern = os.path.join(output_dir, f"frame_%04d.{format}")

        cmd = [
            self.ffmpeg, "-i", video_path,
            "-vf", f"select='not(mod(n,{interval_frames}))'",
            "-fps_mode", "vfr",
            "-q:v", "2",  # High quality
            output_pattern,
            "-y",  # Overwrite
        ]
        subprocess.run(cmd, capture_output=True, check=True)

        frames = sorted(Path(output_dir).glob(f"frame_*.{format}"))
        return [str(f) for f in frames]

    def extract_last_frame(self, video_path: str, output_path: Optional[str] = None) -> str:
        """Extract the final frame of a video. Used for clip chaining."""
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".png", prefix="last_frame_")

        cmd = [
            self.ffmpeg, "-sseof", "-0.1",  # Seek to 0.1s before end
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
            "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def extract_first_frame(self, video_path: str, output_path: Optional[str] = None) -> str:
        """Extract the first frame. Used for thumbnail generation."""
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".png", prefix="first_frame_")

        cmd = [
            self.ffmpeg, "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
            "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def get_video_info(self, video_path: str) -> dict:
        """Get video metadata: duration, fps, resolution."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        import json
        data = json.loads(result.stdout)

        video_stream = next(
            (s for s in data.get("streams", []) if s["codec_type"] == "video"),
            {}
        )
        return {
            "duration": float(data.get("format", {}).get("duration", 0)),
            "fps": eval(video_stream.get("r_frame_rate", "30/1")),
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "codec": video_stream.get("codec_name", ""),
            "has_audio": any(s["codec_type"] == "audio" for s in data.get("streams", [])),
        }

    def cleanup_frames(self, frame_paths: list[str]):
        """Remove extracted frame files and their parent temp directory."""
        for path in frame_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        # Remove temp dir if empty
        if frame_paths:
            parent = os.path.dirname(frame_paths[0])
            if parent.startswith(tempfile.gettempdir()):
                try:
                    os.rmdir(parent)
                except OSError:
                    pass
