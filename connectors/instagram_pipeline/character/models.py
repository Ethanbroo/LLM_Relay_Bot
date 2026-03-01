"""Character identity data models.

These frozen dataclasses define the complete identity specification for
an AI character. All fields are immutable after creation - any change
requires creating a new version of the character.

Design principle: Character identity is a first-class object, not a prompt string.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class IdentityAnchor:
    """
    Describes the character's visual identity in natural language.

    These descriptions are passed verbatim into every generation prompt,
    ensuring consistency without relying solely on the LoRA weights.
    Both systems working together give you layered identity locking.

    IMPORTANT: Latent diffusion models reproduce distinctive marks (birthmarks, scars)
    at only ~30-50% fidelity. Design the character to not rely on exact mark placement
    for brand identity. Use structural face features (gap teeth, upturned nose,
    wide-set eyes) as the primary identity anchors — these persist more reliably.
    """
    face_description: str       # "oval face, wide-set hazel eyes, slight upturned nose,
                                #  small gap between front teeth, faint horizontal
                                #  forehead crease above left brow"

    body_description: str       # "5'7 slim athletic build, slight hip asymmetry,
                                #  small birthmark inner left wrist"

    hair_description: str       # "dark auburn wavy hair, natural frizz at roots,
                                #  mid-length with uneven ends"

    skin_description: str       # "warm olive undertone, faint acne scarring on chin,
                                #  natural pores visible, light sun freckles on nose bridge"

    distinctive_marks: Tuple[str, ...] = field(default_factory=tuple)
                                # ("small birthmark inner left wrist",
                                #  "faint scar right eyebrow outer edge")
                                # NOTE: Logged for completeness, but ~30-50% reproduction fidelity

    def __post_init__(self):
        """Validate required fields are non-empty."""
        if not self.face_description or not self.face_description.strip():
            raise ValueError("face_description cannot be empty")
        if not self.hair_description or not self.hair_description.strip():
            raise ValueError("hair_description cannot be empty")
        if not self.skin_description or not self.skin_description.strip():
            raise ValueError("skin_description cannot be empty")


@dataclass(frozen=True)
class StyleDNA:
    """
    Locks the aesthetic of all content produced for this character.

    Think of this as the character's 'visual signature' — the Instagram
    aesthetic that makes their feed recognizable as a cohesive whole.

    All fields are injected into generation prompts to maintain consistency.
    """
    photography_style: str      # "film photography, natural light, slight grain,
                                #  muted warm tones, candid framing"

    color_palette: str          # "earthy tones, terracotta, cream, sage green,
                                #  dusty rose — avoid primary colors and neon"

    composition_tendency: str   # "slightly off-center subjects, environmental context
                                #  visible, not tightly cropped"

    lighting_preference: str    # "golden hour, overcast natural light, indoor
                                #  window light — avoid studio flash look"

    wardrobe_style: str         # "casual elevated — linen, neutral tones, minimal
                                #  jewelry, no logos, lived-in not pristine"

    environment_range: Tuple[str, ...] = field(default_factory=tuple)
                                # ("coffee shop", "outdoor park", "home interior",
                                #  "street/urban", "beach/nature")
                                # Defined as tuple so it's hashable

    def __post_init__(self):
        """Validate required fields are non-empty."""
        if not self.photography_style or not self.photography_style.strip():
            raise ValueError("photography_style cannot be empty")
        if not self.color_palette or not self.color_palette.strip():
            raise ValueError("color_palette cannot be empty")


@dataclass(frozen=True)
class CharacterProfile:
    """
    The complete, versioned identity record for one AI character.

    This is the object that flows through the entire pipeline.
    The character_hash ties all generated content to this exact version.

    Immutability: All fields are frozen. To update a character, increment
    the version number and create a new CharacterProfile object.
    """
    character_id: str               # "aurora_v1" — used as folder name and registry key
    display_name: str               # "Aurora" — used in captions if character speaks
    identity_anchor: IdentityAnchor
    style_dna: StyleDNA

    # LoRA model references — set after training, null before
    lora_model_path: Optional[str] = None          # local path to .safetensors weights
    lora_model_hash: Optional[str] = None          # SHA-256 of weights file (drift detection)
    lora_trigger_word: str = ""                    # "AURORA_V1" — uppercase by convention
    lora_base_model: str = "black-forest-labs/FLUX.1-dev"  # Base model used for training
    lora_provider: str = "fal.ai"                  # "fal.ai" | "replicate"
    lora_version_string: Optional[str] = None      # API-reported version for reproducibility

    # HeyGen — set after Digital Twin creation, null before (used in UC2)
    heygen_avatar_id: Optional[str] = None         # HeyGen's internal ID for the digital twin
    heygen_training_video_path: Optional[str] = None

    # ElevenLabs voice clone — set after voice cloning, null before
    voice_id: Optional[str] = None                 # ElevenLabs cloned voice ID for TTS

    # Face embedding reference — set after Stage 0 embedding extraction
    face_embedding_path: Optional[str] = None      # path to reference_embeddings.npy
    embedding_model: str = "buffalo_l"             # InsightFace model name

    # Metadata
    created_at: str = ""                           # ISO timestamp
    version: int = 1                               # increment on any identity change
    disclosure_label_required: bool = True         # always True per platform policy

    # ComfyUI reference vault — directory for PuLID hero images
    reference_vault_path: Optional[str] = None     # e.g. "data/characters/solana_v1/reference_vault"

    # Canonical hash — computed from all above fields for audit traceability
    character_hash: Optional[str] = None           # set by registry after construction

    def __post_init__(self):
        """Validate required fields."""
        if not self.character_id or not self.character_id.strip():
            raise ValueError("character_id cannot be empty")
        if not self.display_name or not self.display_name.strip():
            raise ValueError("display_name cannot be empty")
        if self.version < 1:
            raise ValueError("version must be >= 1")

    def is_trained(self) -> bool:
        """Returns True if LoRA training has been completed."""
        return self.lora_model_path is not None and self.lora_model_hash is not None

    def is_embedding_ready(self) -> bool:
        """Returns True if face embeddings have been extracted."""
        return self.face_embedding_path is not None

    def is_production_ready(self) -> bool:
        """Returns True if character is ready for content generation."""
        return self.is_trained() and self.is_embedding_ready()
