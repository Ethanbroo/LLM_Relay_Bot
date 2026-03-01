"""Video Agent: orchestrates the complete video creation pipeline.

Pipeline:
1. Receive content brief (or direct storyboard)
2. Generate storyboard via LLM
3. Convert storyboard to timeline
4. Generate/collect all visual assets (images via existing image generator)
5. Run quality gating on each AI-generated frame (reuses existing tier 1-4)
6. Render video — either locally or via cloud rendering (multiprocess/Lambda/Cloud Run)
7. Emit audit events throughout

Integrates with existing supervisor.py and reuses:
- connectors.instagram_pipeline.generation for image generation
- connectors.instagram_pipeline.quality for quality gating
- connectors.instagram_pipeline.character for identity verification
- audit_logging.log_daemon for tamper-evident audit trail
- connectors.video_pipeline.cloud for scalable distributed rendering
"""

import uuid
import logging
import tempfile
from pathlib import Path
from typing import Optional
from PIL import Image

from .schemas import Storyboard, Timeline, VideoFormat
from .timeline import storyboard_to_timeline
from .compositor import FrameCompositor
from .encoder import encode_video, check_ffmpeg
from .audio import resolve_audio_tracks, mix_audio
from .storyboard import generate_storyboard
from .cloud.orchestrator import RenderOrchestrator
from .templates.base import BaseTemplate, TemplateInput
from .templates.registry import get_template, list_templates

logger = logging.getLogger(__name__)


