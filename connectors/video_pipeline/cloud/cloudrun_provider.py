"""GCP Cloud Run render provider for distributed video rendering.

Distributes frame chunks across Cloud Run instances for parallel rendering.
Each instance receives a chunk specification via HTTP, renders the frames,
and uploads the segment to Google Cloud Storage.

Prerequisites:
- GCP credentials configured (via GOOGLE_APPLICATION_CREDENTIALS or metadata server)
- Cloud Run service deployed with the video pipeline code + FFmpeg
- GCS bucket for asset upload/download
- google-cloud-run and google-cloud-storage installed

The Cloud Run service receives a JSON POST payload with the same structure
as the Lambda provider, enabling code reuse for the worker function.

Cost estimation:
- Cloud Run: ~$0.00002400 per vCPU-second + $0.00000250 per GiB-second
- A typical chunk (~300 frames) with 2 vCPU + 2 GiB takes ~30s
- Estimated $0.0009 per chunk
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional
from PIL import Image

from .base_provider import AbstractRenderProvider
from .models import RenderChunk, RenderJob, ChunkStatus

logger = logging.getLogger(__name__)

# Cloud Run pricing (us-central1, Feb 2026)
CLOUDRUN_VCPU_PER_SECOND = 0.00002400
CLOUDRUN_GIB_PER_SECOND = 0.00000250
DEFAULT_VCPU = 2
DEFAULT_MEMORY_GIB = 2


class CloudRunRenderProvider(AbstractRenderProvider):
    """GCP Cloud Run distributed render provider.

    Uploads source images to GCS, sends HTTP requests to Cloud Run instances
    for each chunk, and downloads rendered segments from GCS.
    """

    def __init__(
        self,
        service_url: str,
        gcs_bucket: str,
        gcs_prefix: str = "video-render/",
        gcp_project: Optional[str] = None,
        gcp_region: str = "us-central1",
        vcpu: int = DEFAULT_VCPU,
        memory_gib: int = DEFAULT_MEMORY_GIB,
        timeout_s: int = 300,
    ):
        """
        Args:
            service_url: Cloud Run service HTTPS URL
            gcs_bucket: GCS bucket for assets and rendered segments
            gcs_prefix: GCS key prefix for this pipeline's files
            gcp_project: GCP project ID (auto-detected if not set)
            gcp_region: GCP region
            vcpu: vCPUs allocated per Cloud Run instance
            memory_gib: Memory in GiB per Cloud Run instance
            timeout_s: Request timeout in seconds
        """
        self.service_url = service_url.rstrip("/")
        self.gcs_bucket = gcs_bucket
        self.gcs_prefix = gcs_prefix.rstrip("/") + "/"
        self.gcp_project = gcp_project
        self.gcp_region = gcp_region
        self.vcpu = vcpu
        self.memory_gib = memory_gib
        self.timeout_s = timeout_s

        self._storage_client = None

    def _get_storage_client(self):
        """Lazy-initialize GCS client."""
        if self._storage_client is None:
            from google.cloud import storage
            self._storage_client = storage.Client(project=self.gcp_project)
        return self._storage_client

    def _get_id_token(self) -> str:
        """Get an identity token for authenticating to Cloud Run."""
        import google.auth.transport.requests
        import google.oauth2.id_token
        request = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(request, self.service_url)

    def health_check(self) -> bool:
        """Verify Cloud Run service is accessible and GCS bucket exists."""
        try:
            storage_client = self._get_storage_client()
            bucket = storage_client.bucket(self.gcs_bucket)
            bucket.reload()

            # Test Cloud Run health endpoint
            import requests
            token = self._get_id_token()
            resp = requests.get(
                f"{self.service_url}/health",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error("Cloud Run health check failed: %s", e)
            return False

    def upload_assets(
        self,
        image_cache: dict[str, Image.Image],
        job: RenderJob,
    ) -> dict[str, str]:
        """Upload images to GCS for Cloud Run workers."""
        storage_client = self._get_storage_client()
        bucket = storage_client.bucket(self.gcs_bucket)
        manifest = {}

        for clip_id, img in image_cache.items():
            blob_name = f"{self.gcs_prefix}{job.job_id}/images/{clip_id}.png"

            import io
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            blob = bucket.blob(blob_name)
            blob.upload_from_file(buffer, content_type="image/png")

            gcs_uri = f"gs://{self.gcs_bucket}/{blob_name}"
            manifest[clip_id] = gcs_uri
            logger.debug("Uploaded %s -> %s", clip_id, gcs_uri)

        logger.info("Uploaded %d images to GCS for job %s", len(manifest), job.job_id)
        return manifest

    def render_chunk(
        self,
        chunk: RenderChunk,
        timeline, image_cache,
        output_dir: str,
        audio_path: Optional[str] = None,
    ) -> RenderChunk:
        """Render a single chunk via Cloud Run HTTP request."""
        start_time = time.time()
        chunk.status = ChunkStatus.RENDERING
        chunk.attempt += 1

        try:
            import requests

            output_gcs_key = (
                f"{self.gcs_prefix}{chunk.timeline_id}/chunks/{chunk.chunk_id}.mp4"
            )

            payload = {
                "timeline": timeline.model_dump() if hasattr(timeline, 'model_dump') else timeline,
                "image_manifest": getattr(self, '_current_manifest', {}),
                "chunk": {
                    "chunk_id": chunk.chunk_id,
                    "start_frame": chunk.start_frame,
                    "end_frame": chunk.end_frame,
                },
                "output": {
                    "gcs_bucket": self.gcs_bucket,
                    "gcs_key": output_gcs_key,
                },
            }

            token = self._get_id_token()
            response = requests.post(
                f"{self.service_url}/render",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_s,
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    # Download rendered chunk from GCS
                    local_path = str(Path(output_dir) / f"{chunk.chunk_id}.mp4")
                    storage_client = self._get_storage_client()
                    bucket = storage_client.bucket(self.gcs_bucket)
                    blob = bucket.blob(output_gcs_key)
                    blob.download_to_filename(local_path)

                    chunk.status = ChunkStatus.COMPLETED
                    chunk.output_path = local_path
                    chunk.worker_id = result.get("worker_id", "cloudrun")
                else:
                    chunk.status = ChunkStatus.FAILED
                    chunk.error_message = result.get("error", "Cloud Run execution failed")
            else:
                chunk.status = ChunkStatus.FAILED
                chunk.error_message = (
                    f"Cloud Run returned status {response.status_code}: "
                    f"{response.text[:300]}"
                )

        except Exception as e:
            chunk.status = ChunkStatus.FAILED
            chunk.error_message = str(e)[:500]

        chunk.render_time_s = time.time() - start_time
        return chunk

    def concatenate(
        self,
        chunk_paths: list[str],
        output_path: str,
        audio_path: Optional[str] = None,
    ) -> str:
        """Concatenate chunks locally using FFmpeg."""
        from .local_provider import _ffmpeg_concatenate
        return _ffmpeg_concatenate(chunk_paths, output_path, audio_path)

    def cleanup(self, job: RenderJob) -> None:
        """Clean up GCS objects and local chunk files."""
        try:
            storage_client = self._get_storage_client()
            bucket = storage_client.bucket(self.gcs_bucket)

            prefix = f"{self.gcs_prefix}{job.timeline_id}/"
            blobs = list(bucket.list_blobs(prefix=prefix))
            if blobs:
                bucket.delete_blobs(blobs)
                logger.info("Cleaned up %d GCS objects for job %s", len(blobs), job.job_id)

        except Exception as e:
            logger.warning("GCS cleanup failed for job %s: %s", job.job_id, e)

        for chunk in job.chunks:
            if chunk.output_path and Path(chunk.output_path).exists():
                try:
                    Path(chunk.output_path).unlink()
                except OSError:
                    pass

    def estimate_cost(self, job: RenderJob) -> float:
        """Estimate Cloud Run rendering cost."""
        if not job.chunks:
            return 0.0

        avg_frames = job.total_frames / len(job.chunks)
        est_duration_s = max(1.0, avg_frames / 100)

        cpu_cost = est_duration_s * self.vcpu * CLOUDRUN_VCPU_PER_SECOND
        mem_cost = est_duration_s * self.memory_gib * CLOUDRUN_GIB_PER_SECOND
        cost_per_chunk = cpu_cost + mem_cost
        total_cost = cost_per_chunk * len(job.chunks)

        return round(total_cost, 6)
