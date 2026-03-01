"""LoRA training workflow for character identity encoding.

This module handles the critical one-time operation of converting a reference
image set into Flux LoRA model weights that encode the character's identity.

IMPORTANT: This requires fal.ai API access. Training costs ~$3-5 per run and
takes 30-90 minutes. Always validate training images before submitting.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from .models import CharacterProfile
from ..utils.hashing import sha256_file
from ..utils.image_utils import validate_training_image

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TRAINING CONFIGURATION CONSTANTS
# These values are derived from community best practices for
# Flux LoRA training as of early 2026. Do not change without
# re-validating identity consistency in your output.
# ─────────────────────────────────────────────

FLUX_LORA_TRAINING_CONFIG = {
    "steps": 1000,              # Sweet spot: 800–1200 for faces. <800 = undertrained,
                                # >1500 = overfitting (only generates one pose)
    "lora_rank": 64,            # Higher rank = more capacity = better face capture.
                                # 64 is the proven sweet spot for identity LoRAs.
    "learning_rate": 0.0001,    # Standard for Flux. Do not tune unless you see artifacts.
    "batch_size": 1,            # fal.ai enforces this for face training
    "resolution": 1024,         # Flux native resolution. All training images must be
                                # square-cropped to 1024x1024 before upload.
    "caption_strategy": "instance_prompt",  # Use trigger word, not auto-captioning.
                                            # Auto-captioning confuses the identity.
    "optimizer": "adamw8bit",               # Memory-efficient, same quality as adamw
    "lr_scheduler": "cosine_with_restarts",
    "gradient_checkpointing": True,
}

TRAINING_IMAGE_REQUIREMENTS = {
    "min_count": 20,            # Absolute minimum. 30–40 is recommended.
    "recommended_count": 35,
    "resolution": 1024,         # Must be square
    "format": "PNG",            # PNG only for training — no JPEG compression artifacts
    "required_angles": [        # Your dataset must cover all of these
        "front_neutral",        # Looking straight ahead, neutral expression
        "front_smile",          # Looking straight ahead, natural smile
        "front_laugh",          # Open mouth laugh — captures teeth gap if present
        "three_quarter_left",   # 45° left
        "three_quarter_right",  # 45° right
        "profile_left",         # 90° left
        "profile_right",        # 90° right
        "looking_down",         # Head slightly tilted down — common Instagram pose
        "looking_up",           # Head slightly tilted up — common Instagram pose
    ],
    "required_lighting_conditions": [
        "bright_natural",       # Clear day, outdoor
        "overcast_natural",     # Cloudy/diffused outdoor
        "indoor_window",        # Window light, indoors
        "golden_hour",          # Warm directional light
        "low_light_indoor",     # Indoor evening — tests shadow fidelity
    ],
    "required_expressions": [
        "neutral", "smile", "laugh", "thoughtful", "slight_smirk"
    ],
    "background_variety": True, # Mix of backgrounds — prevents background leakage
                                # into identity. At least 5 distinct backgrounds.
}


class LoRATrainer:
    """
    Manages the one-time LoRA training workflow for a character.

    Note: This requires fal_client package and FAL_API_KEY environment variable.
    Install with: pip install fal-client
    """

    def __init__(self, character_profile: CharacterProfile, api_key: Optional[str] = None):
        """
        Initialize LoRA trainer.

        Args:
            character_profile: Character to train LoRA for
            api_key: Optional fal.ai API key (uses FAL_API_KEY env var if not provided)

        Raises:
            ValueError: If no API key available
        """
        self.character = character_profile
        self.api_key = api_key or os.environ.get("FAL_API_KEY")

        if not self.api_key:
            raise ValueError(
                "FAL_API_KEY not found. Set environment variable or pass api_key parameter."
            )

        self.output_dir = Path(f"data/characters/{character_profile.character_id}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def validate_training_images(self, image_dir: Path) -> Tuple[bool, list[str]]:
        """
        Validates training images before spending compute on training.

        Checks:
        - Minimum image count
        - All images are 1024x1024 PNG
        - RGB color mode (not RGBA, not grayscale)

        Args:
            image_dir: Directory containing training images

        Returns:
            Tuple of (is_valid, list_of_issues)
            Always call this before train().
        """
        issues = []
        images = list(image_dir.glob("*.png"))

        if len(images) < TRAINING_IMAGE_REQUIREMENTS["min_count"]:
            issues.append(
                f"Only {len(images)} images found. "
                f"Minimum is {TRAINING_IMAGE_REQUIREMENTS['min_count']}. "
                f"Recommended: {TRAINING_IMAGE_REQUIREMENTS['recommended_count']}."
            )

        for img_path in images:
            is_valid, error_msg = validate_training_image(str(img_path))
            if not is_valid:
                issues.append(f"{img_path.name}: {error_msg}")

        # Check for sufficient variety (heuristic)
        if len(images) > 0 and len(images) < TRAINING_IMAGE_REQUIREMENTS["recommended_count"]:
            issues.append(
                f"Warning: {len(images)} images may not provide sufficient pose/lighting variety. "
                f"Recommended: {TRAINING_IMAGE_REQUIREMENTS['recommended_count']}+ images."
            )

        return (len(issues) == 0), issues

    def train(self, image_dir: Path, blocking: bool = True) -> Dict[str, Any]:
        """
        Submits training job to fal.ai.

        This is a long-running operation (30–90 minutes). By default, this
        function blocks until training completes. Set blocking=False to
        return immediately with a job ID that you can poll.

        Args:
            image_dir: Directory containing 1024x1024 PNG training images
            blocking: If True, blocks until training completes (default)

        Returns:
            Dict containing:
                - weights_path: Local path to saved LoRA weights
                - weights_hash: SHA-256 hash of weights file
                - job_id: fal.ai job ID (for reference)
                - training_time_seconds: Total training duration

        Raises:
            ValueError: If images fail validation
            RuntimeError: If training fails
            ImportError: If fal_client not installed
        """
        # Validate images first
        is_valid, issues = self.validate_training_images(image_dir)
        if not is_valid:
            error_msg = "Training image validation failed:\n" + "\n".join(f"  - {issue}" for issue in issues)
            raise ValueError(error_msg)

        try:
            import fal_client
        except ImportError:
            raise ImportError(
                "fal_client not installed. Install with: pip install fal-client"
            )

        logger.info(
            "Starting LoRA training for character %s. Using %d training images.",
            self.character.character_id,
            len(list(image_dir.glob("*.png"))),
        )

        start_time = time.time()

        # Upload training images to fal.ai storage
        image_urls = []
        for img_path in sorted(image_dir.glob("*.png")):
            logger.debug("Uploading %s to fal.ai storage", img_path.name)
            url = fal_client.upload_file(str(img_path))
            image_urls.append(url)

        logger.info("Uploaded %d training images to fal.ai storage.", len(image_urls))

        # Build training config, injecting character-specific trigger word
        config = {**FLUX_LORA_TRAINING_CONFIG}
        config["trigger_word"] = self.character.lora_trigger_word

        logger.info(
            "Submitting training job with config: steps=%d, rank=%d, lr=%f, trigger_word=%s",
            config["steps"],
            config["lora_rank"],
            config["learning_rate"],
            config["trigger_word"],
        )

        # Submit training job — fal.ai model: "fal-ai/flux-lora-fast-training"
        if blocking:
            result = fal_client.subscribe(
                "fal-ai/flux-lora-fast-training",
                arguments={
                    "images_data_url": image_urls,
                    "trigger_word": config["trigger_word"],
                    "steps": config["steps"],
                    "lora_rank": config["lora_rank"],
                    "learning_rate": config["learning_rate"],
                    "batch_size": config["batch_size"],
                    "resolution": config["resolution"],
                    "optimizer": config["optimizer"],
                },
                with_logs=True,
                on_queue_update=lambda update: logger.info("Training status: %s", update),
            )
        else:
            # Non-blocking mode: return job ID immediately
            job = fal_client.submit(
                "fal-ai/flux-lora-fast-training",
                arguments={
                    "images_data_url": image_urls,
                    "trigger_word": config["trigger_word"],
                    "steps": config["steps"],
                    "lora_rank": config["lora_rank"],
                    "learning_rate": config["learning_rate"],
                    "batch_size": config["batch_size"],
                    "resolution": config["resolution"],
                    "optimizer": config["optimizer"],
                },
            )
            logger.info("Training job submitted. Job ID: %s", job.request_id)
            return {
                "job_id": job.request_id,
                "status": "submitted",
                "message": "Training job submitted. Poll with job ID to check status.",
            }

        # Download and persist the weights
        weights_url = result["diffusers_lora_file"]["url"]
        weights_path = self.output_dir / "lora_weights.safetensors"

        logger.info("Downloading trained LoRA weights from %s", weights_url)

        import httpx
        with httpx.stream("GET", weights_url) as r:
            r.raise_for_status()
            with open(weights_path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

        weights_hash = sha256_file(str(weights_path))
        training_time = time.time() - start_time

        logger.info(
            "LoRA training complete. Weights saved to %s. Hash: %s. Time: %.1f minutes.",
            weights_path,
            weights_hash,
            training_time / 60,
        )

        # Persist the hash alongside weights for drift detection
        hash_file = self.output_dir / "lora_weights_hash.txt"
        hash_file.write_text(weights_hash)

        return {
            "weights_path": str(weights_path),
            "weights_hash": weights_hash,
            "job_id": result.get("request_id", "unknown"),
            "training_time_seconds": training_time,
        }

    def verify_weights_integrity(self, weights_path: str, expected_hash: str) -> bool:
        """
        Verify LoRA weights file has not been corrupted or modified.

        Used for drift detection and integrity checks before production use.

        Args:
            weights_path: Path to LoRA weights file
            expected_hash: Expected SHA-256 hash

        Returns:
            True if hash matches, False otherwise
        """
        actual_hash = sha256_file(weights_path)
        return actual_hash == expected_hash