class VideoAgent:
    """Agent responsible for end-to-end video creation.

    Usage:
        agent = VideoAgent(config)
        video_path = agent.create_video(brief)
    """

    def __init__(
        self,
        output_dir: str,
        image_generator=None,
        quality_orchestrator=None,
        claude_client=None,
        log_daemon=None,
        audio_library_dir: Optional[str] = None,
        max_retries_per_clip: int = 3,
        render_orchestrator: Optional[RenderOrchestrator] = None,
    ):
        """
        Args:
            output_dir: Where to save rendered videos
            image_generator: Instance of AbstractAssetGenerator (e.g. FluxImageGenerator)
            quality_orchestrator: Instance of QualityGateOrchestrator
            claude_client: ClaudeClient for storyboard generation
            log_daemon: LogDaemon for audit events
            audio_library_dir: Directory containing audio files for BGM
            max_retries_per_clip: How many times to retry failed AI image generation
            render_orchestrator: Optional RenderOrchestrator for distributed/cloud rendering.
                                 If None, falls back to direct FrameCompositor + encode_video.
        """
        self.output_dir = Path(output_dir)
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_pipeline_"))
        self.image_generator = image_generator
        self.quality_orchestrator = quality_orchestrator
        self.claude_client = claude_client
        self.log_daemon = log_daemon
        self.audio_library_dir = Path(audio_library_dir) if audio_library_dir else None
        self.max_retries = max_retries_per_clip
        self.render_orchestrator = render_orchestrator

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _audit(self, event_type: str, payload: dict):
        """Emit an audit event if log_daemon is available."""
        if self.log_daemon:
            self.log_daemon.ingest_event(
                event_type=event_type,
                actor="video_pipeline.video_agent",
                correlation={"session_id": None, "message_id": None, "task_id": None},
                payload=payload,
            )

    def _generate_clip_image(self, clip) -> Optional[Image.Image]:
        """Generate an AI image for a clip and run quality gating.

        Args:
            clip: Clip with source_type='ai_generated'

        Returns:
            PIL Image if generation + quality passes, None if all retries exhausted
        """
        if self.image_generator is None:
            logger.warning("No image generator configured, cannot generate clip %s", clip.clip_id)
            return None

        from connectors.instagram_pipeline.generation.base import GenerationRequest

        for attempt in range(1, self.max_retries + 1):
            logger.info(
                "Generating image for %s (attempt %d/%d): %s",
                clip.clip_id, attempt, self.max_retries,
                (clip.ai_prompt or "")[:80]
            )

            self._audit("VIDEO_CLIP_GENERATION_STARTED", {
                "clip_id": clip.clip_id,
                "attempt": attempt,
                "prompt_preview": (clip.ai_prompt or "")[:100],
            })

            try:
                request = GenerationRequest(prompt=clip.ai_prompt or "")
                result = self.image_generator.generate(request)

                # Download the image
                local_path = str(self.temp_dir / f"{clip.clip_id}_attempt{attempt}.jpg")
                self.image_generator.download_asset(result.image_url, local_path)

                # Run quality gates if orchestrator is available
                if self.quality_orchestrator:
                    gate_result = self.quality_orchestrator.evaluate(
                        image_path=local_path,
                        prompt=clip.ai_prompt or "",
                    )

                    if gate_result.failed:
                        logger.warning(
                            "Quality gate FAILED for %s attempt %d: %s",
                            clip.clip_id, attempt, gate_result.rejection_reason
                        )
                        self._audit("VIDEO_CLIP_QUALITY_FAILED", {
                            "clip_id": clip.clip_id,
                            "attempt": attempt,
                            "reason": gate_result.rejection_reason,
                        })
                        continue

                img = Image.open(local_path).convert("RGB")

                self._audit("VIDEO_CLIP_GENERATION_COMPLETED", {
                    "clip_id": clip.clip_id,
                    "attempt": attempt,
                    "cost_usd": result.cost_usd,
                })

                return img

            except Exception as e:
                logger.error(
                    "Image generation failed for %s attempt %d: %s",
                    clip.clip_id, attempt, e
                )
                self._audit("VIDEO_CLIP_GENERATION_FAILED", {
                    "clip_id": clip.clip_id,
                    "attempt": attempt,
                    "error": str(e)[:200],
                })

        logger.error("All %d attempts failed for clip %s", self.max_retries, clip.clip_id)
        return None

    def _collect_images(self, timeline: Timeline) -> dict[str, Image.Image]:
        """Collect or generate all images needed by the timeline.

        Returns:
            Dict mapping clip_id to PIL Image
        """
        image_cache = {}

        for clip in timeline.clips:
            if clip.source_type == "image" and clip.source_path:
                try:
                    img = Image.open(clip.source_path).convert("RGB")
                    image_cache[clip.clip_id] = img
                    logger.info("Loaded image for %s: %s", clip.clip_id, clip.source_path)
                except Exception as e:
                    logger.error("Failed to load image for %s: %s", clip.clip_id, e)

            elif clip.source_type == "ai_generated":
                img = self._generate_clip_image(clip)
                if img is not None:
                    image_cache[clip.clip_id] = img
                else:
                    logger.warning("Using placeholder for %s (generation failed)", clip.clip_id)

            elif clip.source_type == "video":
                # Extract first frame from video for now
                # Full video-in-video support is a future enhancement
                logger.info("Video source clips use first frame: %s", clip.clip_id)

        return image_cache

    def create_video_from_template(
        self,
        template_name: str,
        inputs: TemplateInput,
    ) -> Path:
        """Create a video using a registered template.

        The template converts user-provided inputs (images, text, etc.)
        into a Timeline, which is then rendered via the standard pipeline.

        Args:
            template_name: Name of a registered template
            inputs: Template-specific input model

        Returns:
            Path to the rendered video file

        Raises:
            ValueError: If template not found or inputs invalid
            RuntimeError: If rendering fails
        """
        template = get_template(template_name)

        self._audit("VIDEO_TEMPLATE_SELECTED", {
            "template_name": template_name,
            "platform": template.supported_platforms,
        })

        # Validate inputs
        errors = template.validate_inputs(inputs)
        self._audit("VIDEO_TEMPLATE_VALIDATED", {
            "template_name": template_name,
            "valid": len(errors) == 0,
            "errors": errors[:5],
        })
        if errors:
            raise ValueError(
                f"Template '{template_name}' input validation failed: {'; '.join(errors)}"
            )

        # Build timeline from template
        timeline = template.build_timeline(inputs)

        self._audit("VIDEO_TEMPLATE_TIMELINE_BUILT", {
            "template_name": template_name,
            "timeline_id": timeline.timeline_id,
            "clip_count": len(timeline.clips),
            "duration_ms": timeline.total_duration_ms,
        })

        logger.info(
            "Template '%s' built timeline: %d clips, %dms",
            template_name, len(timeline.clips), timeline.total_duration_ms,
        )

        # Render via the standard pipeline
        return self.create_video(timeline=timeline)

    def list_available_templates(self) -> list[dict]:
        """Return metadata for all registered templates."""
        return list_templates()

    def create_video(
        self,
        brief: Optional[dict] = None,
        storyboard: Optional[Storyboard] = None,
        timeline: Optional[Timeline] = None,
    ) -> Path:
        """Full pipeline: brief -> storyboard -> timeline -> rendered video.

        Provide ONE of: brief, storyboard, or timeline. Each is a successively
        more concrete specification.

        Args:
            brief: Content brief dict (will generate storyboard via LLM)
            storyboard: Pre-built Storyboard (will convert to timeline)
            timeline: Pre-built Timeline (will render directly)

        Returns:
            Path to the rendered video file

        Raises:
            ValueError: If no input provided or input is invalid
            RuntimeError: If rendering fails
        """
        run_id = f"vrun_{uuid.uuid4().hex[:12]}"

        self._audit("VIDEO_PIPELINE_STARTED", {
            "run_id": run_id,
            "input_type": "brief" if brief else "storyboard" if storyboard else "timeline",
        })

        # Step 1: Generate storyboard from brief if needed
        if timeline is None and storyboard is None:
            if brief is None:
                raise ValueError("Must provide brief, storyboard, or timeline")

            storyboard = generate_storyboard(
                brief=brief,
                claude_client=self.claude_client,
                log_daemon=self.log_daemon,
            )

        # Step 2: Convert storyboard to timeline if needed
        if timeline is None:
            timeline = storyboard_to_timeline(
                storyboard=storyboard,
                log_daemon=self.log_daemon,
            )

        # Step 3: Verify FFmpeg is available
        if not check_ffmpeg():
            raise RuntimeError(
                "FFmpeg is required for video encoding. "
                "Install: brew install ffmpeg (macOS) or apt-get install ffmpeg (Ubuntu)"
            )

        # Step 4: Collect/generate all images
        logger.info("Collecting images for %d clips", len(timeline.clips))
        image_cache = self._collect_images(timeline)

        loaded = len(image_cache)
        total = len(timeline.clips)
        logger.info("Loaded %d/%d clip images", loaded, total)

        if loaded == 0:
            raise RuntimeError("No images could be loaded or generated for any clip")

        # Step 5: Resolve and mix audio
        audio_path = None
        resolved_tracks = resolve_audio_tracks(timeline, self.audio_library_dir)
        if resolved_tracks:
            audio_output = self.temp_dir / f"{run_id}_audio.mp3"
            audio_path = mix_audio(
                tracks=resolved_tracks,
                total_duration_ms=timeline.total_duration_ms,
                output_path=audio_output,
                log_daemon=self.log_daemon,
            )

        # Determine output filename
        ext = timeline.output_format.value
        if ext == "frames":
            ext = "mp4"  # Fall back for frame export mode
        output_filename = f"{run_id}.{ext}"
        output_path = self.output_dir / output_filename
        total_frames = timeline.total_frames

        logger.info(
            "Rendering %d frames to %s (%dx%d @ %dfps)",
            total_frames, output_path,
            timeline.resolution.width, timeline.resolution.height,
            timeline.fps
        )

        # Step 6: Render — either via cloud orchestrator or direct local encoding
        if self.render_orchestrator is not None:
            # Distributed / cloud rendering via RenderOrchestrator
            logger.info(
                "Using cloud render orchestrator (backend=%s)",
                self.render_orchestrator.backend.value
            )

            render_result = self.render_orchestrator.render(
                timeline=timeline,
                image_cache=image_cache,
                output_path=str(output_path),
                audio_path=str(audio_path) if audio_path else None,
            )

            if not render_result.success:
                self._audit("VIDEO_PIPELINE_FAILED", {
                    "run_id": run_id,
                    "error": render_result.error_message or "Cloud render failed",
                    "backend": render_result.backend_used,
                    "failed_chunks": render_result.failed_chunks,
                })
                raise RuntimeError(
                    f"Cloud rendering failed: {render_result.error_message}"
                )

            result_path = Path(render_result.output_path)

            self._audit("VIDEO_PIPELINE_COMPLETED", {
                "run_id": run_id,
                "output_path": str(result_path),
                "timeline_id": timeline.timeline_id,
                "duration_ms": timeline.total_duration_ms,
                "frame_count": total_frames,
                "clip_count": len(timeline.clips),
                "clips_with_images": loaded,
                "render_backend": render_result.backend_used,
                "render_time_s": render_result.total_render_time_s,
                "render_cost_usd": render_result.cost_estimate_usd,
                "render_chunks": render_result.total_chunks,
            })

        else:
            # Direct local rendering via FrameCompositor + encode_video
            compositor = FrameCompositor(timeline, image_cache)

            def frame_generator():
                for frame_num in range(total_frames):
                    yield compositor.render_frame(frame_num)

            result_path = encode_video(
                frame_generator=frame_generator(),
                timeline=timeline,
                output_path=output_path,
                audio_path=Path(audio_path) if audio_path else None,
                log_daemon=self.log_daemon,
            )

            self._audit("VIDEO_PIPELINE_COMPLETED", {
                "run_id": run_id,
                "output_path": str(result_path),
                "timeline_id": timeline.timeline_id,
                "duration_ms": timeline.total_duration_ms,
                "frame_count": total_frames,
                "clip_count": len(timeline.clips),
                "clips_with_images": loaded,
                "render_backend": "local_direct",
            })

        logger.info("Video pipeline complete: %s", result_path)
        return result_path

    def preview_timeline(
        self,
        timeline: Timeline,
        image_cache: Optional[dict] = None,
        host: str = "127.0.0.1",
        port: int = 8765,
        preview_scale: float = 0.5,
    ):
        """Launch a browser-based preview server for a timeline.

        Opens a local web UI with playback controls, frame scrubbing,
        effect toggles, and resolution scaling. Frames are rendered
        on-demand and streamed via WebSocket.

        Args:
            timeline: Timeline to preview
            image_cache: Pre-loaded images keyed by clip_id (auto-collected if None)
            host: Bind address
            port: Port number
            preview_scale: Initial resolution scale (0.25-1.0)
        """
        from .preview.server import start_preview_server

        if image_cache is None:
            image_cache = self._collect_images(timeline)

        self._audit("VIDEO_PREVIEW_STARTED", {
            "timeline_id": timeline.timeline_id,
            "total_frames": timeline.total_frames,
            "host": host,
            "port": port,
            "preview_scale": preview_scale,
        })

        start_preview_server(
            timeline=timeline,
            image_cache=image_cache,
            host=host,
            port=port,
            preview_scale=preview_scale,
        )

    def compute_render_hash(
        self,
        timeline: Timeline,
        image_cache: Optional[dict] = None,
        sample_frames: int = 10,
    ) -> str:
        """Compute a deterministic hash of sampled rendered frames.

        Given the same timeline and images, this hash will be identical
        across runs. Use it to verify rendering determinism.

        Args:
            timeline: Timeline to hash
            image_cache: Pre-loaded images (auto-collected if None)
            sample_frames: Number of frames to sample

        Returns:
            SHA-256 hex digest
        """
        from .determinism import compute_render_hash

        if image_cache is None:
            image_cache = self._collect_images(timeline)

        return compute_render_hash(timeline, image_cache, sample_frames)

    def verify_render_determinism(
        self,
        timeline: Timeline,
        image_cache: Optional[dict] = None,
        sample_frames: int = 10,
    ) -> dict:
        """Verify that rendering is deterministic by rendering twice and comparing.

        Renders sampled frames twice with fresh FrameCompositor instances and
        compares byte-for-byte. Any mismatch indicates a non-determinism bug.

        Args:
            timeline: Timeline to verify
            image_cache: Pre-loaded images (auto-collected if None)
            sample_frames: Number of frames to sample

        Returns:
            Dict with deterministic (bool), render hashes, and mismatched frames
        """
        from .determinism import verify_determinism

        if image_cache is None:
            image_cache = self._collect_images(timeline)

        def audit_callback(event_type: str, payload: dict):
            self._audit(event_type, payload)

        return verify_determinism(
            timeline, image_cache, sample_frames, audit_callback
        )

    def create_video_multi_platform(
        self,
        platforms: list[str],
        brief: Optional[dict] = None,
        storyboard: Optional[Storyboard] = None,
        timeline: Optional[Timeline] = None,
    ) -> dict[str, Path]:
        """Render a single piece of content for multiple platforms.

        One storyboard/timeline -> multiple output files, each adapted
        for its target platform (aspect ratio, duration, safe zones, codec).

        Provide ONE of: brief, storyboard, or timeline.

        Args:
            platforms: List of platform identifiers
                       (e.g. ["instagram_reel", "tiktok", "youtube_short"])
            brief: Content brief dict (will generate storyboard via LLM)
            storyboard: Pre-built Storyboard
            timeline: Pre-built Timeline

        Returns:
            Dict mapping platform name -> output video Path

        Raises:
            ValueError: If no input provided
            RuntimeError: If FFmpeg unavailable or all renders fail
        """
        from .multi_platform import render_multi_platform

        # Step 1: Get to a storyboard or timeline
        if timeline is None and storyboard is None:
            if brief is None:
                raise ValueError("Must provide brief, storyboard, or timeline")

            from .storyboard import generate_storyboard
            storyboard = generate_storyboard(
                brief=brief,
                claude_client=self.claude_client,
                log_daemon=self.log_daemon,
            )

        # Step 2: Collect images (shared across all platform renders)
        if timeline is not None:
            image_cache = self._collect_images(timeline)
        elif storyboard is not None:
            # Build a temporary timeline to collect images
            from .timeline import storyboard_to_timeline
            temp_timeline = storyboard_to_timeline(storyboard, log_daemon=self.log_daemon)
            image_cache = self._collect_images(temp_timeline)
        else:
            image_cache = {}

        # Step 3: Render for all platforms
        outputs = render_multi_platform(
            platforms=platforms,
            image_cache=image_cache,
            output_dir=self.output_dir,
            storyboard=storyboard,
            timeline=timeline,
            audio_library_dir=self.audio_library_dir,
            log_daemon=self.log_daemon,
        )

        if not outputs:
            raise RuntimeError(
                f"All platform renders failed. Requested: {platforms}"
            )

        logger.info(
            "Multi-platform render complete: %d/%d platforms",
            len(outputs), len(platforms),
        )

        return outputs
