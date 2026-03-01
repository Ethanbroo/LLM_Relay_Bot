"""Base abstractions for asset generation providers.

Provider abstraction is critical for long-term maintainability. AI providers
change pricing, deprecate models, and release better versions constantly.
By hiding provider details behind this interface, you can swap providers
with a single config change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class GenerationRequest:
    """Unified request format across all providers."""
    prompt: str
    negative_prompt: str = (
        "blurry, low quality, distorted, deformed, plastic skin, "
        "too smooth, overexposed, watermark, text, AI-generated looking, "
        "uncanny valley, wrong hands, extra fingers, bad anatomy"
    )
    width: int = 1024
    height: int = 1024
    steps: int = 28         # 28–35 is the sweet spot for Flux quality
    guidance_scale: float = 3.5   # Flux's optimal CFG
    lora_scale: float = 0.85      # Identity vs. creativity balance
                                  # 0.7 = more creative but less consistent
                                  # 0.95 = very consistent but limited variety
    lora_path: Optional[str] = None
    seed: Optional[int] = None    # For reproducibility testing
    num_images: int = 1           # Batch generation (saves cost via parallelization)
    # ComfyUI-specific (ignored by fal.ai providers)
    reference_image_path: Optional[str] = None  # Hero image for PuLID identity lock
    pose_category: Optional[str] = None         # OpenPose category (e.g. "standing_casual")
    pose_image_path: Optional[str] = None       # Direct path to pose skeleton PNG


@dataclass
class GenerationResult:
    """Unified result format across all providers."""
    image_url: str              # URL to download generated image
    image_path: Optional[str] = None    # Local path after download
    generation_time_s: float = 0.0
    cost_usd: float = 0.0
    provider: str = "unknown"
    model: str = "unknown"
    seed_used: Optional[int] = None
    request_hash: Optional[str] = None  # Hash of GenerationRequest for reproducibility


class AbstractAssetGenerator(ABC):
    """
    Base class for all asset generation providers.

    Subclasses implement generate() for their specific provider API.
    This abstraction allows swapping providers without touching pipeline code.
    """

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """
        Generate asset from request.

        Args:
            request: Generation parameters

        Returns:
            GenerationResult with image URL and metadata

        Raises:
            RuntimeError: If generation fails
        """
        pass

    @abstractmethod
    def estimate_cost(self, request: GenerationRequest) -> float:
        """
        Estimate cost in USD for this request.

        Used for budget tracking before actually spending.

        Args:
            request: Generation parameters

        Returns:
            Estimated cost in USD
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if provider is healthy and API key is valid.

        Returns:
            True if provider is ready, False otherwise
        """
        pass

    def download_asset(self, url: str, local_path: str) -> str:
        """
        Download generated asset from URL to local path.

        Default implementation using httpx. Override if provider needs
        special handling (authentication, streaming, etc.).

        Args:
            url: Asset URL from GenerationResult
            local_path: Where to save the file

        Returns:
            Local path to downloaded file
        """
        import httpx
        from pathlib import Path

        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        with httpx.stream("GET", url) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

        return local_path
