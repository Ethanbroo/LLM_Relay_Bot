"""
Segment rotation and manifest management for Phase 3 audit logging.

Responsibilities:
- Monitor segment file size
- Rotate to new segment when max_segment_bytes exceeded
- Maintain manifest.json with segment metadata
- Atomic manifest updates (temp file + fsync + rename)
- Track first/last event hashes per segment
"""

import json
import os
from pathlib import Path
from typing import Optional


class SegmentRotationManager:
    """
    Manages segment rotation and manifest updates.

    Attributes:
        log_directory: Path to log directory
        max_segment_bytes: Maximum bytes per segment before rotation
        current_segment_number: Current segment number (1-indexed)
        current_segment_path: Path to current segment file
        segments_metadata: List of segment metadata dicts
        manifest_path: Path to manifest.json
    """

    def __init__(
        self,
        log_directory: str | Path,
        max_segment_bytes: int = 10485760,  # 10MB default
        run_id: str = "",
        config_hash: str = "",
        time_policy: str = "frozen",
        public_key_fingerprint: str = "",
    ):
        """
        Initialize SegmentRotationManager.

        Args:
            log_directory: Directory for log files
            max_segment_bytes: Maximum bytes per segment (default: 10MB)
            run_id: UUID of current run
            config_hash: SHA-256 of core.yaml
            time_policy: "frozen" or "recorded"
            public_key_fingerprint: SHA-256 of public key
        """
        self.log_directory = Path(log_directory)
        self.max_segment_bytes = max_segment_bytes
        self.run_id = run_id
        self.config_hash = config_hash
        self.time_policy = time_policy
        self.public_key_fingerprint = public_key_fingerprint

        self.current_segment_number = 1
        self.current_segment_path = self._get_segment_path(self.current_segment_number)

        # Segment metadata tracking
        self.segments_metadata = []
        self.current_segment_metadata = {
            "filename": self.current_segment_path.name,
            "first_event_seq": None,
            "last_event_seq": None,
            "first_event_hash": None,
            "last_event_hash": None,
            "byte_length": 0,
        }

        # Manifest path
        self.manifest_path = self.log_directory / "manifest.json"

        # Initialize manifest if not exists
        if not self.manifest_path.exists():
            self._write_manifest()

    def _get_segment_path(self, segment_number: int) -> Path:
        """
        Get path for segment file.

        Format: audit.NNNNNN.jsonl (6-digit zero-padded number)

        Args:
            segment_number: Segment number (1-indexed)

        Returns:
            Path to segment file
        """
        filename = f"audit.{segment_number:06d}.jsonl"
        return self.log_directory / filename

    def record_event(
        self,
        event_seq: int,
        event_hash: str,
        byte_length: int
    ) -> Optional[Path]:
        """
        Record an event and check if rotation needed.

        Updates current segment metadata and checks if rotation threshold exceeded.

        Args:
            event_seq: Event sequence number
            event_hash: Event hash (SHA-256)
            byte_length: Byte length of JSONL line written

        Returns:
            New segment path if rotation occurred, None otherwise
        """
        # Update current segment metadata
        if self.current_segment_metadata["first_event_seq"] is None:
            self.current_segment_metadata["first_event_seq"] = event_seq
            self.current_segment_metadata["first_event_hash"] = event_hash

        self.current_segment_metadata["last_event_seq"] = event_seq
        self.current_segment_metadata["last_event_hash"] = event_hash
        self.current_segment_metadata["byte_length"] += byte_length

        # Check if rotation needed
        if self.current_segment_metadata["byte_length"] >= self.max_segment_bytes:
            return self._rotate_segment()

        return None

    def _rotate_segment(self) -> Path:
        """
        Rotate to new segment file.

        Steps:
        1. Finalize current segment metadata
        2. Append to segments list
        3. Increment segment number
        4. Create new segment path
        5. Update manifest atomically

        Returns:
            Path to new segment file
        """
        # Finalize current segment
        self.segments_metadata.append(self.current_segment_metadata.copy())

        # Increment segment number
        self.current_segment_number += 1
        self.current_segment_path = self._get_segment_path(self.current_segment_number)

        # Initialize new segment metadata
        self.current_segment_metadata = {
            "filename": self.current_segment_path.name,
            "first_event_seq": None,
            "last_event_seq": None,
            "first_event_hash": None,
            "last_event_hash": None,
            "byte_length": 0,
        }

        # Update manifest atomically
        self._write_manifest()

        return self.current_segment_path

    def _write_manifest(self):
        """
        Write manifest.json atomically.

        Steps:
        1. Write to temp file
        2. fsync temp file
        3. Rename temp file to manifest.json (atomic)

        Manifest includes:
        - schema_id, schema_version
        - run_id, config_hash
        - created_at, time_policy
        - public_key_fingerprint
        - segments array
        - event_count_total
        - first_event_hash, last_event_hash
        """
        # Compute aggregated values
        event_count_total = sum(
            seg.get("last_event_seq", 0) - seg.get("first_event_seq", 0) + 1
            for seg in self.segments_metadata
            if seg.get("first_event_seq") is not None
        )

        # Add current segment if it has events
        if self.current_segment_metadata.get("first_event_seq") is not None:
            event_count_total += (
                self.current_segment_metadata["last_event_seq"]
                - self.current_segment_metadata["first_event_seq"]
                + 1
            )

        # Determine first and last event hashes across all segments
        first_event_hash = None
        last_event_hash = None

        if self.segments_metadata:
            first_event_hash = self.segments_metadata[0].get("first_event_hash")

        if self.current_segment_metadata.get("last_event_hash"):
            last_event_hash = self.current_segment_metadata["last_event_hash"]
        elif self.segments_metadata:
            last_event_hash = self.segments_metadata[-1].get("last_event_hash")

        # Handle created_at based on time_policy
        if self.time_policy == "frozen":
            created_at = None
        else:
            from datetime import datetime, timezone
            created_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Build manifest
        manifest = {
            "schema_id": "relay.audit_manifest",
            "schema_version": "1.0.0",
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "created_at": created_at,
            "time_policy": self.time_policy,
            "public_key_fingerprint": self.public_key_fingerprint,
            "segments": self.segments_metadata.copy(),
            "event_count_total": event_count_total,
            "first_event_hash": first_event_hash or ("0" * 64),  # Genesis if no events
            "last_event_hash": last_event_hash or ("0" * 64),    # Genesis if no events
        }

        # Write to temp file
        temp_path = self.manifest_path.with_suffix('.json.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename
        temp_path.rename(self.manifest_path)

    def finalize(self):
        """
        Finalize rotation manager (called on daemon shutdown).

        Finalizes current segment and updates manifest one last time.
        """
        if self.current_segment_metadata.get("first_event_seq") is not None:
            # Current segment has events, finalize it
            self.segments_metadata.append(self.current_segment_metadata.copy())
            # Clear current segment metadata to avoid double-counting
            self.current_segment_metadata = {
                "filename": "",
                "first_event_seq": None,
                "last_event_seq": None,
                "first_event_hash": None,
                "last_event_hash": None,
                "byte_length": 0,
            }

        # Final manifest write
        self._write_manifest()

    def get_current_segment_path(self) -> Path:
        """
        Get path to current segment file.

        Returns:
            Path to current segment
        """
        return self.current_segment_path

    def get_segment_metadata_summary(self) -> dict:
        """
        Get summary of segment metadata.

        Returns:
            Dict with: segment_count, total_bytes, current_segment_number
        """
        total_bytes = sum(seg["byte_length"] for seg in self.segments_metadata)
        total_bytes += self.current_segment_metadata["byte_length"]

        return {
            "segment_count": len(self.segments_metadata) + 1,  # +1 for current
            "total_bytes": total_bytes,
            "current_segment_number": self.current_segment_number,
            "current_segment_bytes": self.current_segment_metadata["byte_length"],
        }
