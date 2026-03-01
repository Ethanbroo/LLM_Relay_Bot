"""Still-to-video animation pipeline.

Takes a validated, approved still image and animates it using Wan 2.2 I2V.
Only processes images that have passed StillImageValidationGate.

Uses existing SiliconFlowClient.generate_i2v() for the actual generation.
A future iteration could add RunPod-hosted ComfyUI with Wan 2.2 as an
alternative backend using the same ComfyUIClient pattern.
"""

import logging
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..generation.siliconflow_client import SiliconFlowClient
from ..validation.approval_store import ApprovalStore

logger = logging.getLogger(__name__)


@dataclass
class AnimationResult:
    """Result from still-to-video animation."""

    video_path: str = ""
    video_url: str = ""
    source_image_path: str = ""
    source_image_hash: str = ""
    motion_prompt: str = ""
    generation_time_s: float = 0.0
    cost_usd: float = 0.29          # SiliconFlow flat rate
    provider: str = "siliconflow"
    model: str = "Wan-AI/Wan2.2-I2V-A14B"
    duration_seconds: float = 5.0
    success: bool = True
    error: str = ""


# Scene-specific motion prompts — subtle, natural movements
MOTION_PROMPTS: dict[str, list[str]] = {
    "cafe": [
        "woman picks up coffee cup and takes a sip, natural movement",
        "woman looks out window then turns back with slight smile",
        "woman tucks hair behind ear, casual candid moment",
    ],
    "pool": [
        "woman adjusts sunglasses and looks around poolside",
        "woman dips feet in pool water, gentle ripples",
        "woman reaches for drink on side table, relaxed motion",
    ],
    "bed": [
        "woman stretches arms above head, morning wake up",
        "woman reaches for phone on nightstand, casual morning",
        "woman adjusts pillows and settles back, cozy movement",
    ],
    "kitchen": [
        "woman stirs pot on stove, natural cooking motion",
        "woman chops vegetables on cutting board, focused",
        "woman opens refrigerator and reaches inside",
    ],
    "couch": [
        "woman adjusts position on couch, gets comfortable",
        "woman picks up book from coffee table, starts reading",
        "woman laughs at phone screen, genuine expression",
    ],
    "beach": [
        "wind blows through hair, woman brushes it aside",
        "woman walks slowly along waterline, waves around feet",
        "woman looks out at ocean, peaceful contemplation",
    ],
    "gym": [
        "woman picks up water bottle and takes a drink",
        "woman adjusts ponytail before next set",
        "woman wipes face with towel, post-workout",
    ],
}


class StillToVideoAnimator:
    """Animates validated still images to short video clips.

    ONLY processes images that have passed the StillImageValidationGate
    and have status='approved' in ApprovalStore.
    """

    def __init__(
        self,
        siliconflow_client: Optional[SiliconFlowClient] = None,
        approval_store: Optional[ApprovalStore] = None,
    ):
        self._client = siliconflow_client
        self.store = approval_store

    @property
    def client(self) -> SiliconFlowClient:
        if self._client is None:
            self._client = SiliconFlowClient()
        return self._client

    async def animate(
        self,
        image_path: str,
        motion_prompt: str = "",
        scene: str = "",
        duration: int = 5,
        size: str = "720x1280",
    ) -> AnimationResult:
        """Animate a still image to video.

        Args:
            image_path: Path to approved still image
            motion_prompt: Motion description. If empty, picks from
                          scene-specific defaults.
            scene: Scene type for auto-selecting motion prompt
            duration: Video duration in seconds
            size: Output video dimensions

        Returns:
            AnimationResult with video path and metadata
        """
        start_time = time.time()

        # Select motion prompt
        if not motion_prompt and scene:
            prompts = MOTION_PROMPTS.get(scene, [])
            motion_prompt = random.choice(prompts) if prompts else ""

        if not motion_prompt:
            motion_prompt = "subtle natural movement, slight head turn and blink"

        # Upload image to accessible URL via fal.ai
        # SiliconFlow I2V needs a public URL, not a local path
        import fal_client
        image_url = fal_client.upload_file(image_path)

        logger.info(
            "Animating %s with prompt: %s",
            Path(image_path).name, motion_prompt[:80],
        )

        try:
            video_url = await self.client.generate_i2v(
                prompt=motion_prompt,
                image_url=image_url,
                size=size,
            )

            # Download video
            output_path = tempfile.mktemp(
                suffix=".mp4", prefix="animated_still_",
            )
            await self.client.download_video(video_url, output_path)

            generation_time = time.time() - start_time

            result = AnimationResult(
                video_path=output_path,
                video_url=video_url,
                source_image_path=image_path,
                motion_prompt=motion_prompt,
                generation_time_s=round(generation_time, 2),
                cost_usd=0.29,
                duration_seconds=float(duration),
            )

        except Exception as e:
            logger.error("Animation failed: %s", e)
            result = AnimationResult(
                source_image_path=image_path,
                motion_prompt=motion_prompt,
                generation_time_s=round(time.time() - start_time, 2),
                success=False,
                error=str(e),
            )

        return result

    async def animate_approved_batch(
        self,
        character_id: str,
        batch_size: int = 5,
    ) -> list[AnimationResult]:
        """Process a batch of approved stills from the approval store.

        Fetches approved-but-not-yet-animated images and animates them.
        Updates the approval store with video paths on success.

        Args:
            character_id: Which character's images to process
            batch_size: Max images to animate in this batch

        Returns:
            List of AnimationResults
        """
        if not self.store:
            raise RuntimeError(
                "ApprovalStore required for batch animation. "
                "Pass approval_store to constructor."
            )

        approved = self.store.get_approved_for_animation(
            character_id, limit=batch_size,
        )

        if not approved:
            logger.info("No approved images pending animation for %s", character_id)
            return []

        results = []
        for record in approved:
            result = await self.animate(
                image_path=record.image_path,
                scene=record.scene,
            )

            if result.success:
                self.store.mark_animated(
                    record.image_hash, result.video_path,
                )
                logger.info(
                    "Animated %s -> %s",
                    Path(record.image_path).name,
                    Path(result.video_path).name,
                )
            else:
                logger.warning(
                    "Failed to animate %s: %s",
                    record.image_path, result.error,
                )

            results.append(result)

        return results
