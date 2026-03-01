"""Video generation with budget awareness and quality gates.

Main entry point for generating video content. Iterates through the
provider fallback chain, runs quality gates, and tracks cost.
Falls back to static image if budget exhausted or all providers fail.

Supports multiple providers: fal.ai/Kling, SiliconFlow/Wan2.2, FFmpeg PiP.
"""

import logging
import os
import tempfile
import time
from typing import Optional

from ..brief.models import ContentFormat, VideoIntent
from ..config.budget import BudgetConfig, CostTracker
from ..character.models import CharacterProfile
from .video_provider_registry import VideoProviderRegistry, VideoGenerationResult

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    """All providers exhausted or budget blown."""
    pass


class VideoGenerator:
    """Generates video content via multiple providers.

    Handles:
    - Provider fallback chain (primary → fallback → static_image)
    - Multi-provider dispatch (fal.ai, SiliconFlow, FFmpeg)
    - Budget tracking per post
    - Video file download
    - Integration with quality gate orchestrator
    """

    def __init__(
        self,
        character: Optional[CharacterProfile] = None,
        budget_config: Optional[BudgetConfig] = None,
    ):
        self.character = character
        self.registry = VideoProviderRegistry()
        self.budget_config = budget_config or BudgetConfig()
        self._siliconflow_client = None

    @property
    def siliconflow_client(self):
        if self._siliconflow_client is None:
            from .siliconflow_client import SiliconFlowClient
            self._siliconflow_client = SiliconFlowClient()
        return self._siliconflow_client

    async def generate(self, intent: VideoIntent) -> VideoGenerationResult:
        """Generate video for a given intent. No quality gates — raw generation."""
        chain = self.registry.get_endpoint_chain(intent.content_format)

        for tier_name, endpoint in chain:
            if endpoint == "static_image":
                return await self._fallback_to_static(intent)

            try:
                result = await self._call_endpoint(endpoint, intent)
                result.provider_tier = tier_name
                result.endpoint = endpoint
                return result
            except Exception as e:
                logger.warning(
                    "%s failed on %s: %s", tier_name, endpoint, e
                )
                continue

        raise GenerationError(
            f"All providers exhausted for {intent.content_format.value}"
        )

    async def generate_with_budget(
        self, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Generate video with budget tracking and retry logic."""
        tracker = CostTracker(intent.content_format.value, self.budget_config)
        chain = self.registry.get_endpoint_chain(intent.content_format)

        for tier_name, endpoint in chain:
            if endpoint == "static_image":
                break

            for attempt in range(self.budget_config.MAX_RETRIES_PER_TIER):
                if not tracker.can_afford(endpoint, intent.duration):
                    logger.warning(
                        "Budget exhausted at %s, attempt %d. "
                        "Spent: $%.2f/$%.2f",
                        tier_name, attempt,
                        tracker.total_spent, tracker.budget_cap
                    )
                    break  # Move to next tier

                try:
                    result = await self._call_endpoint(endpoint, intent)
                    tracker.record_attempt(endpoint, intent.duration, True)
                    result.provider_tier = tier_name
                    result.endpoint = endpoint
                    result.cost_summary = tracker.summary()
                    return result
                except Exception as e:
                    tracker.record_attempt(endpoint, intent.duration, False)
                    logger.warning(
                        "%s attempt %d failed on %s: %s",
                        tier_name, attempt + 1, endpoint, e
                    )
                    continue

        # All tiers exhausted or budget blown
        logger.warning(
            "Falling back to static image. Cost summary: %s",
            tracker.summary()
        )
        result = await self._fallback_to_static(intent)
        result.cost_summary = tracker.summary()
        return result

    async def _call_endpoint(
        self, endpoint: str, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Route to the appropriate provider based on endpoint."""
        payload = self.registry.build_request_payload(endpoint, intent)
        provider = payload.pop("_provider", "fal")

        if provider == "siliconflow":
            return await self._call_siliconflow(payload, intent)
        elif provider == "ffmpeg":
            return await self._call_ffmpeg_composite(payload, intent)
        else:
            return await self._call_fal(endpoint, payload, intent)

    async def _call_fal(
        self, endpoint: str, payload: dict, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Call a fal.ai/Kling endpoint."""
        start_time = time.time()

        logger.info("Calling %s with payload keys: %s", endpoint, list(payload.keys()))

        import asyncio
        import fal_client
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: fal_client.subscribe(
                endpoint,
                arguments=payload,
                with_logs=True,
            )
        )

        generation_time = time.time() - start_time

        # Extract video URL from response
        video_url = ""
        if isinstance(response, dict):
            video_data = response.get("video", response.get("output", {}))
            if isinstance(video_data, dict):
                video_url = video_data.get("url", "")
            elif isinstance(video_data, str):
                video_url = video_data
            if not video_url:
                video_url = response.get("video_url", response.get("url", ""))

        if not video_url:
            raise GenerationError(
                f"No video URL in response from {endpoint}: {response}"
            )

        video_path = await self._download_video(video_url)

        # Estimate cost
        cost_rate = self.budget_config.COST_PER_SECOND.get(endpoint, 0.20)
        estimated_cost = cost_rate * intent.duration

        return VideoGenerationResult(
            video_url=video_url,
            video_path=video_path,
            generation_time_s=generation_time,
            cost_usd=estimated_cost,
            provider="fal.ai",
            endpoint=endpoint,
            duration_seconds=float(intent.duration),
            has_audio=intent.native_audio,
        )

    async def _call_siliconflow(
        self, payload: dict, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Generate video via SiliconFlow/Wan2.2."""
        model_key = payload["model_key"]
        start = time.time()

        if model_key == "i2v" and payload.get("image_url"):
            video_url = await self.siliconflow_client.generate_i2v(
                prompt=payload["prompt"],
                image_url=payload["image_url"],
                size=payload["size"],
            )
        else:
            video_url = await self.siliconflow_client.generate_t2v(
                prompt=payload["prompt"],
                size=payload["size"],
            )

        local_path = await self.siliconflow_client.download_video(
            video_url,
            tempfile.mktemp(suffix=".mp4", prefix="wan2_"),
        )

        return VideoGenerationResult(
            video_url=video_url,
            video_path=local_path,
            generation_time_s=time.time() - start,
            cost_usd=0.29,
            provider="siliconflow",
            endpoint=f"siliconflow/wan2.2-{model_key}",
            duration_seconds=float(intent.duration),
            has_audio=False,
        )

    async def _call_ffmpeg_composite(
        self, payload: dict, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Generate AI character reaction clip, then PiP composite over gameplay."""
        from ..gameplay.pip_compositor import PiPCompositor

        compositor = PiPCompositor()
        start = time.time()

        # Step 1: Generate character reaction clip via cheapest provider
        reaction_payload = {
            "_provider": "siliconflow",
            "model_key": "i2v",
            "prompt": payload["prompt"],
            "image_url": payload["character_refs"][0] if payload["character_refs"] else None,
            "size": "720x1280",
        }
        reaction_clip = await self._call_siliconflow(reaction_payload, intent)

        # Step 2: FFmpeg PiP overlay
        output_path = tempfile.mktemp(suffix=".mp4", prefix="gameplay_pip_")
        compositor.overlay(
            background_video=payload["gameplay_path"],
            pip_video=reaction_clip.video_path,
            output_path=output_path,
            pip_position="bottom_right",
            pip_scale=0.30,
            target_aspect=payload["aspect_ratio"],
        )

        return VideoGenerationResult(
            video_path=output_path,
            generation_time_s=time.time() - start,
            cost_usd=0.29,
            provider="ffmpeg+siliconflow",
            endpoint="ffmpeg_pip_composite",
            duration_seconds=float(intent.duration),
            has_audio=True,
        )

    async def _download_video(self, url: str) -> str:
        """Download video from URL to local temp file."""
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=120.0)
            response.raise_for_status()

            suffix = ".mp4"
            if "webm" in response.headers.get("content-type", ""):
                suffix = ".webm"

            tmp = tempfile.NamedTemporaryFile(
                suffix=suffix, prefix="kling_video_", delete=False
            )
            tmp.write(response.content)
            tmp.close()
            logger.info("Downloaded video to %s (%d bytes)", tmp.name, len(response.content))
            return tmp.name

    async def _fallback_to_static(
        self, intent: VideoIntent
    ) -> VideoGenerationResult:
        """Fall back to static image using existing Flux pipeline."""
        logger.info(
            "Falling back to static image for %s",
            intent.content_format.value
        )
        return VideoGenerationResult(
            is_static_fallback=True,
            provider_tier="last_resort",
            endpoint="static_image",
        )
