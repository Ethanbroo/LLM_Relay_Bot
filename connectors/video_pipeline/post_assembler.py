"""Video post assembler.

Assembles a complete video post (video file + caption + hashtags + metadata)
ready for publishing. Same interface pattern as the existing image post
assembly in the Instagram pipeline, but for video content.

Usage:
    post = assemble_video_post(
        video_path=Path("output/video/reel.mp4"),
        caption="Check out this vibe",
        hashtags=["#reels", "#aesthetic"],
        platform="instagram_reel",
    )
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def assemble_video_post(
    video_path: Path,
    caption: str,
    hashtags: list[str],
    platform: str,
    thumbnail_path: Optional[Path] = None,
    scheduled_time: Optional[str] = None,
) -> dict:
    """Assemble a complete video post (video + metadata).

    Args:
        video_path: Path to the rendered video file
        caption: Post caption text
        hashtags: List of hashtag strings (with or without # prefix)
        platform: Target platform identifier
        thumbnail_path: Optional custom thumbnail image
        scheduled_time: Optional ISO 8601 timestamp for scheduling

    Returns:
        Dict with all post data ready for publishing:
            type: "video"
            video_path: str
            caption: str
            hashtags: list[str]
            platform: str
            thumbnail_path: str | None
            scheduled_time: str | None
            metadata: dict (file_size_mb, etc.)
    """
    video_path = Path(video_path)

    # Normalize hashtags (ensure # prefix)
    normalized_tags = []
    for tag in hashtags:
        tag = tag.strip()
        if tag and not tag.startswith("#"):
            tag = f"#{tag}"
        if tag:
            normalized_tags.append(tag)

    # Gather file metadata
    metadata = {}
    if video_path.exists():
        file_size = video_path.stat().st_size
        metadata["file_size_bytes"] = file_size
        metadata["file_size_mb"] = round(file_size / (1024 * 1024), 2)
        metadata["file_extension"] = video_path.suffix.lstrip(".")
    else:
        logger.warning("Video file does not exist: %s", video_path)
        metadata["file_size_bytes"] = 0
        metadata["file_size_mb"] = 0.0

    return {
        "type": "video",
        "video_path": str(video_path),
        "caption": caption,
        "hashtags": normalized_tags,
        "platform": platform,
        "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
        "scheduled_time": scheduled_time,
        "metadata": metadata,
    }


def assemble_multi_platform_posts(
    video_paths: dict[str, Path],
    caption: str,
    hashtags: list[str],
    platform_captions: Optional[dict[str, str]] = None,
    platform_hashtags: Optional[dict[str, list[str]]] = None,
) -> list[dict]:
    """Assemble posts for multiple platforms from multi-platform render output.

    Args:
        video_paths: Dict mapping platform -> video path (from render_multi_platform)
        caption: Default caption for all platforms
        hashtags: Default hashtags for all platforms
        platform_captions: Optional per-platform caption overrides
        platform_hashtags: Optional per-platform hashtag overrides

    Returns:
        List of assembled post dicts, one per platform
    """
    platform_captions = platform_captions or {}
    platform_hashtags = platform_hashtags or {}

    posts = []
    for platform, video_path in video_paths.items():
        post = assemble_video_post(
            video_path=video_path,
            caption=platform_captions.get(platform, caption),
            hashtags=platform_hashtags.get(platform, hashtags),
            platform=platform,
        )
        posts.append(post)

    return posts
