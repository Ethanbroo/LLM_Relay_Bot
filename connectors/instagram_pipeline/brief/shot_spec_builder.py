"""ShotSpec prompt assembly - converts structured shot specs into Flux prompts.

This module handles the critical task of converting ShotSpec dataclass fields
into properly formatted Flux.1-dev prompts that include:
- Character identity (via IdentityAnchor text description)
- LoRA trigger word
- Shot-specific composition/lighting/action details
- Style DNA for aesthetic consistency
- Negative prompts to prevent common artifacts

Design principle: This module does NO creative writing. It assembles prompts
from structured fields. All creativity happens in Stage 2 (intent_builder.py).
"""

import logging
from typing import Optional

from .models import ShotSpec
from ..character.models import CharacterProfile, IdentityAnchor, StyleDNA

logger = logging.getLogger(__name__)


# Negative prompt components — prevents common Flux artifacts
NEGATIVE_PROMPT_BASE = (
    "deformed face, asymmetrical eyes, distorted features, multiple heads, "
    "extra limbs, mutated hands, poorly drawn hands, poorly drawn face, "
    "mutation, deformed, blurry, bad anatomy, bad proportions, disfigured, "
    "out of frame, extra fingers, fused fingers, too many fingers, "
    "watermark, signature, text overlay, username, logo"
)

# Additional negative prompt for identity consistency
IDENTITY_NEGATIVE = (
    "different person, wrong face, face swap, multiple people in focus, "
    "celebrity resemblance, stock photo model"
)


