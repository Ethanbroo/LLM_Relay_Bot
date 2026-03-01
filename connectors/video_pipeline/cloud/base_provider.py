"""Abstract base class for render providers.

Every render backend (local multiprocess, Lambda, Cloud Run) implements
this interface. The orchestrator is backend-agnostic — it just calls
render_chunk() and concatenate().
"""

from abc import ABC, abstractmethod
from typing import Optional
from PIL import Image

from .models import RenderChunk, RenderJob
from ..schemas import Timeline


class AbstractRenderProvider(ABC):
    """Base class for all render backends.

    Subclasses must implement:
    - render_chunk(): Render a range of frames into a video segment
    - concatenate(): Join multiple video segments into one file
    - cleanup(): Clean up temporary files

    Optional:
    - upload_assets(): Upload images to remote storage (for cloud backends)
    - health_check(): Verify the backend is operational
    """

    @abstractmethod
    def render_chunk(
        self,
        chunk: RenderChunk,
        timeline: Timeline,
        image_cache: dict[str, Image.Image],
        output_dir: str,
        audio_path: Optional[str] = None,
    ) -> RenderChunk:
        """Render a chunk of frames into a video segment.

        Args:
            chunk: The chunk specification (frame range)
            timeline: Full timeline specification
            image_cache: Pre-loaded images keyed by clip_id
            output_dir: Directory to write the segment file
            audio_path: Optional audio file (only applied to first chunk or during concat)

        Returns:
            Updated RenderChunk with status, output_path, render_time
        """
        pass

    @abstractmethod
    def concatenate(
        self,
        chunk_paths: list[str],
        output_path: str,
        audio_path: Optional[str] = None,
    ) -> str:
        """Concatenate rendered chunk segments into a single video file.

        Args:
            chunk_paths: Ordered list of video segment file paths
            output_path: Where to write the final concatenated video
            audio_path: Optional audio to mux into the final video

        Returns:
            Path to the final video file

        Raises:
            RuntimeError: If concatenation fails
        """
        pass

    @abstractmethod
    def cleanup(self, job: RenderJob) -> None:
        """Clean up temporary files from a completed render job.

        Args:
            job: The completed render job
        """
        pass

    def upload_assets(
        self,
        image_cache: dict[str, Image.Image],
        job: RenderJob,
    ) -> dict[str, str]:
        """Upload image assets to remote storage for cloud workers.

        Default implementation: no-op (local backends don't need uploads).
        Cloud backends override this to upload to S3/GCS.

        Args:
            image_cache: Images keyed by clip_id
            job: The render job (for metadata)

        Returns:
            Mapping of clip_id -> remote URL/path
        """
        return {}

    def health_check(self) -> bool:
        """Check if the backend is operational.

        Returns:
            True if backend is ready to accept render jobs
        """
        return True

    def estimate_cost(self, job: RenderJob) -> float:
        """Estimate the cost of rendering this job on this backend.

        Args:
            job: The render job specification

        Returns:
            Estimated cost in USD (0.0 for local backends)
        """
        return 0.0
