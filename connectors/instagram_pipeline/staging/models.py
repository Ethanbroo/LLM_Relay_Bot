"""Data models for staging and approval workflow."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime


class ReviewStatus(Enum):
    """Review status for staged posts."""
    PENDING_REVIEW = "pending_review"      # Awaiting human review
    APPROVED = "approved"                  # Approved for publishing
    REJECTED = "rejected"                  # Rejected, do not publish
    PUBLISHED = "published"                # Successfully published to Instagram
    FAILED = "failed"                      # Publishing attempt failed


@dataclass
class StagedPost:
    """
    Represents a post staged for review and publishing.

    This is the final stage before content goes live. Posts in this
    state await human approval (or auto-approval based on quality gates).
    """
    # Post identification
    post_id: str                           # Unique identifier (timestamp-based)
    staging_directory: str                 # Path to staged post directory

    # Review workflow
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    reviewed_by: Optional[str] = None      # Username/email of reviewer
    reviewed_at: Optional[str] = None      # ISO timestamp
    review_notes: Optional[str] = None     # Human reviewer notes

    # Publishing
    instagram_post_id: Optional[str] = None    # Instagram's media ID after publishing
    instagram_permalink: Optional[str] = None  # Public URL after publishing
    published_at: Optional[str] = None         # ISO timestamp

    # Metadata
    character_id: str = ""
    post_format: str = ""                  # "single_image" | "carousel" | "reel"
    brief_hash: str = ""
    intent_hash: str = ""

    # Quality gates summary
    all_quality_gates_passed: bool = False
    quality_gate_summary: dict = field(default_factory=dict)

    # Timestamps
    staged_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def approve(self, reviewer: str, notes: Optional[str] = None) -> None:
        """Mark post as approved."""
        self.review_status = ReviewStatus.APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = datetime.utcnow().isoformat()
        self.review_notes = notes

    def reject(self, reviewer: str, notes: str) -> None:
        """Mark post as rejected."""
        self.review_status = ReviewStatus.REJECTED
        self.reviewed_by = reviewer
        self.reviewed_at = datetime.utcnow().isoformat()
        self.review_notes = notes

    def mark_published(self, post_id: str, permalink: str) -> None:
        """Mark post as successfully published."""
        self.review_status = ReviewStatus.PUBLISHED
        self.instagram_post_id = post_id
        self.instagram_permalink = permalink
        self.published_at = datetime.utcnow().isoformat()

    def mark_failed(self, error_message: str) -> None:
        """Mark publishing attempt as failed."""
        self.review_status = ReviewStatus.FAILED
        self.review_notes = f"Publishing failed: {error_message}"

    @property
    def is_approved(self) -> bool:
        """Returns True if post is approved for publishing."""
        return self.review_status == ReviewStatus.APPROVED

    @property
    def is_published(self) -> bool:
        """Returns True if post has been published."""
        return self.review_status == ReviewStatus.PUBLISHED

    @property
    def can_publish(self) -> bool:
        """Returns True if post can be published (approved and not yet published)."""
        return self.review_status == ReviewStatus.APPROVED

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        import dataclasses
        data = dataclasses.asdict(self)
        # Convert enum to string
        data["review_status"] = self.review_status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "StagedPost":
        """Create from dict (reverse of to_dict)."""
        # Convert string back to enum
        if "review_status" in data and isinstance(data["review_status"], str):
            data["review_status"] = ReviewStatus(data["review_status"])
        return cls(**data)
