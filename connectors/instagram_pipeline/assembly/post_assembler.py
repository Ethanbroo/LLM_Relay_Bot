"""Post assembler - final stage before staging.

Takes quality-gated images, processed assets, and metadata
and assembles them into a complete AssembledPost ready for review/publishing.
"""

import logging
import json
from pathlib import Path
from typing import Optional

from .models import AssembledPost
from ..brief.models import ContentFormat
from ..quality.models import AggregatedGateResult

logger = logging.getLogger(__name__)


class PostAssembler:
    """
    Assembles complete Instagram posts from processed assets.

    This is the final assembly step - all assets have been generated,
    quality-gated, and post-processed. We now bundle everything together
    with metadata for staging and review.
    """

    def assemble(
        self,
        image_paths: list[str],
        caption: str,
        hashtags: list[str],
        post_format: str,
        platform_target: str,
        character_id: str,
        brief_hash: str,
        intent_hash: str,
        gate_results: list[AggregatedGateResult],
        total_cost_usd: float,
        scheduled_post_time: Optional[str] = None,
        content_format: ContentFormat = ContentFormat.STATIC_IMAGE,
        video_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> AssembledPost:
        """
        Assemble a complete post from components.

        Args:
            image_paths: List of processed image file paths
            caption: Final caption text
            hashtags: List of hashtags (without # prefix)
            post_format: "single_image" | "carousel" | "reel"
            platform_target: "instagram_feed" | "instagram_reels"
            character_id: Character identifier
            brief_hash: Original content brief hash
            intent_hash: Post intent hash
            gate_results: Quality gate results for all images
            total_cost_usd: Total cost (generation + gating)
            scheduled_post_time: Optional scheduled time (ISO format)

        Returns:
            AssembledPost ready for staging
        """
        logger.info(
            "Assembling %s post: %d images, %d hashtags",
            post_format,
            len(image_paths),
            len(hashtags)
        )

        # Validate all images passed quality gates
        all_passed = all(result.passed for result in gate_results)

        if not all_passed:
            failed_count = sum(1 for r in gate_results if r.failed)
            marginal_count = sum(1 for r in gate_results if r.needs_review)

            logger.warning(
                "Not all images passed quality gates: %d failed, %d marginal",
                failed_count,
                marginal_count
            )

        # Build quality gate summary
        quality_summary = {
            "total_images": len(gate_results),
            "passed": sum(1 for r in gate_results if r.passed),
            "failed": sum(1 for r in gate_results if r.failed),
            "marginal": sum(1 for r in gate_results if r.needs_review),
            "total_gate_cost_usd": sum(r.total_cost_usd for r in gate_results),
            "avg_gate_time_s": sum(r.total_time_s for r in gate_results) / len(gate_results),
            "results": [
                {
                    "image_index": i,
                    "overall_decision": r.overall_decision.value,
                    "tier_decisions": [
                        {
                            "tier": tr.tier.value,
                            "decision": tr.decision.value,
                            "score": tr.score,
                            "reason": tr.reason
                        }
                        for tr in r.tier_results
                    ]
                }
                for i, r in enumerate(gate_results)
            ]
        }

        # Determine if this is video content
        has_video = video_path is not None and content_format != ContentFormat.STATIC_IMAGE

        # Create assembled post
        post = AssembledPost(
            image_paths=image_paths,
            caption=caption,
            hashtags=hashtags,
            post_format=post_format,
            platform_target=platform_target,
            character_id=character_id,
            brief_hash=brief_hash,
            intent_hash=intent_hash,
            all_images_passed=all_passed,
            quality_gate_summary=quality_summary,
            content_format=content_format,
            video_path=video_path,
            audio_path=audio_path,
            has_video=has_video,
            duration_seconds=duration_seconds,
            total_cost_usd=total_cost_usd,
            scheduled_post_time=scheduled_post_time,
        )

        media_desc = f"video ({duration_seconds:.1f}s)" if has_video else f"{post.image_count} images"
        logger.info(
            "Post assembled: %s, caption=%d chars, cost=$%.3f",
            media_desc,
            len(caption),
            total_cost_usd
        )

        return post

    def save_to_directory(
        self,
        post: AssembledPost,
        output_dir: str
    ) -> str:
        """
        Save assembled post to staging directory.

        Creates directory structure:
        output_dir/
          ├── image_001.jpg
          ├── image_002.jpg (if carousel)
          ├── caption.txt
          ├── hashtags.txt
          ├── post_metadata.json
          └── quality_gates.json

        Args:
            post: Assembled post
            output_dir: Directory to save to

        Returns:
            Path to output directory
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        logger.info("Saving assembled post to %s", output_dir)

        # Copy images to output directory with sequential naming
        import shutil
        saved_image_paths = []

        for i, image_path in enumerate(post.image_paths, 1):
            ext = Path(image_path).suffix
            dest_path = output_path / f"image_{i:03d}{ext}"
            shutil.copy2(image_path, dest_path)
            saved_image_paths.append(str(dest_path))
            logger.debug("Copied image %d: %s", i, dest_path)

        # Save caption
        caption_path = output_path / "caption.txt"
        caption_path.write_text(post.caption, encoding="utf-8")

        # Save hashtags
        hashtags_path = output_path / "hashtags.txt"
        hashtags_path.write_text("\n".join(post.hashtags), encoding="utf-8")

        # Save caption with hashtags (Instagram format)
        full_caption_path = output_path / "caption_with_hashtags.txt"
        full_caption_path.write_text(post.caption_with_hashtags, encoding="utf-8")

        # Copy video file if present
        if post.has_video and post.video_path:
            video_ext = Path(post.video_path).suffix
            dest_video = output_path / f"video{video_ext}"
            shutil.copy2(post.video_path, dest_video)
            logger.debug("Copied video: %s", dest_video)

        # Copy audio file if present
        if post.audio_path:
            audio_ext = Path(post.audio_path).suffix
            dest_audio = output_path / f"audio{audio_ext}"
            shutil.copy2(post.audio_path, dest_audio)
            logger.debug("Copied audio: %s", dest_audio)

        # Save metadata
        metadata = {
            "post_format": post.post_format,
            "platform_target": post.platform_target,
            "character_id": post.character_id,
            "brief_hash": post.brief_hash,
            "intent_hash": post.intent_hash,
            "image_count": post.image_count,
            "content_format": post.content_format.value if hasattr(post.content_format, 'value') else str(post.content_format),
            "has_video": post.has_video,
            "duration_seconds": post.duration_seconds,
            "all_images_passed": post.all_images_passed,
            "total_cost_usd": post.total_cost_usd,
            "scheduled_post_time": post.scheduled_post_time,
            "assembled_at": post.assembled_at,
        }
        metadata_path = output_path / "post_metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        # Save quality gate results
        quality_path = output_path / "quality_gates.json"
        with open(quality_path, "w") as f:
            json.dump(post.quality_gate_summary, f, indent=2)

        # Update post with output directory
        post.output_directory = str(output_path)

        logger.info(
            "Post saved to %s: %d files created",
            output_dir,
            len(list(output_path.iterdir()))
        )

        return str(output_path)

    def load_from_directory(self, directory: str) -> AssembledPost:
        """
        Load assembled post from staging directory.

        Reverses save_to_directory() - reconstructs AssembledPost
        from saved files.

        Args:
            directory: Path to staged post directory

        Returns:
            AssembledPost

        Raises:
            FileNotFoundError: If required files missing
            ValueError: If metadata is invalid
        """
        dir_path = Path(directory)

        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        logger.info("Loading assembled post from %s", directory)

        # Load metadata
        metadata_path = dir_path / "post_metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Missing post_metadata.json in {directory}")

        with open(metadata_path) as f:
            metadata = json.load(f)

        # Load caption
        caption_path = dir_path / "caption.txt"
        caption = caption_path.read_text(encoding="utf-8")

        # Load hashtags
        hashtags_path = dir_path / "hashtags.txt"
        hashtags = hashtags_path.read_text(encoding="utf-8").strip().split("\n")

        # Load quality gates
        quality_path = dir_path / "quality_gates.json"
        with open(quality_path) as f:
            quality_summary = json.load(f)

        # Find image files
        image_paths = sorted([
            str(p) for p in dir_path.glob("image_*.jpg")
        ] + [
            str(p) for p in dir_path.glob("image_*.jpeg")
        ] + [
            str(p) for p in dir_path.glob("image_*.png")
        ])

        # Reconstruct AssembledPost
        post = AssembledPost(
            image_paths=image_paths,
            caption=caption,
            hashtags=hashtags,
            post_format=metadata["post_format"],
            platform_target=metadata["platform_target"],
            character_id=metadata["character_id"],
            brief_hash=metadata["brief_hash"],
            intent_hash=metadata["intent_hash"],
            all_images_passed=metadata["all_images_passed"],
            quality_gate_summary=quality_summary,
            total_cost_usd=metadata["total_cost_usd"],
            scheduled_post_time=metadata.get("scheduled_post_time"),
            assembled_at=metadata["assembled_at"],
            output_directory=str(dir_path),
        )

        logger.info("Post loaded: %d images", post.image_count)

        return post
