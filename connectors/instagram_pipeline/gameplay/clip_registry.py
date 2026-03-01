# gameplay/clip_registry.py

import json
import hashlib
import shutil
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


GAMEPLAY_STORE = Path("data/gameplay_clips")
REGISTRY_FILE = GAMEPLAY_STORE / "clip_registry.json"


class GameplayClip(BaseModel):
    """Registered gameplay clip with extracted metadata."""

    clip_id: str                          # SHA256 hash of file (dedup key)
    original_filename: str
    game_title: str
    stored_path: str                      # Path in gameplay store
    duration_seconds: float
    resolution: str                       # e.g. "1920x1080"
    fps: float
    has_audio: bool
    scene_count: int = 0                  # Number of detected scene changes
    highlight_timestamps: list[float] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    ingested_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    times_used: int = 0


class ClipRegistry:
    """Manages gameplay clip storage and metadata.

    Storage structure:
        data/gameplay_clips/
        ├── clip_registry.json        # Metadata index
        ├── abc123def/                 # clip_id directory
        │   ├── source.mp4            # Original clip
        │   ├── thumbnail.png         # First frame
        │   └── highlights/           # Extracted highlight segments
        │       ├── highlight_001.mp4
        │       └── highlight_002.mp4
        └── fed321cba/
            └── ...
    """

    def __init__(self):
        GAMEPLAY_STORE.mkdir(parents=True, exist_ok=True)
        self.clips: dict[str, GameplayClip] = self._load_registry()

    def _load_registry(self) -> dict:
        if REGISTRY_FILE.exists():
            data = json.loads(REGISTRY_FILE.read_text())
            return {k: GameplayClip(**v) for k, v in data.items()}
        return {}

    def _save_registry(self):
        data = {k: v.dict() for k, v in self.clips.items()}
        REGISTRY_FILE.write_text(json.dumps(data, indent=2))

    def ingest(self, source_path: str, game_title: str, tags: list[str] = None) -> GameplayClip:
        """Ingest a new gameplay clip. Copies to store, extracts metadata."""
        from ..quality.frame_extractor import FrameExtractor

        extractor = FrameExtractor()

        # Generate clip ID from file hash
        file_hash = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()[:12]

        if file_hash in self.clips:
            return self.clips[file_hash]  # Already ingested

        # Create storage directory
        clip_dir = GAMEPLAY_STORE / file_hash
        clip_dir.mkdir(exist_ok=True)

        # Copy source file
        stored_path = clip_dir / "source.mp4"
        shutil.copy2(source_path, stored_path)

        # Extract metadata
        info = extractor.get_video_info(str(stored_path))

        # Extract thumbnail
        extractor.extract_first_frame(str(stored_path), str(clip_dir / "thumbnail.png"))

        # Detect scene changes for highlight extraction
        scene_timestamps = self._detect_scenes(str(stored_path))

        clip = GameplayClip(
            clip_id=file_hash,
            original_filename=Path(source_path).name,
            game_title=game_title,
            stored_path=str(stored_path),
            duration_seconds=info["duration"],
            resolution=f"{info['width']}x{info['height']}",
            fps=info["fps"],
            has_audio=info["has_audio"],
            scene_count=len(scene_timestamps),
            highlight_timestamps=scene_timestamps,
            tags=tags or [],
        )

        self.clips[file_hash] = clip
        self._save_registry()
        return clip

    def _detect_scenes(self, video_path: str, threshold: float = 0.3) -> list[float]:
        """Detect scene changes using FFmpeg's scene detection filter.
        Returns list of timestamps (seconds) where scenes change."""
        import subprocess

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-vsync", "vfq",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        timestamps = []
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    pts = float(line.split("pts_time:")[1].split()[0])
                    timestamps.append(round(pts, 2))
                except (IndexError, ValueError):
                    continue
        return timestamps

    def get_clip(self, clip_id: str) -> Optional[GameplayClip]:
        return self.clips.get(clip_id)

    def list_clips(self, game_title: str = None) -> list[GameplayClip]:
        clips = list(self.clips.values())
        if game_title:
            clips = [c for c in clips if c.game_title.lower() == game_title.lower()]
        return sorted(clips, key=lambda c: c.ingested_at, reverse=True)

    def mark_used(self, clip_id: str):
        if clip_id in self.clips:
            self.clips[clip_id].times_used += 1
            self._save_registry()
