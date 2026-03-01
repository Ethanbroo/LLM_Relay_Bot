"""AWS Lambda render provider for distributed video rendering.

Distributes frame chunks across Lambda functions for parallel rendering.
Each Lambda function receives a chunk specification, renders the frames
using the stateless compositor, and uploads the segment to S3.

Prerequisites:
- AWS credentials configured (via environment, IAM role, or AWS CLI profile)
- Lambda function deployed with the video pipeline code + FFmpeg layer
- S3 bucket for asset upload/download
- boto3 installed

The Lambda function receives a JSON payload with:
- timeline: Serialized Timeline dict
- image_manifest: Dict of clip_id -> S3 URI for source images
- chunk: Start frame, end frame, output S3 key
- config: FPS, resolution, codec preset

Cost estimation:
- Lambda: ~$0.0000166667 per GB-second (1024MB, arm64)
- A 1080x1920 @ 30fps chunk of 300 frames (~10s of video) takes ~30s on Lambda
- Estimated $0.0005 per chunk at 1024MB
"""

import json
import time
import uuid
import logging
from pathlib import Path
from typing import Optional
from PIL import Image

from .base_provider import AbstractRenderProvider
from .models import RenderChunk, RenderJob, ChunkStatus

logger = logging.getLogger(__name__)

# Lambda pricing (us-east-1, arm64, Feb 2026)
LAMBDA_COST_PER_GB_SECOND = 0.0000133334  # arm64 pricing
DEFAULT_LAMBDA_MEMORY_MB = 1024
DEFAULT_LAMBDA_TIMEOUT_S = 300


