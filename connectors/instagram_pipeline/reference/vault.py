"""Reference image vault for ComfyUI PuLID identity lock.

Manages hero images: uploads once to ComfyUI input/, caches filename
mappings in a JSON manifest, provides the correct filename for workflow
template slots. Skips re-upload when the local file hash hasn't changed.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..utils.hashing import sha256_file

logger = logging.getLogger(__name__)


class ReferenceVault:
    """Manages character reference images for ComfyUI PuLID workflows.

    Workflow:
    1. On first use, upload hero images to ComfyUI via client.upload_image()
    2. Cache the mapping {local_hash -> comfyui_filename} in manifest.json
    3. On subsequent uses, return cached filename (skip re-upload)
    4. If local file changes (hash mismatch), re-upload automatically
    """

    SUBFOLDER = "references"

    def __init__(
        self,
        vault_dir: str | Path,
        comfyui_client=None,
    ):
        """
        Args:
            vault_dir: Directory to store manifest and local reference copies.
                       e.g. data/characters/solana_v1/reference_vault/
            comfyui_client: ComfyUIClient instance (injected). Can be set
                           later via set_client() if not available at init.
        """
        self.vault_dir = Path(vault_dir)
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.client = comfyui_client
        self.manifest_path = self.vault_dir / "manifest.json"
        self._manifest = self._load_manifest()

    def set_client(self, comfyui_client) -> None:
        """Set or replace the ComfyUI client."""
        self.client = comfyui_client

    def _load_manifest(self) -> dict:
        """Load upload manifest from disk."""
        if self.manifest_path.exists():
            with open(self.manifest_path) as f:
                return json.load(f)
        return {"uploads": {}}

    def _save_manifest(self) -> None:
        """Persist manifest to disk."""
        with open(self.manifest_path, "w") as f:
            json.dump(self._manifest, f, indent=2)

    async def ensure_uploaded(
        self,
        local_path: str,
        ref_name: str = "hero_front",
    ) -> str:
        """Ensure a reference image is uploaded to ComfyUI.

        Args:
            local_path: Path to local reference image file
            ref_name: Logical name for this reference (e.g. "hero_front", "hero_back")

        Returns:
            ComfyUI filename to use in workflow JSON LoadImage nodes
            (e.g. "references/hero_front.png")
        """
        if self.client is None:
            raise RuntimeError("ComfyUI client not set. Call set_client() first.")

        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Reference image not found: {local_path}")

        # Hash local file
        local_hash = sha256_file(str(path))

        # Check manifest for existing upload with matching hash
        uploads = self._manifest.get("uploads", {})
        existing = uploads.get(ref_name)

        if existing and existing.get("local_hash") == local_hash:
            comfyui_filename = existing["comfyui_filename"]
            logger.debug(
                "Reference %s already uploaded (hash match): %s",
                ref_name, comfyui_filename,
            )
            return comfyui_filename

        # Upload to ComfyUI
        logger.info(
            "Uploading reference %s from %s (hash: %s...)",
            ref_name, path.name, local_hash[:12],
        )
        comfyui_filename = await self.client.upload_image(
            str(path),
            subfolder=self.SUBFOLDER,
        )

        # Update manifest
        uploads[ref_name] = {
            "local_path": str(path),
            "local_hash": local_hash,
            "comfyui_filename": comfyui_filename,
            "uploaded_at": datetime.now().isoformat(),
        }
        self._manifest["uploads"] = uploads
        self._save_manifest()

        return comfyui_filename

    def get_cached_filename(self, ref_name: str) -> Optional[str]:
        """Get the ComfyUI filename from cache without uploading.

        Returns None if not cached.
        """
        existing = self._manifest.get("uploads", {}).get(ref_name)
        if existing:
            return existing.get("comfyui_filename")
        return None

    def list_references(self) -> list[dict]:
        """List all uploaded references."""
        return [
            {"name": name, **info}
            for name, info in self._manifest.get("uploads", {}).items()
        ]

    def clear_cache(self) -> None:
        """Clear the upload manifest. Next ensure_uploaded() will re-upload."""
        self._manifest = {"uploads": {}}
        self._save_manifest()
        logger.info("Reference vault cache cleared")
