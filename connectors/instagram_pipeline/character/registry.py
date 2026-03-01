"""Character registry for persistence and retrieval.

The registry is the single source of truth for all characters.
The pipeline always loads characters from here — nothing is hardcoded elsewhere.
"""

import json
import dataclasses
from pathlib import Path
from typing import Optional
from datetime import datetime

from .models import CharacterProfile, IdentityAnchor, StyleDNA
from ..utils.hashing import canonical_hash


CHARACTER_DATA_ROOT = Path("data/characters")


class CharacterRegistry:
    """
    Manages persistence and retrieval of CharacterProfile objects.

    Design principle: The pipeline always fetches from the registry
    at the start of a run. This means you can update a character
    (lora_model_hash, heygen_avatar_id, etc.) by updating the JSON
    file and restarting — no code changes needed.
    """

    def __init__(self, data_root: Optional[Path] = None):
        """
        Initialize registry.

        Args:
            data_root: Optional override for character data directory.
                      Defaults to data/characters/
        """
        self.data_root = data_root or CHARACTER_DATA_ROOT
        self.data_root.mkdir(parents=True, exist_ok=True)

    def load(self, character_id: str) -> CharacterProfile:
        """
        Load character profile from registry.

        Args:
            character_id: Character ID (e.g., "aurora_v1")

        Returns:
            CharacterProfile object

        Raises:
            FileNotFoundError: If character doesn't exist
            ValueError: If profile JSON is invalid
        """
        profile_path = self.data_root / character_id / "character_profile.json"
        if not profile_path.exists():
            raise FileNotFoundError(
                f"Character '{character_id}' not found at {profile_path}. "
                "Run Stage 0 setup first or create the character manually."
            )

        with open(profile_path, 'r') as f:
            data = json.load(f)

        return self._deserialize(data)

    def save(self, profile: CharacterProfile) -> None:
        """
        Save character profile to registry.

        Automatically computes character_hash if not already set.

        Args:
            profile: CharacterProfile to save
        """
        # Compute character hash if not already set
        if profile.character_hash is None:
            profile_dict = dataclasses.asdict(profile)
            profile_dict['character_hash'] = None  # Exclude from its own hash
            new_hash = canonical_hash(profile_dict)

            # Replace with hashed version
            profile = dataclasses.replace(profile, character_hash=new_hash)

        char_dir = self.data_root / profile.character_id
        char_dir.mkdir(parents=True, exist_ok=True)

        profile_path = char_dir / "character_profile.json"
        serialized = self._serialize(profile)

        with open(profile_path, 'w') as f:
            json.dump(serialized, f, indent=2)

    def exists(self, character_id: str) -> bool:
        """
        Check if character exists in registry.

        Args:
            character_id: Character ID to check

        Returns:
            True if character exists
        """
        profile_path = self.data_root / character_id / "character_profile.json"
        return profile_path.exists()

    def list_characters(self) -> list[str]:
        """
        List all character IDs in the registry.

        Returns:
            List of character IDs
        """
        character_ids = []
        for char_dir in self.data_root.iterdir():
            if char_dir.is_dir():
                profile_path = char_dir / "character_profile.json"
                if profile_path.exists():
                    character_ids.append(char_dir.name)
        return sorted(character_ids)

    def update_lora(
        self,
        character_id: str,
        lora_path: str,
        lora_hash: str,
        version_string: Optional[str] = None
    ) -> CharacterProfile:
        """
        Update character profile with LoRA training results.

        Called after training to update the profile with new LoRA info.

        Args:
            character_id: Character to update
            lora_path: Path to trained LoRA weights file
            lora_hash: SHA-256 hash of weights file
            version_string: Optional API version string

        Returns:
            Updated CharacterProfile

        Raises:
            FileNotFoundError: If character doesn't exist
        """
        profile = self.load(character_id)

        # Create updated profile — frozen dataclass requires replace()
        updated = dataclasses.replace(
            profile,
            lora_model_path=lora_path,
            lora_model_hash=lora_hash,
            lora_version_string=version_string,
        )

        # Recompute character hash with new LoRA info
        updated = dataclasses.replace(
            updated,
            character_hash=None  # Clear for recomputation
        )

        self.save(updated)
        return updated

    def update_face_embeddings(
        self,
        character_id: str,
        embedding_path: str
    ) -> CharacterProfile:
        """
        Update character profile with face embedding extraction results.

        Called after Stage 0 embedding extraction.

        Args:
            character_id: Character to update
            embedding_path: Path to reference_embeddings.npy file

        Returns:
            Updated CharacterProfile

        Raises:
            FileNotFoundError: If character doesn't exist
        """
        profile = self.load(character_id)

        updated = dataclasses.replace(
            profile,
            face_embedding_path=embedding_path,
        )

        # Recompute character hash
        updated = dataclasses.replace(updated, character_hash=None)

        self.save(updated)
        return updated

    def update_heygen_avatar(
        self,
        character_id: str,
        avatar_id: str,
        training_video_path: Optional[str] = None
    ) -> CharacterProfile:
        """
        Update character profile with HeyGen Digital Twin ID.

        Called after HeyGen avatar creation (used in UC2 - gaming ads).

        Args:
            character_id: Character to update
            avatar_id: HeyGen's internal avatar ID
            training_video_path: Optional path to training video

        Returns:
            Updated CharacterProfile

        Raises:
            FileNotFoundError: If character doesn't exist
        """
        profile = self.load(character_id)

        updated = dataclasses.replace(
            profile,
            heygen_avatar_id=avatar_id,
            heygen_training_video_path=training_video_path,
        )

        # Recompute character hash
        updated = dataclasses.replace(updated, character_hash=None)

        self.save(updated)
        return updated

    def update_voice(
        self,
        character_id: str,
        voice_id: str,
    ) -> CharacterProfile:
        """
        Update character profile with ElevenLabs voice clone ID.

        Called after voice cloning to associate a voice with the character.

        Args:
            character_id: Character to update
            voice_id: ElevenLabs voice clone ID

        Returns:
            Updated CharacterProfile
        """
        profile = self.load(character_id)

        updated = dataclasses.replace(
            profile,
            voice_id=voice_id,
        )

        # Recompute character hash
        updated = dataclasses.replace(updated, character_hash=None)

        self.save(updated)
        return updated

    def _serialize(self, profile: CharacterProfile) -> dict:
        """
        Convert CharacterProfile to JSON-serializable dict.

        Args:
            profile: CharacterProfile to serialize

        Returns:
            Dict suitable for JSON serialization
        """
        return dataclasses.asdict(profile)

    def _deserialize(self, data: dict) -> CharacterProfile:
        """
        Convert JSON dict to CharacterProfile object.

        Args:
            data: Serialized character profile dict

        Returns:
            CharacterProfile object
        """
        # Reconstruct nested dataclasses
        anchor_data = data.get('identity_anchor', {})
        # Convert distinctive_marks list back to tuple for hashability
        if 'distinctive_marks' in anchor_data and isinstance(anchor_data['distinctive_marks'], list):
            anchor_data['distinctive_marks'] = tuple(anchor_data['distinctive_marks'])
        anchor = IdentityAnchor(**anchor_data)

        style_data = data.get('style_dna', {})
        # Convert environment_range list back to tuple for hashability
        if 'environment_range' in style_data and isinstance(style_data['environment_range'], list):
            style_data['environment_range'] = tuple(style_data['environment_range'])
        style_dna = StyleDNA(**style_data)

        # Remove nested objects from data before passing to CharacterProfile
        remaining = {k: v for k, v in data.items()
                    if k not in ('identity_anchor', 'style_dna')}

        return CharacterProfile(
            identity_anchor=anchor,
            style_dna=style_dna,
            **remaining
        )

    def create_new_character(
        self,
        character_id: str,
        display_name: str,
        identity_anchor: IdentityAnchor,
        style_dna: StyleDNA,
        lora_trigger_word: str,
        version: int = 1
    ) -> CharacterProfile:
        """
        Create a new character profile and save to registry.

        This is the entry point for Stage 0 character creation.

        Args:
            character_id: Unique ID (e.g., "aurora_v1")
            display_name: Human-readable name (e.g., "Aurora")
            identity_anchor: Visual identity description
            style_dna: Aesthetic style specification
            lora_trigger_word: Trigger word for LoRA (e.g., "AURORA_V1")
            version: Version number (default 1)

        Returns:
            New CharacterProfile object

        Raises:
            FileExistsError: If character_id already exists
        """
        if self.exists(character_id):
            raise FileExistsError(
                f"Character '{character_id}' already exists. "
                "Use a new version ID or delete the existing character first."
            )

        profile = CharacterProfile(
            character_id=character_id,
            display_name=display_name,
            identity_anchor=identity_anchor,
            style_dna=style_dna,
            lora_trigger_word=lora_trigger_word,
            version=version,
            created_at=datetime.utcnow().isoformat(),
        )

        self.save(profile)
        return profile