class LambdaRenderProvider(AbstractRenderProvider):
    """AWS Lambda distributed render provider.

    Uploads source images to S3, invokes Lambda functions for each chunk,
    and downloads the rendered segments from S3.
    """

    def __init__(
        self,
        function_name: str,
        s3_bucket: str,
        s3_prefix: str = "video-render/",
        aws_region: str = "us-east-1",
        lambda_memory_mb: int = DEFAULT_LAMBDA_MEMORY_MB,
        lambda_timeout_s: int = DEFAULT_LAMBDA_TIMEOUT_S,
    ):
        """
        Args:
            function_name: Name of the deployed Lambda function
            s3_bucket: S3 bucket for assets and rendered segments
            s3_prefix: S3 key prefix for this pipeline's files
            aws_region: AWS region
            lambda_memory_mb: Memory allocated to each Lambda invocation
            lambda_timeout_s: Timeout for each Lambda invocation
        """
        self.function_name = function_name
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix.rstrip("/") + "/"
        self.aws_region = aws_region
        self.lambda_memory_mb = lambda_memory_mb
        self.lambda_timeout_s = lambda_timeout_s

        self._lambda_client = None
        self._s3_client = None

    def _get_lambda_client(self):
        """Lazy-initialize boto3 Lambda client."""
        if self._lambda_client is None:
            import boto3
            self._lambda_client = boto3.client(
                "lambda", region_name=self.aws_region
            )
        return self._lambda_client

    def _get_s3_client(self):
        """Lazy-initialize boto3 S3 client."""
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client(
                "s3", region_name=self.aws_region
            )
        return self._s3_client

    def health_check(self) -> bool:
        """Verify Lambda function exists and S3 bucket is accessible."""
        try:
            lambda_client = self._get_lambda_client()
            lambda_client.get_function(FunctionName=self.function_name)

            s3_client = self._get_s3_client()
            s3_client.head_bucket(Bucket=self.s3_bucket)
            return True
        except Exception as e:
            logger.error("Lambda health check failed: %s", e)
            return False

    def upload_assets(
        self,
        image_cache: dict[str, Image.Image],
        job: RenderJob,
    ) -> dict[str, str]:
        """Upload images to S3 for Lambda workers.

        Args:
            image_cache: Images keyed by clip_id
            job: Render job for metadata

        Returns:
            Mapping of clip_id -> S3 URI
        """
        s3_client = self._get_s3_client()
        manifest = {}

        for clip_id, img in image_cache.items():
            s3_key = f"{self.s3_prefix}{job.job_id}/images/{clip_id}.png"

            # Save image to bytes
            import io
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            s3_client.upload_fileobj(
                buffer, self.s3_bucket, s3_key,
                ExtraArgs={"ContentType": "image/png"}
            )

            s3_uri = f"s3://{self.s3_bucket}/{s3_key}"
            manifest[clip_id] = s3_uri
            logger.debug("Uploaded %s -> %s", clip_id, s3_uri)

        logger.info("Uploaded %d images to S3 for job %s", len(manifest), job.job_id)
        return manifest

    def render_chunk(
        self,
        chunk: RenderChunk,
        timeline, image_cache,
        output_dir: str,
        audio_path: Optional[str] = None,
    ) -> RenderChunk:
        """Render a single chunk via Lambda invocation.

        Note: For batch rendering, use the orchestrator which calls
        render_all_chunks_async() for better parallelism.
        """
        start_time = time.time()
        chunk.status = ChunkStatus.RENDERING
        chunk.attempt += 1

        try:
            lambda_client = self._get_lambda_client()
            s3_client = self._get_s3_client()

            # Output S3 key for this chunk
            output_s3_key = (
                f"{self.s3_prefix}{chunk.timeline_id}/chunks/{chunk.chunk_id}.mp4"
            )

            # Build Lambda payload
            payload = {
                "timeline": timeline.model_dump() if hasattr(timeline, 'model_dump') else timeline,
                "image_manifest": getattr(self, '_current_manifest', {}),
                "chunk": {
                    "chunk_id": chunk.chunk_id,
                    "start_frame": chunk.start_frame,
                    "end_frame": chunk.end_frame,
                },
                "output": {
                    "s3_bucket": self.s3_bucket,
                    "s3_key": output_s3_key,
                },
                "config": {
                    "s3_bucket": self.s3_bucket,
                    "s3_prefix": self.s3_prefix,
                },
            }

            # Invoke Lambda (synchronous)
            response = lambda_client.invoke(
                FunctionName=self.function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )

            # Check response
            status_code = response.get("StatusCode", 0)
            if status_code != 200:
                error = response.get("FunctionError", "Unknown error")
                chunk.status = ChunkStatus.FAILED
                chunk.error_message = f"Lambda returned status {status_code}: {error}"
                chunk.render_time_s = time.time() - start_time
                return chunk

            # Parse response payload
            response_payload = json.loads(response["Payload"].read())

            if response_payload.get("success"):
                # Download rendered chunk from S3
                local_path = str(Path(output_dir) / f"{chunk.chunk_id}.mp4")
                s3_client.download_file(self.s3_bucket, output_s3_key, local_path)

                chunk.status = ChunkStatus.COMPLETED
                chunk.output_path = local_path
                chunk.worker_id = response_payload.get("worker_id", "lambda")
            else:
                chunk.status = ChunkStatus.FAILED
                chunk.error_message = response_payload.get("error", "Lambda execution failed")

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
        """Concatenate chunks locally using FFmpeg.

        Lambda rendering produces chunks that are downloaded to local disk,
        so concatenation happens locally.
        """
        from .local_provider import _ffmpeg_concatenate
        return _ffmpeg_concatenate(chunk_paths, output_path, audio_path)

    def cleanup(self, job: RenderJob) -> None:
        """Clean up S3 objects and local chunk files."""
        try:
            s3_client = self._get_s3_client()

            # Delete S3 objects for this job
            prefix = f"{self.s3_prefix}{job.timeline_id}/"
            response = s3_client.list_objects_v2(
                Bucket=self.s3_bucket, Prefix=prefix
            )
            objects = response.get("Contents", [])
            if objects:
                s3_client.delete_objects(
                    Bucket=self.s3_bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]}
                )
                logger.info("Cleaned up %d S3 objects for job %s", len(objects), job.job_id)

        except Exception as e:
            logger.warning("S3 cleanup failed for job %s: %s", job.job_id, e)

        # Clean up local chunk files
        for chunk in job.chunks:
            if chunk.output_path and Path(chunk.output_path).exists():
                try:
                    Path(chunk.output_path).unlink()
                except OSError:
                    pass

    def estimate_cost(self, job: RenderJob) -> float:
        """Estimate Lambda rendering cost.

        Cost = chunks * avg_duration_s * memory_gb * price_per_gb_s

        Args:
            job: Render job specification

        Returns:
            Estimated cost in USD
        """
        if not job.chunks:
            return 0.0

        avg_frames_per_chunk = job.total_frames / len(job.chunks)
        # Rough estimate: 100 frames/second render speed on Lambda
        est_duration_per_chunk_s = max(1.0, avg_frames_per_chunk / 100)

        memory_gb = self.lambda_memory_mb / 1024
        cost_per_chunk = est_duration_per_chunk_s * memory_gb * LAMBDA_COST_PER_GB_SECOND
        total_cost = cost_per_chunk * len(job.chunks)

        return round(total_cost, 6)
