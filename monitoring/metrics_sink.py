"""Metrics sink for Phase 7 - JSONL append-only with fsync.

Phase 7 Invariant: Metrics are written deterministically with fsync guarantees.
"""

import os
import json
from pathlib import Path
from typing import List, Optional
from monitoring.metrics_types import MetricsRecord


class MetricsSinkError(Exception):
    """Base exception for metrics sink errors."""
    pass


class MetricsSink:
    """JSONL metrics sink with fsync policy and rotation.

    Phase 7 Invariants:
    - Append-only JSONL format
    - seq assignment monotonic with no gaps
    - Rotation on max_segment_bytes
    - fsync_each_line policy enforced
    """

    def __init__(
        self,
        metrics_dir: str,
        run_id: str,
        max_segment_bytes: int = 10_000_000,
        flush_policy: str = "fsync_each_line"
    ):
        """Initialize metrics sink.

        Args:
            metrics_dir: Directory for metrics files
            run_id: Run identifier
            max_segment_bytes: Maximum bytes per segment (default 10MB)
            flush_policy: Flush policy (must be "fsync_each_line")

        Raises:
            MetricsSinkError: If flush_policy is invalid
        """
        if flush_policy != "fsync_each_line":
            raise MetricsSinkError(f"Only fsync_each_line policy allowed, got {flush_policy}")

        self.metrics_dir = Path(metrics_dir)
        self.run_id = run_id
        self.max_segment_bytes = max_segment_bytes
        self.flush_policy = flush_policy

        # Create metrics directory
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

        # Current segment
        self.current_segment_idx = 0
        self.current_segment_bytes = 0
        self.current_file = None
        self.current_file_path = None

        # Open first segment
        self._open_new_segment()

    def _open_new_segment(self) -> None:
        """Open new metrics segment file.

        Phase 7 Invariant: Segment filenames are deterministic.
        """
        # Close current file if open
        if self.current_file is not None:
            self.current_file.close()

        # Generate deterministic filename
        filename = f"metrics_{self.run_id}_{self.current_segment_idx:06d}.jsonl"
        self.current_file_path = self.metrics_dir / filename

        # Open file in append mode
        self.current_file = open(self.current_file_path, 'a', encoding='utf-8')
        self.current_segment_bytes = 0

    def write(self, records: List[MetricsRecord]) -> None:
        """Write metrics records to sink.

        Phase 7 Invariant: Each line is fsynced immediately.

        Args:
            records: List of MetricsRecord instances

        Raises:
            MetricsSinkError: If write fails
        """
        for record in records:
            # Convert to dict and serialize
            record_dict = record.to_dict()
            line = json.dumps(record_dict, sort_keys=True) + '\n'
            line_bytes = line.encode('utf-8')

            # Check if rotation needed
            if self.current_segment_bytes + len(line_bytes) > self.max_segment_bytes:
                self.current_segment_idx += 1
                self._open_new_segment()

            # Write line
            try:
                self.current_file.write(line)
                self.current_file.flush()
                os.fsync(self.current_file.fileno())
                self.current_segment_bytes += len(line_bytes)

            except (IOError, OSError) as e:
                raise MetricsSinkError(f"Failed to write metrics: {e}")

    def close(self) -> None:
        """Close metrics sink.

        Phase 7 Invariant: Flush and close cleanly.
        """
        if self.current_file is not None:
            self.current_file.flush()
            os.fsync(self.current_file.fileno())
            self.current_file.close()
            self.current_file = None

    def get_segment_paths(self) -> List[Path]:
        """Get all segment file paths for this run.

        Returns:
            List of Path objects for metrics segments
        """
        pattern = f"metrics_{self.run_id}_*.jsonl"
        return sorted(self.metrics_dir.glob(pattern))

    def read_window(self, start_seq: int, end_seq: int) -> List[dict]:
        """Read metrics window by sequence range.

        Args:
            start_seq: Starting sequence (inclusive)
            end_seq: Ending sequence (inclusive)

        Returns:
            List of metric records as dicts
        """
        records = []

        for segment_path in self.get_segment_paths():
            with open(segment_path, 'r', encoding='utf-8') as f:
                for line in f:
                    record = json.loads(line)
                    if start_seq <= record['seq'] <= end_seq:
                        records.append(record)

        return records
