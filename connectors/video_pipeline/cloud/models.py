"""Data models for cloud rendering jobs, results, and status tracking."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path


class RenderBackend(str, Enum):
    """Available render backends."""
    LOCAL = "local"                  # Single-process (existing behavior)
    LOCAL_MULTIPROCESS = "local_mp"  # Local multiprocessing pool
    AWS_LAMBDA = "aws_lambda"        # AWS Lambda distributed rendering
    GCP_CLOUD_RUN = "gcp_cloudrun"   # GCP Cloud Run distributed rendering


class ChunkStatus(str, Enum):
    """Status of a render chunk."""
    PENDING = "pending"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class RenderChunk:
    """A contiguous range of frames to render as a unit.

    Each chunk is rendered independently and produces a video segment.
    The orchestrator concatenates all segments into the final video.
    """
    chunk_id: str
    chunk_index: int                # 0-indexed position in the sequence
    start_frame: int                # First frame number (inclusive)
    end_frame: int                  # Last frame number (exclusive)
    timeline_id: str                # Which timeline this belongs to
    status: ChunkStatus = ChunkStatus.PENDING
    attempt: int = 0
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    render_time_s: float = 0.0
    worker_id: Optional[str] = None  # Which worker rendered this chunk

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame

    def __repr__(self) -> str:
        return (
            f"RenderChunk({self.chunk_id}, frames={self.start_frame}-{self.end_frame}, "
            f"status={self.status.value})"
        )


@dataclass
class RenderJob:
    """Complete render job specification.

    A job contains the full timeline specification plus the chunking strategy.
    It's the top-level unit of work for the render orchestrator.
    """
    job_id: str
    timeline_id: str
    total_frames: int
    chunks: list[RenderChunk] = field(default_factory=list)
    backend: RenderBackend = RenderBackend.LOCAL
    max_retries_per_chunk: int = 2
    max_concurrent_workers: int = 4
    # Serialized timeline + image cache references for remote workers
    timeline_json: Optional[str] = None
    image_manifest: dict = field(default_factory=dict)  # clip_id -> s3/gcs path

    @property
    def total_chunks(self) -> int:
        return len(self.chunks)

    @property
    def completed_chunks(self) -> int:
        return sum(1 for c in self.chunks if c.status == ChunkStatus.COMPLETED)

    @property
    def failed_chunks(self) -> int:
        return sum(1 for c in self.chunks if c.status == ChunkStatus.FAILED)

    @property
    def progress(self) -> float:
        """Completion percentage (0.0 to 1.0)."""
        if not self.chunks:
            return 0.0
        return self.completed_chunks / len(self.chunks)

    @property
    def is_complete(self) -> bool:
        return all(c.status == ChunkStatus.COMPLETED for c in self.chunks)

    @property
    def has_failures(self) -> bool:
        return any(c.status == ChunkStatus.FAILED for c in self.chunks)


@dataclass
class RenderResult:
    """Final result of a completed render job."""
    job_id: str
    success: bool
    output_path: Optional[str] = None
    total_render_time_s: float = 0.0
    total_chunks: int = 0
    failed_chunks: int = 0
    total_frames: int = 0
    file_size_bytes: int = 0
    backend_used: str = "local"
    cost_estimate_usd: float = 0.0  # Estimated cloud cost
    error_message: Optional[str] = None
    chunk_details: list[dict] = field(default_factory=list)
