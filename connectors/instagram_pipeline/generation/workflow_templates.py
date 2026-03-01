"""ComfyUI workflow template engine.

Loads API-format JSON templates, substitutes placeholder slots with
pipeline parameters, and returns ready-to-submit workflow dicts.

Templates use {{placeholder}} tokens that get string-replaced at runtime.
After substitution, numeric values are parsed back from strings to ints/floats
so the resulting JSON is valid for ComfyUI's /prompt endpoint.
"""

import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "workflows" / "templates"


@dataclass
class WorkflowSlots:
    """All substitutable parameters for a ComfyUI workflow template."""

    positive_prompt: str = ""
    negative_prompt: str = (
        "airbrushed, smooth skin, overexposed, specular highlights, "
        "plastic, CGI, 3D render, cartoon, anime, painting, "
        "oversaturated, HDR, studio lighting, ring light, "
        "extra fingers, deformed hands, blurry face"
    )
    reference_image: str = ""       # ComfyUI filename (from vault)
    pose_image: str = ""            # ComfyUI filename (from pose library)
    seed: int = -1                  # -1 = generate random
    steps: int = 28
    cfg_scale: float = 4.0
    width: int = 1024
    height: int = 1024
    pulid_strength: float = 0.90    # Identity fidelity (PuLID weight)
    controlnet_strength: float = 0.75  # Pose adherence
    checkpoint: str = "flux1-dev-Q8_0.gguf"


# Scene-specific defaults from the architecture plan.
# Each scene has tuned prompts, CFG, steps, and control weights.
SCENE_CONFIGS: dict[str, dict] = {
    "cafe": {
        "positive_prompt": (
            "woman sitting at a wooden cafe table, natural window light, "
            "warm afternoon, candid photography, shallow depth of field, "
            "shot on Sony A7III 85mm f/1.8, ambient cafe atmosphere, "
            "other patrons slightly blurred in background"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, studio lighting, ring light, "
            "extra fingers, deformed hands, blurry face"
        ),
        "cfg_scale": 4.0,
        "steps": 28,
        "controlnet_strength": 0.75,
        "pulid_strength": 0.9,
    },
    "pool": {
        "positive_prompt": (
            "woman relaxing by pool, bright natural sunlight, "
            "outdoor lifestyle photography, warm skin tones, "
            "slight sun glare, pool tiles visible, tropical plants, "
            "shot on Canon R5 70-200mm, summer afternoon"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, studio lighting, "
            "extra fingers, deformed hands, blurry face, "
            "too much reflection on water, unrealistic water"
        ),
        "cfg_scale": 4.5,
        "steps": 30,
        "controlnet_strength": 0.70,
        "pulid_strength": 0.9,
    },
    "bed": {
        "positive_prompt": (
            "woman sitting on bed in bedroom, soft morning light "
            "through curtains, cozy atmosphere, clean white bedding, "
            "natural indoor photography, relaxed expression, "
            "shot on Fujifilm X-T5 35mm, warm color temperature"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, harsh shadows, "
            "extra fingers, deformed hands, blurry face"
        ),
        "cfg_scale": 3.5,
        "steps": 28,
        "controlnet_strength": 0.80,
        "pulid_strength": 0.95,
    },
    "kitchen": {
        "positive_prompt": (
            "woman standing at kitchen counter, natural overhead lighting, "
            "modern kitchen, granite countertops, casual home setting, "
            "lifestyle photography, candid moment cooking, "
            "shot on Sony A7III 35mm f/2.0, slightly warm tones"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, studio lighting, "
            "extra fingers, deformed hands, blurry face"
        ),
        "cfg_scale": 4.0,
        "steps": 28,
        "controlnet_strength": 0.75,
        "pulid_strength": 0.9,
    },
    "couch": {
        "positive_prompt": (
            "woman sitting on living room couch, natural window light, "
            "casual home interior, comfortable posture, throw pillows, "
            "lifestyle photography, warm afternoon, "
            "shot on Canon R6 50mm f/1.4, bokeh background"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, studio lighting, "
            "extra fingers, deformed hands, blurry face"
        ),
        "cfg_scale": 3.5,
        "steps": 28,
        "controlnet_strength": 0.80,
        "pulid_strength": 0.9,
    },
    "beach": {
        "positive_prompt": (
            "woman on sandy beach, golden hour sunlight, ocean in background, "
            "natural outdoor photography, wind in hair, relaxed posture, "
            "shot on Sony A7IV 85mm f/1.8, warm sunset tones, "
            "sand texture visible, waves in distance"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, studio lighting, "
            "extra fingers, deformed hands, blurry face, "
            "unrealistic water, fake sky"
        ),
        "cfg_scale": 4.5,
        "steps": 30,
        "controlnet_strength": 0.70,
        "pulid_strength": 0.85,
    },
    "gym": {
        "positive_prompt": (
            "woman standing in gym, fluorescent overhead lighting mixed "
            "with natural light from windows, gym equipment in background, "
            "athletic wear, fitness photography, mirrors visible, "
            "shot on Canon R5 24-70mm, slightly cool tones"
        ),
        "negative_prompt": (
            "airbrushed, smooth skin, overexposed, specular highlights, "
            "plastic, CGI, 3D render, cartoon, anime, painting, "
            "oversaturated, HDR, ring light, "
            "extra fingers, deformed hands, blurry face"
        ),
        "cfg_scale": 4.0,
        "steps": 28,
        "controlnet_strength": 0.75,
        "pulid_strength": 0.9,
    },
}


