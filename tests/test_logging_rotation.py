"""
Tests for audit_logging/rotation.py

Covers:
- Segment rotation when max_segment_bytes exceeded
- Manifest creation and updates
- Atomic manifest writes
- First/last event hash tracking
"""

import pytest
import tempfile
import json
from pathlib import Path

from audit_logging.rotation import SegmentRotationManager


@pytest.fixture
def temp_log_dir():
    """Create temporary directory for log files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def rotation_manager(temp_log_dir):
    """Create SegmentRotationManager for tests."""
    return SegmentRotationManager(
        log_directory=temp_log_dir,
        max_segment_bytes=1000,  # Small size for testing rotation
        run_id="run123",
        config_hash="a" * 64,
        time_policy="frozen",
        public_key_fingerprint="b" * 64,
    )


class TestRotationManagerInit:
    """Tests for SegmentRotationManager initialization."""

    def test_creates_initial_manifest(self, temp_log_dir):
        """Manager creates initial manifest on init"""
        manager = SegmentRotationManager(
            log_directory=temp_log_dir,
            run_id="run123",
            config_hash="a" * 64,
            time_policy="frozen",
            public_key_fingerprint="b" * 64,
        )

        manifest_path = temp_log_dir / "manifest.json"
        assert manifest_path.exists()

        # Read and verify manifest
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["schema_id"] == "relay.audit_manifest"
        assert manifest["run_id"] == "run123"
        assert manifest["segments"] == []
        assert manifest["event_count_total"] == 0

    def test_initial_segment_path(self, rotation_manager):
        """Initial segment is audit.000001.jsonl"""
        path = rotation_manager.get_current_segment_path()
        assert path.name == "audit.000001.jsonl"


class TestEventRecording:
    """Tests for record_event method."""

    def test_record_single_event(self, rotation_manager):
        """Record a single event"""
        result = rotation_manager.record_event(
            event_seq=1,
            event_hash="hash1",
            byte_length=100
        )

        # No rotation should occur (< 1000 bytes)
        assert result is None

        metadata = rotation_manager.current_segment_metadata
        assert metadata["first_event_seq"] == 1
        assert metadata["last_event_seq"] == 1
        assert metadata["first_event_hash"] == "hash1"
        assert metadata["last_event_hash"] == "hash1"
        assert metadata["byte_length"] == 100

    def test_record_multiple_events(self, rotation_manager):
        """Record multiple events"""
        rotation_manager.record_event(1, "hash1", 100)
        rotation_manager.record_event(2, "hash2", 100)
        rotation_manager.record_event(3, "hash3", 100)

        metadata = rotation_manager.current_segment_metadata
        assert metadata["first_event_seq"] == 1
        assert metadata["last_event_seq"] == 3
        assert metadata["first_event_hash"] == "hash1"
        assert metadata["last_event_hash"] == "hash3"
        assert metadata["byte_length"] == 300


class TestSegmentRotation:
    """Tests for segment rotation."""

    def test_rotation_when_max_bytes_exceeded(self, rotation_manager):
        """Rotation occurs when max_segment_bytes reached or exceeded"""
        # Record events totaling exactly 1000 bytes (= max)
        result = None
        for i in range(10):
            result = rotation_manager.record_event(i + 1, f"hash{i+1}", 100)

        # 10th event should trigger rotation (1000 bytes = max)
        assert result is not None
        assert result.name == "audit.000002.jsonl"

        # Current segment should be #2
        assert rotation_manager.current_segment_number == 2

    def test_metadata_preserved_after_rotation(self, rotation_manager):
        """Segment metadata preserved after rotation"""
        # Fill first segment
        for i in range(10):
            rotation_manager.record_event(i + 1, f"hash{i+1}", 100)

        # Trigger rotation
        rotation_manager.record_event(11, "hash11", 100)

        # Check first segment metadata
        assert len(rotation_manager.segments_metadata) == 1
        seg1 = rotation_manager.segments_metadata[0]

        assert seg1["filename"] == "audit.000001.jsonl"
        assert seg1["first_event_seq"] == 1
        assert seg1["last_event_seq"] == 10
        assert seg1["first_event_hash"] == "hash1"
        assert seg1["last_event_hash"] == "hash10"
        assert seg1["byte_length"] == 1000

        # Current segment should have event 11
        assert rotation_manager.current_segment_metadata["first_event_seq"] == 11
        assert rotation_manager.current_segment_metadata["last_event_seq"] == 11

    def test_multiple_rotations(self, rotation_manager):
        """Multiple rotations work correctly"""
        # Trigger 3 rotations
        for seg in range(3):
            for i in range(10):
                event_seq = seg * 10 + i + 1
                rotation_manager.record_event(event_seq, f"hash{event_seq}", 100)

            # Trigger rotation
            event_seq = seg * 10 + 11
            rotation_manager.record_event(event_seq, f"hash{event_seq}", 100)

        # Should have 3 finalized segments
        assert len(rotation_manager.segments_metadata) == 3
        assert rotation_manager.current_segment_number == 4


class TestManifestUpdates:
    """Tests for manifest.json updates."""

    def test_manifest_updated_after_rotation(self, rotation_manager, temp_log_dir):
        """Manifest updated after rotation"""
        # Trigger rotation
        for i in range(11):
            rotation_manager.record_event(i + 1, f"hash{i+1}", 100)

        # Read manifest
        with open(temp_log_dir / "manifest.json") as f:
            manifest = json.load(f)

        # Should have 1 finalized segment
        assert len(manifest["segments"]) == 1
        assert manifest["segments"][0]["filename"] == "audit.000001.jsonl"
        assert manifest["segments"][0]["first_event_seq"] == 1
        assert manifest["segments"][0]["last_event_seq"] == 10

    def test_manifest_event_count_total(self, rotation_manager, temp_log_dir):
        """Manifest tracks total event count"""
        # Record 15 events (will span 2 segments with rotation at 10)
        for i in range(15):
            rotation_manager.record_event(i + 1, f"hash{i+1}", 100)

        rotation_manager.finalize()

        with open(temp_log_dir / "manifest.json") as f:
            manifest = json.load(f)

        # Should count all 15 events
        assert manifest["event_count_total"] == 15

    def test_manifest_first_last_hashes(self, rotation_manager, temp_log_dir):
        """Manifest tracks first and last event hashes"""
        # Record events across 2 segments
        for i in range(15):
            rotation_manager.record_event(i + 1, f"hash{i+1}", 100)

        rotation_manager.finalize()

        with open(temp_log_dir / "manifest.json") as f:
            manifest = json.load(f)

        # First hash should be from event 1
        assert manifest["first_event_hash"] == "hash1"

        # Last hash should be from event 15
        assert manifest["last_event_hash"] == "hash15"


class TestFinalize:
    """Tests for finalize method."""

    def test_finalize_current_segment(self, rotation_manager, temp_log_dir):
        """Finalize adds current segment to manifest"""
        # Record events (no rotation)
        for i in range(5):
            rotation_manager.record_event(i + 1, f"hash{i+1}", 100)

        rotation_manager.finalize()

        with open(temp_log_dir / "manifest.json") as f:
            manifest = json.load(f)

        # Should have 1 segment (current was finalized)
        assert len(manifest["segments"]) == 1
        assert manifest["segments"][0]["first_event_seq"] == 1
        assert manifest["segments"][0]["last_event_seq"] == 5
