# audio/audio_mixer.py

import subprocess
import tempfile
from pathlib import Path


class AudioMixer:
    """FFmpeg-based audio operations for video post-processing.

    Used when:
    - Adding ElevenLabs TTS to a silent video
    - Mixing background music under dialogue
    - Extracting audio from gameplay clips
    - Normalizing audio levels across clips
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    def add_audio_to_video(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        video_volume: float = 0.3,   # Reduce existing video audio
        audio_volume: float = 1.0,    # TTS/music volume
    ) -> str:
        """Overlay audio track onto video. If video has audio, mix both."""
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex",
            f"[0:a]volume={video_volume}[va];"
            f"[1:a]volume={audio_volume}[aa];"
            f"[va][aa]amix=inputs=2:duration=shortest[out]",
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path,
            "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def replace_audio(self, video_path: str, audio_path: str, output_path: str) -> str:
        """Replace video's audio entirely with new audio track."""
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path,
            "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def extract_audio(self, video_path: str, output_path: str = None) -> str:
        """Extract audio track from video."""
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".m4a")

        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-vn",
            "-acodec", "aac",
            "-q:a", "2",
            output_path,
            "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def normalize_audio(self, audio_path: str, output_path: str, target_lufs: float = -14) -> str:
        """Normalize audio to target loudness (Instagram standard is ~-14 LUFS)."""
        cmd = [
            self.ffmpeg,
            "-i", audio_path,
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            output_path,
            "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    def concatenate_audio(self, audio_paths: list[str], output_path: str) -> str:
        """Concatenate multiple audio files sequentially."""
        list_file = tempfile.mktemp(suffix=".txt")
        with open(list_file, "w") as f:
            for path in audio_paths:
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
        subprocess.run(cmd, capture_output=True, check=True)
        Path(list_file).unlink()
        return output_path
