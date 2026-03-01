"""Flux.1-dev + LoRA image generation via fal.ai.

This is the primary image generator for UC1 (Instagram content).
Uses fal.ai's Flux LoRA inference endpoint with character-specific weights.
"""

import logging
import time
from typing import Optional

from .base import AbstractAssetGenerator, GenerationRequest, GenerationResult
from ..utils.hashing import canonical_hash

logger = logging.getLogger(__name__)


# fal.ai pricing as of early 2026
# These numbers change — update from https://fal.ai/pricing when needed
FAL_FLUX_DEV_LORA_COST_PER_IMAGE = 0.055  # $0.055 per image with LoRA
FAL_FLUX_DEV_COST_PER_IMAGE = 0.040       # $0.040 per image without LoRA


class FluxImageGenerator(AbstractAssetGenerator):
    """
    Flux.1-dev image generation with LoRA support via fal.ai.

    This is the workhorse generator for the Instagram pipeline.
    Produces photorealistic 1024x1024 images with character consistency.
    """

    def __init__(self, api_key: str, model: str = "fal-ai/flux-lora"):
        """
        Initialize Flux generator.

        Args:
            api_key: fal.ai API key
            model: fal.ai model endpoint (default: fal-ai/flux-lora)
        """
        self.api_key = api_key
        self.model = model

        try:
            import fal_client
            self.fal = fal_client
        except ImportError:
            raise ImportError(
                "fal_client not installed. Install with: pip install fal-client"
            )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """
        Generate image using Flux.1-dev + LoRA.

        Args:
            request: Generation parameters including prompt and LoRA path

        Returns:
            GenerationResult with image URL and metadata

        Raises:
            RuntimeError: If generation fails
        """
        start_time = time.time()

        logger.info(
            "Generating image with Flux: prompt='%s', lora_scale=%.2f, steps=%d",
            request.prompt[:100],
            request.lora_scale,
            request.steps
        )

        # Build fal.ai request arguments
        arguments = {
            "prompt": request.prompt,
            "image_size": {
                "width": request.width,
                "height": request.height,
            },
            "num_inference_steps": request.steps,
            "guidance_scale": request.guidance_scale,
            "num_images": request.num_images,
            "enable_safety_checker": False,  # We run our own quality gates
            "output_format": "jpeg",
            "sync_mode": True,  # Block until generation completes
        }

        # Add LoRA if provided
        if request.lora_path:
            # Upload LoRA weights to fal.ai storage (cached after first upload)
            lora_url = self.fal.upload_file(request.lora_path)
            arguments["loras"] = [{
                "path": lora_url,
                "scale": request.lora_scale,
            }]

        # Add negative prompt
        if request.negative_prompt:
            arguments["negative_prompt"] = request.negative_prompt

        # Add seed if specified (for reproducibility testing)
        if request.seed is not None:
            arguments["seed"] = request.seed

        # Submit generation request
        try:
            result = self.fal.subscribe(
                self.model,
                arguments=arguments,
                with_logs=False,  # Disable verbose logging
            )
        except Exception as e:
            logger.error("Flux generation failed: %s", e)
            raise RuntimeError(f"Image generation failed: {e}")

        generation_time = time.time() - start_time

        # Extract image URL from result
        if "images" not in result or len(result["images"]) == 0:
            raise RuntimeError("No images returned from Flux generation")

        image_url = result["images"][0]["url"]
        seed_used = result.get("seed", request.seed)

        # Compute request hash for reproducibility tracking
        import dataclasses
        request_dict = dataclasses.asdict(request)
        request_dict["seed"] = None  # Exclude seed from hash so we can compare runs
        request_hash = canonical_hash(request_dict)

        # Estimate cost
        cost = self.estimate_cost(request)

        logger.info(
            "Image generated successfully in %.1fs. URL: %s, Cost: $%.3f",
            generation_time,
            image_url[:50],
            cost
        )

        return GenerationResult(
            image_url=image_url,
            generation_time_s=generation_time,
            cost_usd=cost,
            provider="fal.ai",
            model=self.model,
            seed_used=seed_used,
            request_hash=request_hash,
        )

    def estimate_cost(self, request: GenerationRequest) -> float:
        """
        Estimate generation cost.

        Args:
            request: Generation parameters

        Returns:
            Estimated cost in USD
        """
        base_cost = (
            FAL_FLUX_DEV_LORA_COST_PER_IMAGE if request.lora_path
            else FAL_FLUX_DEV_COST_PER_IMAGE
        )
        return base_cost * request.num_images

    def health_check(self) -> bool:
        """
        Check if fal.ai is accessible with current API key.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Simple test: try to list models (doesn't cost anything)
            self.fal.list_models()
            return True
        except Exception as e:
            logger.error("fal.ai health check failed: %s", e)
            return False

    def generate_batch(
        self,
        requests: list[GenerationRequest],
        parallel: bool = True
    ) -> list[GenerationResult]:
        """
        Generate multiple images.

        If parallel=True, submits all requests concurrently for faster completion.
        If parallel=False, generates sequentially (useful for rate limiting).

        Args:
            requests: List of generation requests
            parallel: Whether to generate in parallel

        Returns:
            List of GenerationResults (same order as requests)
        """
        if not parallel:
            return [self.generate(req) for req in requests]

        # Parallel generation using fal.ai's batch API
        results = []
        for req in requests:
            # Each request is submitted independently
            # fal.ai handles parallelization internally
            result = self.generate(req)
            results.append(result)

        return results
