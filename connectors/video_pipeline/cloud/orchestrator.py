"""Render Orchestrator: splits timelines into chunks and coordinates rendering.

The orchestrator is the main entry point for scalable rendering. It:
1. Splits a timeline into render chunks (configurable chunk size)
2. Dispatches chunks to the selected render provider
3. Tracks chunk progress and handles retries
4. Concatenates completed chunks into the final video
5. Muxes audio into the final output
6. Emits audit events throughout

The orchestrator is backend-agnostic — it works with any AbstractRenderProvider.
"""

import uuid
import time
import logging
from pathlib import Path
from typing import Optional
from PIL import Image

from .models import (
    RenderBackend, RenderChunk, RenderJob, RenderResult, ChunkStatus
)
from .base_provider import AbstractRenderProvider
from .local_provider import LocalProvider, LocalMultiprocessProvider
from ..schemas import Timeline

logger = logging.getLogger(__name__)

# Default chunk size: 300 frames = 10 seconds at 30fps
DEFAULT_FRAMES_PER_CHUNK = 300
MIN_FRAMES_PER_CHUNK = 30    # Don't create chunks smaller than 1 second
MAX_CHUNKS = 100              # Safety limit on chunk count


def create_provider(
    backend: RenderBackend,
    config: Optional[dict] = None,
) -> AbstractRenderProvider:
    """Factory function to create a render provider from backend type.

    Args:
        backend: Which render backend to use
        config: Backend-specific configuration dict

    Returns:
        Initialized render provider

    Raises:
        ValueError: If backend is unsupported or config is missing required keys
    """
    config = config or {}

    if backend == RenderBackend.LOCAL:
        return LocalProvider()

    elif backend == RenderBackend.LOCAL_MULTIPROCESS:
        return LocalMultiprocessProvider(
            max_workers=config.get("max_workers"),
        )

    elif backend == RenderBackend.AWS_LAMBDA:
        from .lambda_provider import LambdaRenderProvider
        required = ["function_name", "s3_bucket"]
        for key in required:
            if key not in config:
                raise ValueError(f"Lambda config missing required key: {key}")
        return LambdaRenderProvider(
            function_name=config["function_name"],
            s3_bucket=config["s3_bucket"],
            s3_prefix=config.get("s3_prefix", "video-render/"),
            aws_region=config.get("aws_region", "us-east-1"),
            lambda_memory_mb=config.get("lambda_memory_mb", 1024),
            lambda_timeout_s=config.get("lambda_timeout_s", 300),
        )

    elif backend == RenderBackend.GCP_CLOUD_RUN:
        from .cloudrun_provider import CloudRunRenderProvider
        required = ["service_url", "gcs_bucket"]
        for key in required:
            if key not in config:
                raise ValueError(f"Cloud Run config missing required key: {key}")
        return CloudRunRenderProvider(
            service_url=config["service_url"],
            gcs_bucket=config["gcs_bucket"],
            gcs_prefix=config.get("gcs_prefix", "video-render/"),
            gcp_project=config.get("gcp_project"),
            gcp_region=config.get("gcp_region", "us-central1"),
            vcpu=config.get("vcpu", 2),
            memory_gib=config.get("memory_gib", 2),
            timeout_s=config.get("timeout_s", 300),
        )

    else:
        raise ValueError(f"Unsupported render backend: {backend}")


def _split_into_chunks(
    total_frames: int,
    timeline_id: str,
    frames_per_chunk: int = DEFAULT_FRAMES_PER_CHUNK,
) -> list[RenderChunk]:
    """Split a frame range into render chunks.

    Args:
        total_frames: Total number of frames in the timeline
        timeline_id: Timeline ID for correlation
        frames_per_chunk: Target frames per chunk

    Returns:
        List of RenderChunk objects covering all frames
    """
    frames_per_chunk = max(MIN_FRAMES_PER_CHUNK, frames_per_chunk)
    chunks = []
    start = 0
    chunk_index = 0

    while start < total_frames:
        end = min(start + frames_per_chunk, total_frames)
        chunk_id = f"chunk_{chunk_index:04d}_{uuid.uuid4().hex[:6]}"

        chunks.append(RenderChunk(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            start_frame=start,
            end_frame=end,
            timeline_id=timeline_id,
        ))

        start = end
        chunk_index += 1

        if chunk_index > MAX_CHUNKS:
            # Safety: if we'd create too many chunks, merge remaining into last
            if start < total_frames:
                chunks[-1] = RenderChunk(
                    chunk_id=chunks[-1].chunk_id,
                    chunk_index=chunks[-1].chunk_index,
                    start_frame=chunks[-1].start_frame,
                    end_frame=total_frames,
                    timeline_id=timeline_id,
                )
            break

    return chunks


