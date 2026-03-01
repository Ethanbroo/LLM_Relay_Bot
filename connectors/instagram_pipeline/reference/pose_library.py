"""Pose library for ControlNet OpenPose body positioning.

Stores and retrieves OpenPose skeleton PNG images organized by scene
category. These are pre-created once (via DWPose preprocessor on stock
photos or manually drawn) and reused across all generation runs.

Directory structure:
    poses/
      standing_casual/
        variant_01.png
        variant_02.png
      sitting_relaxed/
        variant_01.png
      ...
"""

import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Mapping from ShotSpec framing/action to pose categories
FRAMING_TO_CATEGORY = {
    "portrait_tight": "portrait_close",
    "portrait_medium": "portrait_close",
    "half_body": "half_body_gesture",
    "half_body_gesture": "half_body_gesture",
    "full_body": "full_body_environmental",
    "full_body_standing": "standing_casual",
    "full_body_walking": "walking",
    "environmental": "full_body_environmental",
}

ACTION_TO_CATEGORY = {
    "sitting": "sitting_relaxed",
    "sitting at cafe": "sitting_relaxed",
    "lounging": "lounging",
    "lying down": "lounging",
    "standing": "standing_casual",
    "walking": "walking",
    "cooking": "standing_casual",
    "exercising": "standing_casual",
    "leaning": "leaning",
}


class PoseLibrary:
    """Manages OpenPose skeleton PNGs for ControlNet workflows.

    Pose skeletons are created once and reused. The library provides
    lookup by category and random variant selection.
    """

    CATEGORIES = (
        "standing_casual",
        "sitting_relaxed",
        "walking",
        "portrait_close",
        "half_body_gesture",
        "full_body_environmental",
        "lounging",
        "leaning",
    )

    def __init__(self, library_dir: str | Path):
        self.library_dir = Path(library_dir)

    def get_pose(self, category: str, variant: int = 0) -> Optional[str]:
        """Get path to a specific pose skeleton PNG.

        Args:
            category: Pose category (e.g. "standing_casual")
            variant: 0-based index of variant within category

        Returns:
            Absolute path to pose PNG, or None if not found
        """
        cat_dir = self.library_dir / category
        if not cat_dir.exists():
            logger.warning("Pose category directory not found: %s", cat_dir)
            return None

        variants = sorted(cat_dir.glob("*.png"))
        if not variants:
            logger.warning("No pose PNGs in category: %s", category)
            return None

        if variant >= len(variants):
            variant = 0

        return str(variants[variant])

    def get_random_pose(self, category: str) -> Optional[str]:
        """Get a random variant from a category."""
        cat_dir = self.library_dir / category
        if not cat_dir.exists():
            return None

        variants = list(cat_dir.glob("*.png"))
        if not variants:
            return None

        return str(random.choice(variants))

    def list_categories(self) -> list[str]:
        """List available pose categories (directories that contain PNGs)."""
        categories = []
        if not self.library_dir.exists():
            return categories
        for d in sorted(self.library_dir.iterdir()):
            if d.is_dir() and list(d.glob("*.png")):
                categories.append(d.name)
        return categories

    def list_variants(self, category: str) -> list[str]:
        """List variant PNG filenames in a category."""
        cat_dir = self.library_dir / category
        if not cat_dir.exists():
            return []
        return [p.name for p in sorted(cat_dir.glob("*.png"))]

    def map_shot_spec_to_category(
        self,
        framing: str = "",
        action: str = "",
    ) -> str:
        """Map ShotSpec framing and action fields to a pose category.

        Tries framing first (more specific), falls back to action,
        then defaults to "standing_casual".
        """
        # Normalize
        framing_lower = framing.lower().strip()
        action_lower = action.lower().strip()

        # Try framing match
        if framing_lower in FRAMING_TO_CATEGORY:
            return FRAMING_TO_CATEGORY[framing_lower]

        # Try action match (partial matching)
        for keyword, category in ACTION_TO_CATEGORY.items():
            if keyword in action_lower:
                return category

        return "standing_casual"

    async def ensure_uploaded(
        self,
        pose_path: str,
        comfyui_client,
    ) -> str:
        """Upload pose PNG to ComfyUI input/ if needed.

        Args:
            pose_path: Local path to pose skeleton PNG
            comfyui_client: ComfyUIClient instance

        Returns:
            ComfyUI filename to use in workflow JSON
        """
        return await comfyui_client.upload_image(
            pose_path,
            subfolder="poses",
        )
