"""ComfyUI-based image generation with PuLID identity lock + ControlNet OpenPose.

Replaces FluxImageGenerator (fal.ai) with local/cloud ComfyUI execution.
Implements AbstractAssetGenerator so it plugs directly into ProviderRegistry.

Uses PuLID for face identity consistency and optionally ControlNet OpenPose
for body positioning. All generation parameters (sampler, scheduler, CFG,
negative prompts, etc.) are fully controllable via workflow templates.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from .base import AbstractAssetGenerator, GenerationRequest, GenerationResult
from .comfyui_client import ComfyUIClient
from .workflow_templates import WorkflowTemplateEngine, WorkflowSlots
from ..reference.vault import ReferenceVault
from ..reference.pose_library import PoseLibrary
from ..utils.hashing import canonical_hash

logger = logging.getLogger(__name__)

# Cost estimates per image
COST_LOCAL = 0.00       # Mac local: electricity only
COST_CLOUD = 0.03       # RunPod A100 spot: ~$0.44/hr, ~4 min/image


class ComfyUIImageGenerator(AbstractAssetGenerator):
    """PuLID + ControlNet OpenPose image generation via ComfyUI.

    Drop-in replacement for FluxImageGenerator in ProviderRegistry.
    """

    def __init__(
        self,
        comfyui_client: ComfyUIClient,
        template_engine: WorkflowTemplateEngine,
        reference_vault: ReferenceVault,
        pose_library: Optional[PoseLibrary] = None,
        is_local: bool = True,
        output_dir: str = "output/comfyui",
    ):
        self.client = comfyui_client
        self.templates = template_engine
        self.vault = reference_vault
        self.poses = pose_library
        self.is_local = is_local
        self.output_dir = output_dir

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Synchronous generate matching AbstractAssetGenerator interface.

        Bridges to async ComfyUI client via asyncio. If an event loop is
        already running, uses run_in_executor to avoid nested loop errors.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context — schedule as a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._generate_async(request))
                return future.result()
        else:
            return asyncio.run(self._generate_async(request))

    async def _generate_async(self, request: GenerationRequest) -> GenerationResult:
        """Actual async generation logic."""
        start_time = time.time()

        # 1. Ensure reference image is uploaded
        ref_filename = ""
        if request.reference_image_path:
            ref_filename = await self.vault.ensure_uploaded(
                request.reference_image_path,
            )

        # 2. Determine pose image
        pose_filename = ""
        if request.pose_image_path:
            # Direct pose path provided
            if self.poses:
                pose_filename = await self.poses.ensure_uploaded(
                    request.pose_image_path, self.client,
                )
        elif request.pose_category and self.poses:
            # Category provided — look up from library
            pose_path = self.poses.get_random_pose(request.pose_category)
            if pose_path:
                pose_filename = await self.poses.ensure_uploaded(
                    pose_path, self.client,
                )

        # 3. Select template and build workflow
        has_pose = bool(pose_filename)
        template_name = self.templates.select_template(has_pose)

        slots = WorkflowSlots(
            positive_prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            reference_image=ref_filename,
            pose_image=pose_filename,
            seed=request.seed if request.seed is not None else -1,
            steps=request.steps,
            cfg_scale=request.guidance_scale,
            width=request.width,
            height=request.height,
            pulid_strength=request.lora_scale,  # Reuse lora_scale for PuLID weight
        )

        workflow = self.templates.fill_template(template_name, slots)

        # 4. Execute workflow
        logger.info(
            "Generating via ComfyUI (%s) | template=%s | pose=%s",
            "local" if self.is_local else "cloud",
            template_name,
            bool(pose_filename),
        )

        image_paths = await self.client.execute_workflow(
            workflow, output_dir=self.output_dir,
        )

        generation_time = time.time() - start_time
        image_path = image_paths[0] if image_paths else None

        # 5. Extract seed from workflow (was resolved by template engine)
        seed_used = workflow.get("13", {}).get("inputs", {}).get("seed")

        # 6. Compute request hash
        request_hash = canonical_hash({
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "seed": seed_used,
            "steps": request.steps,
            "cfg": request.guidance_scale,
            "template": template_name,
        })

        return GenerationResult(
            image_url="",  # Local generation, no URL
            image_path=image_path,
            generation_time_s=round(generation_time, 2),
            cost_usd=COST_LOCAL if self.is_local else COST_CLOUD,
            provider="comfyui_local" if self.is_local else "comfyui_cloud",
            model="flux-dev-pulid",
            seed_used=seed_used,
            request_hash=request_hash,
        )

    def estimate_cost(self, request: GenerationRequest) -> float:
        """Estimate cost in USD."""
        per_image = COST_LOCAL if self.is_local else COST_CLOUD
        return per_image * request.num_images

    def health_check(self) -> bool:
        """Check if ComfyUI server is reachable."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            return True  # Can't check synchronously from async context
        return asyncio.run(self.client.health_check())

    async def generate_batch(
        self,
        requests: list[GenerationRequest],
    ) -> list[GenerationResult]:
        """Generate multiple images. Runs sequentially since ComfyUI
        processes one workflow at a time."""
        results = []
        for request in requests:
            result = await self._generate_async(request)
            results.append(result)
        return results
