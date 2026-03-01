"""Post stager - prepares posts for review and publishing."""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from .models import StagedPost, ReviewStatus
from ..assembly.models import AssembledPost

logger = logging.getLogger(__name__)


class PostStager:
    """
    Manages post staging for review workflow.

    Posts are staged to a directory structure where they await approval.
    Once approved, they can be published to Instagram.
    """

    def __init__(self, staging_root: str = "output/instagram/staged"):
        """
        Initialize post stager.

        Args:
            staging_root: Root directory for staged posts
        """
        self.staging_root = Path(staging_root)
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def stage(
        self,
        assembled_post: AssembledPost,
        auto_approve: bool = False,
    ) -> StagedPost:
        """
        Stage an assembled post for review.

        Args:
            assembled_post: Complete assembled post
            auto_approve: If True, automatically approve (skip human review)
                         Typically enabled when all quality gates passed with high scores

        Returns:
            StagedPost with staging metadata
        """
        # Generate unique post ID (timestamp-based)
        post_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")[:19]  # YYYYMMDD_HHMMSS_fff

        logger.info(
            "Staging post %s: %s, %d images, auto_approve=%s",
            post_id,
            assembled_post.post_format,
            assembled_post.image_count,
            auto_approve
        )

        # Use existing output directory or create new one
        if assembled_post.output_directory:
            staging_dir = Path(assembled_post.output_directory)
        else:
            staging_dir = self.staging_root / post_id
            staging_dir.mkdir(parents=True, exist_ok=True)

        # Create StagedPost
        staged_post = StagedPost(
            post_id=post_id,
            staging_directory=str(staging_dir),
            character_id=assembled_post.character_id,
            post_format=assembled_post.post_format,
            brief_hash=assembled_post.brief_hash,
            intent_hash=assembled_post.intent_hash,
            all_quality_gates_passed=assembled_post.all_images_passed,
            quality_gate_summary=assembled_post.quality_gate_summary,
        )

        # Auto-approve if requested
        if auto_approve:
            staged_post.approve(
                reviewer="system_auto_approval",
                notes="Auto-approved: all quality gates passed"
            )
            logger.info("Post %s auto-approved", post_id)

        # Save staging metadata
        self._save_staging_metadata(staged_post)

        # Create review status file
        self._write_review_status(staged_post)

        logger.info(
            "Post %s staged at %s (status: %s)",
            post_id,
            staging_dir,
            staged_post.review_status.value
        )

        return staged_post

    def load(self, post_id: str) -> StagedPost:
        """
        Load staged post by ID.

        Args:
            post_id: Post identifier

        Returns:
            StagedPost

        Raises:
            FileNotFoundError: If post not found
        """
        staging_dir = self.staging_root / post_id
        metadata_path = staging_dir / "staging_metadata.json"

        if not metadata_path.exists():
            raise FileNotFoundError(f"Staged post not found: {post_id}")

        with open(metadata_path) as f:
            data = json.load(f)

        return StagedPost.from_dict(data)

    def list_pending(self) -> list[StagedPost]:
        """
        List all posts pending review.

        Returns:
            List of StagedPost objects with status PENDING_REVIEW
        """
        pending = []

        for post_dir in self.staging_root.iterdir():
            if not post_dir.is_dir():
                continue

            try:
                staged_post = self.load(post_dir.name)
                if staged_post.review_status == ReviewStatus.PENDING_REVIEW:
                    pending.append(staged_post)
            except Exception as e:
                logger.warning("Failed to load staged post %s: %s", post_dir.name, e)

        return sorted(pending, key=lambda p: p.staged_at)

    def list_approved(self) -> list[StagedPost]:
        """
        List all approved posts ready for publishing.

        Returns:
            List of StagedPost objects with status APPROVED
        """
        approved = []

        for post_dir in self.staging_root.iterdir():
            if not post_dir.is_dir():
                continue

            try:
                staged_post = self.load(post_dir.name)
                if staged_post.review_status == ReviewStatus.APPROVED:
                    approved.append(staged_post)
            except Exception as e:
                logger.warning("Failed to load staged post %s: %s", post_dir.name, e)

        return sorted(approved, key=lambda p: p.reviewed_at or p.staged_at)

    def approve(
        self,
        post_id: str,
        reviewer: str,
        notes: Optional[str] = None
    ) -> StagedPost:
        """
        Approve a staged post.

        Args:
            post_id: Post identifier
            reviewer: Username/email of reviewer
            notes: Optional review notes

        Returns:
            Updated StagedPost
        """
        staged_post = self.load(post_id)
        staged_post.approve(reviewer, notes)

        self._save_staging_metadata(staged_post)
        self._write_review_status(staged_post)

        logger.info("Post %s approved by %s", post_id, reviewer)

        return staged_post

    def reject(
        self,
        post_id: str,
        reviewer: str,
        notes: str
    ) -> StagedPost:
        """
        Reject a staged post.

        Args:
            post_id: Post identifier
            reviewer: Username/email of reviewer
            notes: Rejection reason (required)

        Returns:
            Updated StagedPost
        """
        staged_post = self.load(post_id)
        staged_post.reject(reviewer, notes)

        self._save_staging_metadata(staged_post)
        self._write_review_status(staged_post)

        logger.info("Post %s rejected by %s: %s", post_id, reviewer, notes)

        return staged_post

    def mark_published(
        self,
        post_id: str,
        instagram_post_id: str,
        permalink: str
    ) -> StagedPost:
        """
        Mark post as successfully published.

        Args:
            post_id: Post identifier
            instagram_post_id: Instagram's media ID
            permalink: Public URL to the post

        Returns:
            Updated StagedPost
        """
        staged_post = self.load(post_id)
        staged_post.mark_published(instagram_post_id, permalink)

        self._save_staging_metadata(staged_post)
        self._write_review_status(staged_post)

        logger.info(
            "Post %s marked as published: %s",
            post_id,
            permalink
        )

        return staged_post

    def _save_staging_metadata(self, staged_post: StagedPost) -> None:
        """Save staging metadata to JSON file."""
        staging_dir = Path(staged_post.staging_directory)
        metadata_path = staging_dir / "staging_metadata.json"

        with open(metadata_path, "w") as f:
            json.dump(staged_post.to_dict(), f, indent=2)

    def _write_review_status(self, staged_post: StagedPost) -> None:
        """
        Write human-readable review status file.

        This file is watched by automation tools for status changes.
        """
        staging_dir = Path(staged_post.staging_directory)
        status_path = staging_dir / "review_status.txt"

        status_text = f"""Post ID: {staged_post.post_id}
Status: {staged_post.review_status.value.upper()}
Staged At: {staged_post.staged_at}

Character: {staged_post.character_id}
Format: {staged_post.post_format}
Quality Gates Passed: {staged_post.all_quality_gates_passed}

"""

        if staged_post.reviewed_by:
            status_text += f"""Reviewed By: {staged_post.reviewed_by}
Reviewed At: {staged_post.reviewed_at}
"""

        if staged_post.review_notes:
            status_text += f"""Notes: {staged_post.review_notes}
"""

        if staged_post.is_published:
            status_text += f"""
Published At: {staged_post.published_at}
Instagram Post ID: {staged_post.instagram_post_id}
Permalink: {staged_post.instagram_permalink}
"""

        status_path.write_text(status_text)
