"""Data models for post assembly."""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from ..brief.models import ContentFormat


@dataclass
class AssembledPost:
    """
    Complete, ready-to-publish Instagram post.

    This is the final output of the pipeline before staging/approval.
    Contains all assets, metadata, and audit trail.
    """
    # Core content
    image_paths: list[str]                    # Processed image file paths
    caption: str                              # Final caption text
    hashtags: list[str]                       # List of hashtags (without #)

    # Post configuration
    post_format: str                          # "single_image" | "carousel" | "reel"
    platform_target: str                      # "instagram_feed" | "instagram_reels"

    # Metadata
    character_id: str
    brief_hash: str                           # Original brief hash
    intent_hash: str                          # PostIntent hash

    # Quality gate results
    all_images_passed: bool                   # True if all images passed quality gates
    quality_gate_summary: dict                # Summary of gate results

    # Video content
    content_format: ContentFormat = ContentFormat.STATIC_IMAGE
    video_path: Optional[str] = None          # Path to video file (if video content)
    audio_path: Optional[str] = None          # Path to audio file (if separate audio)
    has_video: bool = False                   # True if this post contains video
    duration_seconds: Optional[float] = None  # Video duration in seconds

    # Cost tracking
    total_cost_usd: float = 0.0               # Total generation + gating cost

    # Scheduling
    scheduled_post_time: Optional[str] = None  # ISO datetime or None for ASAP

    # Timestamps
    assembled_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Output paths
    output_directory: Optional[str] = None    # Staging directory path

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        import dataclasses
        return dataclasses.asdict(self)

    @property
    def is_carousel(self) -> bool:
        """Returns True if this is a carousel post."""
        return self.post_format == "carousel"

    @property
    def is_reel(self) -> bool:
        """Returns True if this is a reel."""
        return self.post_format == "reel"

    @property
    def image_count(self) -> int:
        """Number of images in this post."""
        return len(self.image_paths)

    @property
    def caption_with_hashtags(self) -> str:
        """
        Returns caption with hashtags appended.

        Instagram best practice: Add hashtags at end of caption
        separated by line breaks for readability.
        """
        if not self.hashtags:
            return self.caption

        hashtag_str = " ".join(f"#{tag}" for tag in self.hashtags)
        return f"{self.caption}\n\n{hashtag_str}"