class ShotSpecBuilder:
    """
    Assembles Flux.1-dev prompts from structured ShotSpec + CharacterProfile.

    Key concept: The prompt is deterministic given the inputs. No randomness,
    no creative decisions. This ensures reproducibility and auditability.
    """

    def __init__(self, character: CharacterProfile):
        """
        Initialize builder for a specific character.

        Args:
            character: Character profile with identity_anchor and style_dna
        """
        self.character = character
        self.identity_anchor = character.identity_anchor
        self.style_dna = character.style_dna
        self.lora_trigger = character.lora_trigger_word

    def build_prompt(self, shot_spec: ShotSpec, include_trigger: bool = True) -> dict:
        """
        Assemble Flux prompt from ShotSpec.

        Returns a dict suitable for passing to Flux generation API:
        {
            "prompt": "full positive prompt text",
            "negative_prompt": "negative prompt text",
            "lora_path": "path/to/lora/weights.safetensors",
            "lora_scale": 0.8-1.0,
        }

        Args:
            shot_spec: Structured shot specification
            include_trigger: If True, include LoRA trigger word (default True)

        Returns:
            Dict with prompt, negative_prompt, lora_path, lora_scale
        """
        # Build identity description from IdentityAnchor
        identity_desc = self._build_identity_description(self.identity_anchor)

        # Build style prompt from StyleDNA
        style_prompt = self._build_style_prompt(self.style_dna)

        # Build shot-specific prompt from ShotSpec fields
        shot_prompt = self._build_shot_prompt(shot_spec)

        # Assemble full prompt
        prompt_parts = []

        # 1. LoRA trigger word (if using LoRA)
        if include_trigger and self.lora_trigger:
            prompt_parts.append(self.lora_trigger)

        # 2. Identity description (who is in the shot)
        prompt_parts.append(identity_desc)

        # 3. Shot-specific details (what they're doing, where, how it's framed)
        prompt_parts.append(shot_prompt)

        # 4. Style DNA (aesthetic cohesion)
        prompt_parts.append(style_prompt)

        prompt = ", ".join(prompt_parts)

        # Build negative prompt
        negative_prompt = f"{NEGATIVE_PROMPT_BASE}, {IDENTITY_NEGATIVE}"

        # LoRA scale: 0.8-1.0 for identity LoRAs (community best practice)
        # Hero shots get slightly higher scale for maximum fidelity
        lora_scale = 0.95 if shot_spec.is_hero_shot else 0.85

        return {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "lora_path": self.character.lora_model_path,
            "lora_scale": lora_scale,
        }

    def _build_identity_description(self, anchor: IdentityAnchor) -> str:
        """
        Convert IdentityAnchor to prompt text.

        Example output:
        "woman with warm medium-brown skin tone, deep brown eyes with slight epicanthic fold,
        dark brown wavy shoulder-length hair with natural texture, athletic build with
        defined shoulders, small gap between front teeth"
        """
        parts = [
            anchor.face_description,
            anchor.body_description,
            anchor.hair_description,
            anchor.skin_description,
        ]

        if anchor.distinctive_marks:
            parts.append(", ".join(anchor.distinctive_marks))

        return ", ".join(p for p in parts if p)

    def _build_style_prompt(self, style_dna: StyleDNA) -> str:
        """
        Convert StyleDNA to prompt text.

        Example output:
        "shot on 35mm film, natural grain, soft muted color palette with earthy tones,
        slightly off-center composition, natural window light with soft shadows"
        """
        parts = [
            style_dna.photography_style,
            style_dna.color_palette,
            style_dna.composition_tendency,
            style_dna.lighting_preference,
        ]

        return ", ".join(p for p in parts if p)

    def _build_shot_prompt(self, shot_spec: ShotSpec) -> str:
        """
        Convert ShotSpec fields to prompt text.

        Example output:
        "sitting on apartment windowsill reading a book, gentle smile and relaxed expression,
        portrait medium framing, eye level camera angle, soft natural window light,
        wearing oversized cream knit sweater, background shows blurred cityscape through window"
        """
        parts = [
            # Action and setting
            f"{shot_spec.action} in {shot_spec.scene}",

            # Expression
            shot_spec.expression,

            # Technical camera specs
            f"{shot_spec.framing} framing",
            f"{shot_spec.camera_angle} camera angle",
            shot_spec.lighting,

            # Wardrobe
            f"wearing {shot_spec.wardrobe}",

            # Background
            f"background: {shot_spec.background_detail}",
        ]

        return ", ".join(p for p in parts if p)

    def build_batch_prompts(
        self,
        shot_specs: list[ShotSpec],
        include_trigger: bool = True
    ) -> list[dict]:
        """
        Build prompts for multiple shots at once.

        Useful for carousel posts (3-4 shots) or batch processing.

        Args:
            shot_specs: List of shot specifications
            include_trigger: If True, include LoRA trigger word in all prompts

        Returns:
            List of prompt dicts (same order as input)
        """
        return [self.build_prompt(spec, include_trigger) for spec in shot_specs]

    def get_prompt_preview(self, shot_spec: ShotSpec) -> str:
        """
        Get human-readable preview of assembled prompt.

        Useful for debugging and logging. Returns just the positive prompt text.

        Args:
            shot_spec: Shot specification

        Returns:
            Assembled positive prompt string
        """
        prompt_dict = self.build_prompt(shot_spec)
        return prompt_dict["prompt"]

    def validate_prompt_length(self, shot_spec: ShotSpec) -> tuple[bool, Optional[str]]:
        """
        Validate that assembled prompt doesn't exceed Flux token limits.

        Flux.1-dev uses CLIP for text encoding, which has a 77-token limit
        per chunk. Most prompts stay well under this, but very detailed
        shots might exceed it.

        Args:
            shot_spec: Shot specification

        Returns:
            Tuple of (is_valid, error_message)
            error_message is None if valid
        """
        prompt_dict = self.build_prompt(shot_spec)
        prompt = prompt_dict["prompt"]

        # Rough token count heuristic: ~0.75 tokens per word for English
        word_count = len(prompt.split())
        estimated_tokens = int(word_count * 0.75)

        # Flux CLIP limit is 77 tokens, but we use 70 as safety margin
        if estimated_tokens > 70:
            return False, (
                f"Prompt too long: ~{estimated_tokens} tokens (limit: 70). "
                f"Simplify ShotSpec fields to reduce prompt length."
            )

        return True, None


def create_builder_for_character(character_id: str) -> ShotSpecBuilder:
    """
    Convenience function to create ShotSpecBuilder for a character.

    Loads character from registry and returns initialized builder.

    Args:
        character_id: Character ID to load

    Returns:
        ShotSpecBuilder instance

    Raises:
        FileNotFoundError: If character doesn't exist
    """
    from ..character.registry import CharacterRegistry
    registry = CharacterRegistry()
    character = registry.load(character_id)
    return ShotSpecBuilder(character)