class RenderOrchestrator:
    """Coordinates distributed rendering of a video timeline.

    Usage:
        orchestrator = RenderOrchestrator(
            backend=RenderBackend.LOCAL_MULTIPROCESS,
            log_daemon=supervisor.log_daemon,
        )
        result = orchestrator.render(timeline, image_cache, output_path)
    """

    def __init__(
        self,
        backend: RenderBackend = RenderBackend.LOCAL,
        backend_config: Optional[dict] = None,
        frames_per_chunk: int = DEFAULT_FRAMES_PER_CHUNK,
        max_retries: int = 2,
        log_daemon=None,
    ):
        """
        Args:
            backend: Which render backend to use
            backend_config: Backend-specific configuration
            frames_per_chunk: Target frames per chunk for splitting
            max_retries: Maximum retry attempts for failed chunks
            log_daemon: LogDaemon for audit events
        """
        self.backend = backend
        self.provider = create_provider(backend, backend_config)
        self.frames_per_chunk = frames_per_chunk
        self.max_retries = max_retries
        self.log_daemon = log_daemon

    def _audit(self, event_type: str, payload: dict):
        """Emit an audit event if log_daemon is available."""
        if self.log_daemon:
            self.log_daemon.ingest_event(
                event_type=event_type,
                actor="video_pipeline.cloud.orchestrator",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload=payload,
            )

    def render(
        self,
        timeline: Timeline,
        image_cache: dict[str, Image.Image],
        output_path: str,
        audio_path: Optional[str] = None,
    ) -> RenderResult:
        """Render a complete video via distributed chunked rendering.

        This is the main entry point. It:
        1. Creates a render job with chunks
        2. Uploads assets if using cloud backend
        3. Renders all chunks (parallel for multiprocess/cloud)
        4. Retries failed chunks
        5. Concatenates chunks into final video
        6. Muxes audio
        7. Cleans up temp files

        Args:
            timeline: Complete video timeline specification
            image_cache: Pre-loaded images keyed by clip_id
            output_path: Where to write the final video file
            audio_path: Optional audio file to mux into the final video

        Returns:
            RenderResult with success status, output path, timing, cost
        """
        overall_start = time.time()
        job_id = f"rjob_{uuid.uuid4().hex[:12]}"
        total_frames = timeline.total_frames

        self._audit("CLOUD_RENDER_JOB_STARTED", {
            "job_id": job_id,
            "backend": self.backend.value,
            "total_frames": total_frames,
            "timeline_id": timeline.timeline_id,
        })

        logger.info(
            "Starting render job %s: %d frames, backend=%s",
            job_id, total_frames, self.backend.value
        )

        # Step 1: Split into chunks
        chunks = _split_into_chunks(
            total_frames, timeline.timeline_id, self.frames_per_chunk
        )

        job = RenderJob(
            job_id=job_id,
            timeline_id=timeline.timeline_id,
            total_frames=total_frames,
            chunks=chunks,
            backend=self.backend,
            max_retries_per_chunk=self.max_retries,
        )

        logger.info("Split into %d chunks (%d frames/chunk)", len(chunks), self.frames_per_chunk)

        self._audit("CLOUD_RENDER_JOB_CHUNKED", {
            "job_id": job_id,
            "chunk_count": len(chunks),
            "frames_per_chunk": self.frames_per_chunk,
        })

        # Step 2: Upload assets for cloud backends
        if self.backend in (RenderBackend.AWS_LAMBDA, RenderBackend.GCP_CLOUD_RUN):
            logger.info("Uploading assets to cloud storage...")
            manifest = self.provider.upload_assets(image_cache, job)
            job.image_manifest = manifest
            # Store manifest on provider for chunk rendering
            self.provider._current_manifest = manifest

        # Step 3: Set up output directory for chunks
        output_dir = str(Path(output_path).parent / f"_chunks_{job_id}")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Step 4: Render chunks
        try:
            if (
                self.backend == RenderBackend.LOCAL_MULTIPROCESS
                and isinstance(self.provider, LocalMultiprocessProvider)
            ):
                # Use parallel rendering for multiprocess backend
                self.provider.render_all_chunks(job, timeline, image_cache, output_dir)
            else:
                # Sequential rendering for local and cloud backends
                for chunk in job.chunks:
                    chunk = self.provider.render_chunk(
                        chunk, timeline, image_cache, output_dir, audio_path
                    )

                    if chunk.status == ChunkStatus.COMPLETED:
                        logger.info(
                            "Chunk %s completed in %.1fs",
                            chunk.chunk_id, chunk.render_time_s
                        )
                    else:
                        logger.warning(
                            "Chunk %s failed: %s",
                            chunk.chunk_id, chunk.error_message
                        )

            # Step 5: Retry failed chunks
            for retry_round in range(self.max_retries):
                failed = [c for c in job.chunks if c.status == ChunkStatus.FAILED]
                if not failed:
                    break

                logger.info(
                    "Retry round %d: %d failed chunks",
                    retry_round + 1, len(failed)
                )

                for chunk in failed:
                    chunk.status = ChunkStatus.RETRYING
                    chunk = self.provider.render_chunk(
                        chunk, timeline, image_cache, output_dir, audio_path
                    )

            # Step 6: Check for remaining failures
            final_failed = [c for c in job.chunks if c.status != ChunkStatus.COMPLETED]
            if final_failed:
                error_msg = (
                    f"{len(final_failed)} chunks failed after {self.max_retries} retries: "
                    + ", ".join(f"{c.chunk_id}: {c.error_message}" for c in final_failed[:3])
                )
                logger.error(error_msg)

                self._audit("CLOUD_RENDER_JOB_FAILED", {
                    "job_id": job_id,
                    "failed_chunks": len(final_failed),
                    "error": error_msg[:500],
                })

                return RenderResult(
                    job_id=job_id,
                    success=False,
                    total_render_time_s=time.time() - overall_start,
                    total_chunks=len(job.chunks),
                    failed_chunks=len(final_failed),
                    total_frames=total_frames,
                    backend_used=self.backend.value,
                    error_message=error_msg,
                )

            # Step 7: Concatenate chunks
            chunk_paths = [
                c.output_path for c in sorted(job.chunks, key=lambda c: c.chunk_index)
                if c.output_path
            ]

            logger.info("Concatenating %d chunks into %s", len(chunk_paths), output_path)

            final_path = self.provider.concatenate(
                chunk_paths, output_path, audio_path
            )

            # Step 8: Calculate results
            total_render_time = sum(c.render_time_s for c in job.chunks)
            wall_time = time.time() - overall_start
            file_size = Path(final_path).stat().st_size if Path(final_path).exists() else 0
            cost = self.provider.estimate_cost(job)

            self._audit("CLOUD_RENDER_JOB_COMPLETED", {
                "job_id": job_id,
                "output_path": final_path,
                "total_chunks": len(job.chunks),
                "total_frames": total_frames,
                "total_render_time_s": round(total_render_time, 2),
                "wall_time_s": round(wall_time, 2),
                "speedup": round(total_render_time / wall_time, 1) if wall_time > 0 else 0,
                "file_size_bytes": file_size,
                "cost_estimate_usd": cost,
                "backend": self.backend.value,
            })

            logger.info(
                "Render complete: %s (%.1fs wall, %.1fs total render, %.1fx speedup, $%.4f est)",
                final_path, wall_time, total_render_time,
                total_render_time / wall_time if wall_time > 0 else 0,
                cost,
            )

            return RenderResult(
                job_id=job_id,
                success=True,
                output_path=final_path,
                total_render_time_s=round(wall_time, 2),
                total_chunks=len(job.chunks),
                failed_chunks=0,
                total_frames=total_frames,
                file_size_bytes=file_size,
                backend_used=self.backend.value,
                cost_estimate_usd=cost,
                chunk_details=[
                    {
                        "chunk_id": c.chunk_id,
                        "frames": c.frame_count,
                        "render_time_s": round(c.render_time_s, 2),
                        "worker_id": c.worker_id,
                    }
                    for c in job.chunks
                ],
            )

        finally:
            # Step 9: Cleanup
            try:
                self.provider.cleanup(job)
                # Remove chunk output directory
                import shutil
                if Path(output_dir).exists():
                    shutil.rmtree(output_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Cleanup failed for job %s: %s", job_id, e)
