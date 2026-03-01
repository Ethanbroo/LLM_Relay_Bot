"""SQLite-backed approval store for still image validation.

Tracks the approval status of generated images through the multi-tier
validation gate. Nothing gets animated unless status='approved'.

Pattern follows connectors/idempotency_store.py: sqlite3 + threading.Lock.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from ..utils.hashing import sha256_file


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    ANIMATED = "animated"


@dataclass
class ApprovalRecord:
    """A tracked image with its validation and approval state."""

    image_hash: str                     # SHA-256 of image file (primary key)
    image_path: str
    character_id: str
    scene: str                          # Scene type (cafe, pool, bed, etc.)
    shot_hash: Optional[str]            # From ShotSpec.shot_hash
    status: ApprovalStatus
    identity_score: Optional[float]     # Cosine similarity vs reference
    clip_score: Optional[float]         # CLIP alignment score
    gate_results_json: str              # Full serialized gate results
    attempt_number: int                 # Which generation attempt (1-based)
    seed: Optional[int]                 # Seed used for generation
    prompt: str                         # Prompt used
    created_at: str
    approved_by: Optional[str] = None   # "auto" or "human:{name}"
    rejection_reason: Optional[str] = None
    video_path: Optional[str] = None    # Set after animation


class ApprovalStore:
    """SQLite-backed persistent approval store.

    Schema follows the idempotency_store.py pattern:
    - Thread-safe with threading.Lock
    - Auto-creates table on init
    - Parameterized queries (no SQL injection)
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS approvals (
        image_hash      TEXT PRIMARY KEY,
        image_path      TEXT NOT NULL,
        character_id    TEXT NOT NULL,
        scene           TEXT NOT NULL DEFAULT '',
        shot_hash       TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        identity_score  REAL,
        clip_score      REAL,
        gate_results    TEXT NOT NULL DEFAULT '{}',
        attempt_number  INTEGER NOT NULL DEFAULT 1,
        seed            INTEGER,
        prompt          TEXT NOT NULL DEFAULT '',
        approved_by     TEXT,
        rejection_reason TEXT,
        video_path      TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_approvals_character
        ON approvals(character_id);
    CREATE INDEX IF NOT EXISTS idx_approvals_status
        ON approvals(status);
    CREATE INDEX IF NOT EXISTS idx_approvals_shot
        ON approvals(shot_hash);
    CREATE INDEX IF NOT EXISTS idx_approvals_scene
        ON approvals(scene);
    """

    def __init__(self, db_path: str | Path = "data/approvals.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self,
        image_path: str,
        character_id: str,
        scene: str,
        gate_results: dict,
        attempt_number: int,
        prompt: str = "",
        seed: Optional[int] = None,
        shot_hash: Optional[str] = None,
        identity_score: Optional[float] = None,
        clip_score: Optional[float] = None,
    ) -> str:
        """Record a generated image with its validation results.

        Returns:
            image_hash (primary key)
        """
        image_hash = sha256_file(image_path)
        gate_json = json.dumps(gate_results, default=str)

        # Determine initial status from gate results
        overall = gate_results.get("overall_decision", "pending")
        if overall == "pass":
            status = ApprovalStatus.APPROVED.value
        elif overall == "marginal":
            status = ApprovalStatus.NEEDS_REVIEW.value
        elif overall == "fail":
            status = ApprovalStatus.REJECTED.value
        else:
            status = ApprovalStatus.PENDING.value

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO approvals
                       (image_hash, image_path, character_id, scene,
                        shot_hash, status, identity_score, clip_score,
                        gate_results, attempt_number, seed, prompt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        image_hash, image_path, character_id, scene,
                        shot_hash, status, identity_score, clip_score,
                        gate_json, attempt_number, seed, prompt,
                    ),
                )

        return image_hash

    def approve(
        self,
        image_hash: str,
        approved_by: str = "auto",
    ) -> None:
        """Mark an image as approved for animation."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE approvals SET status = ?, approved_by = ? WHERE image_hash = ?",
                    (ApprovalStatus.APPROVED.value, approved_by, image_hash),
                )

    def reject(
        self,
        image_hash: str,
        reason: str = "",
    ) -> None:
        """Mark an image as rejected."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE approvals SET status = ?, rejection_reason = ? WHERE image_hash = ?",
                    (ApprovalStatus.REJECTED.value, reason, image_hash),
                )

    def mark_animated(
        self,
        image_hash: str,
        video_path: str,
    ) -> None:
        """Update status after successful animation."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE approvals SET status = ?, video_path = ? WHERE image_hash = ?",
                    (ApprovalStatus.ANIMATED.value, video_path, image_hash),
                )

    def get_status(self, image_hash: str) -> Optional[ApprovalRecord]:
        """Get full record for an image."""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM approvals WHERE image_hash = ?",
                    (image_hash,),
                ).fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)

    def get_approved_for_scene(
        self,
        character_id: str,
        scene: str,
    ) -> list[ApprovalRecord]:
        """Get approved images for a specific scene."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE character_id = ? AND scene = ? AND status = ?",
                    (character_id, scene, ApprovalStatus.APPROVED.value),
                ).fetchall()
                return [self._row_to_record(r) for r in rows]

    def get_approved_for_animation(
        self,
        character_id: str,
        limit: int = 10,
    ) -> list[ApprovalRecord]:
        """Get approved images that haven't been animated yet."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE character_id = ? AND status = ? "
                    "ORDER BY created_at ASC LIMIT ?",
                    (character_id, ApprovalStatus.APPROVED.value, limit),
                ).fetchall()
                return [self._row_to_record(r) for r in rows]

    def count_by_status(self, character_id: str) -> dict[str, int]:
        """Get count of images per status for a character."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM approvals "
                    "WHERE character_id = ? GROUP BY status",
                    (character_id,),
                ).fetchall()
                return {row["status"]: row["cnt"] for row in rows}

    def get_history(
        self,
        character_id: str,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        """Get recent approval history for a character."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE character_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (character_id, limit),
                ).fetchall()
                return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            image_hash=row["image_hash"],
            image_path=row["image_path"],
            character_id=row["character_id"],
            scene=row["scene"],
            shot_hash=row["shot_hash"],
            status=ApprovalStatus(row["status"]),
            identity_score=row["identity_score"],
            clip_score=row["clip_score"],
            gate_results_json=row["gate_results"],
            attempt_number=row["attempt_number"],
            seed=row["seed"],
            prompt=row["prompt"],
            created_at=row["created_at"],
            approved_by=row["approved_by"],
            rejection_reason=row["rejection_reason"],
            video_path=row["video_path"],
        )