class WorkflowTemplateEngine:
    """Loads and fills ComfyUI workflow templates."""

    def __init__(self, template_dir: Optional[Path] = None):
        self.template_dir = Path(template_dir) if template_dir else TEMPLATE_DIR

    def list_templates(self) -> list[str]:
        """List available template names (without .json extension)."""
        return [
            p.stem for p in self.template_dir.glob("*.json")
        ]

    def load_template(self, template_name: str) -> dict:
        """Load raw template JSON from disk."""
        path = self.template_dir / f"{template_name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Template not found: {path}. "
                f"Available: {self.list_templates()}"
            )
        with open(path) as f:
            return json.load(f)

    def fill_template(
        self,
        template_name: str,
        slots: WorkflowSlots,
    ) -> dict:
        """Load template and substitute all {{placeholder}} tokens.

        Works by:
        1. Load template JSON as raw string
        2. Replace each {{key}} with the corresponding WorkflowSlots value
        3. Parse back to dict
        4. Validate no unreplaced tokens remain
        """
        path = self.template_dir / f"{template_name}.json"
        with open(path) as f:
            raw = f.read()

        # Resolve seed
        seed = slots.seed if slots.seed >= 0 else random.randint(0, 2**32 - 1)

        # Build replacement map
        replacements = {
            "positive_prompt": slots.positive_prompt,
            "negative_prompt": slots.negative_prompt,
            "reference_image": slots.reference_image,
            "pose_image": slots.pose_image,
            "seed": str(seed),
            "steps": str(slots.steps),
            "cfg_scale": str(slots.cfg_scale),
            "width": str(slots.width),
            "height": str(slots.height),
            "pulid_strength": str(slots.pulid_strength),
            "controlnet_strength": str(slots.controlnet_strength),
            "checkpoint": slots.checkpoint,
        }

        for key, value in replacements.items():
            token = "{{" + key + "}}"
            raw = raw.replace(token, value)

        # Check for unreplaced tokens
        remaining = re.findall(r"\{\{(\w+)\}\}", raw)
        if remaining:
            raise ValueError(
                f"Unreplaced template tokens in {template_name}: {remaining}"
            )

        # Parse back to dict — numeric strings in JSON quotes stay as strings,
        # but ComfyUI expects numeric values for seed/steps/cfg/etc.
        workflow = json.loads(raw)

        # Fix numeric types that were stringified by the replacement
        self._fix_numeric_types(workflow)

        return workflow

    def _fix_numeric_types(self, workflow: dict) -> None:
        """Convert stringified numbers back to int/float in workflow dict.

        ComfyUI's API requires numeric inputs as actual numbers, not strings.
        Template substitution turns everything into strings, so we need to
        fix seed, steps, cfg, width, height, strength values.
        """
        numeric_keys = {
            "seed", "steps", "cfg", "width", "height", "batch_size",
            "denoise", "weight", "start_at", "end_at",
            "strength", "start_percent", "end_percent",
        }

        for node_id, node in workflow.items():
            inputs = node.get("inputs", {})
            for key, value in inputs.items():
                if key in numeric_keys and isinstance(value, str):
                    try:
                        if "." in value:
                            inputs[key] = float(value)
                        else:
                            inputs[key] = int(value)
                    except ValueError:
                        pass  # Leave as string if conversion fails

    def select_template(self, has_pose: bool) -> str:
        """Select appropriate template based on generation requirements."""
        if has_pose:
            return "pulid_openpose"
        return "pulid_txt2img"

    def build_workflow_for_scene(
        self,
        scene: str,
        reference_image: str,
        pose_image: str = "",
        prompt_override: str = "",
        seed: int = -1,
        **overrides,
    ) -> dict:
        """Build a complete workflow for a scene using SCENE_CONFIGS defaults.

        Args:
            scene: Scene key from SCENE_CONFIGS (e.g. "cafe", "pool", "bed")
            reference_image: ComfyUI filename for hero reference
            pose_image: ComfyUI filename for pose skeleton (empty = no ControlNet)
            prompt_override: Override the scene's default positive prompt
            seed: Specific seed or -1 for random
            **overrides: Override any WorkflowSlots field

        Returns:
            Complete workflow dict ready for ComfyUIClient.submit_workflow()
        """
        config = SCENE_CONFIGS.get(scene)
        if config is None:
            raise ValueError(
                f"Unknown scene: {scene}. Available: {list(SCENE_CONFIGS.keys())}"
            )

        has_pose = bool(pose_image)
        template_name = self.select_template(has_pose)

        slots = WorkflowSlots(
            positive_prompt=prompt_override or config["positive_prompt"],
            negative_prompt=config["negative_prompt"],
            reference_image=reference_image,
            pose_image=pose_image,
            seed=seed,
            steps=config.get("steps", 28),
            cfg_scale=config.get("cfg_scale", 4.0),
            pulid_strength=config.get("pulid_strength", 0.9),
            controlnet_strength=config.get("controlnet_strength", 0.75),
        )

        # Apply any additional overrides
        for key, value in overrides.items():
            if hasattr(slots, key):
                object.__setattr__(slots, key, value)

        return self.fill_template(template_name, slots)
