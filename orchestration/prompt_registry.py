"""Immutable prompt version registry for Layer 2.

Layer 2 Invariants:
- Prompt versions are immutable once registered.
- Re-registration with a different hash raises PromptRegistryError (hard error).
- Re-registration with the same hash is a no-op (idempotent, safe for retries).
- prompt_hash = canonical_hash({"id": prompt_id, "template": template, "version": version})
- The registry is NOT thread-safe by default — callers must synchronize if needed.

Replay determinism: The prompt_hash is captured in SandboxEnvironment / ReplayRecord
so that replay verification can detect prompt drift between original and replay runs.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from orchestration.canonical import canonical_hash
from orchestration.errors import PromptRegistryError


@dataclass(frozen=True)
class PromptVersion:
    """An immutable, versioned prompt template.

    Fields:
        prompt_id: Logical name, e.g. "dev.generate_code"
        version: Semver string, e.g. "1.0.0"
        template: The raw prompt template text
        prompt_hash: canonical_hash({"id": prompt_id, "template": template, "version": version})
                     Computed at construction; verified at registration.
    """
    prompt_id: str
    version: str
    template: str
    prompt_hash: str = ""

    def __post_init__(self) -> None:
        expected_hash = _compute_prompt_hash(
            object.__getattribute__(self, "prompt_id"),
            object.__getattribute__(self, "version"),
            object.__getattribute__(self, "template"),
        )
        object.__setattr__(self, "prompt_hash", expected_hash)

    def to_dict(self) -> dict:
        return {
            "prompt_hash": self.prompt_hash,
            "prompt_id": self.prompt_id,
            "template": self.template,
            "version": self.version,
        }


def _compute_prompt_hash(prompt_id: str, version: str, template: str) -> str:
    """Compute the canonical hash of a prompt version.

    Keys sorted in canonical_hash: id, template, version.
    """
    return canonical_hash({
        "id": prompt_id,
        "template": template,
        "version": version,
    })


class PromptRegistry:
    """Registry of immutable prompt versions.

    Layer 2 Invariant: Once a (prompt_id, version) pair is registered, its
    template cannot change. Any attempt to re-register with a different hash
    raises PromptRegistryError.
    """

    def __init__(self) -> None:
        # {(prompt_id, version): PromptVersion}
        self._store: Dict[tuple, PromptVersion] = {}
        # {prompt_id: latest_version_string} — updated on each registration
        self._latest: Dict[str, str] = {}

    def register(self, prompt_version: PromptVersion) -> None:
        """Register a prompt version.

        Idempotent: registering the same (id, version, template) twice is a no-op.
        Error: registering the same (id, version) with a different template raises
               PromptRegistryError.

        Args:
            prompt_version: PromptVersion instance to register

        Raises:
            PromptRegistryError: If (id, version) already registered with different hash
        """
        key = (prompt_version.prompt_id, prompt_version.version)
        existing = self._store.get(key)

        if existing is not None:
            if existing.prompt_hash != prompt_version.prompt_hash:
                raise PromptRegistryError(
                    f"Attempted to re-register prompt {prompt_version.prompt_id!r} "
                    f"version {prompt_version.version!r} with a different template. "
                    f"Existing hash: {existing.prompt_hash[:16]}..., "
                    f"new hash: {prompt_version.prompt_hash[:16]}..."
                )
            # Same hash — idempotent, no-op
            return

        self._store[key] = prompt_version

        # Update latest: use string comparison of version for now.
        # For production semver comparison, use packaging.version.
        current_latest = self._latest.get(prompt_version.prompt_id)
        if current_latest is None or prompt_version.version > current_latest:
            self._latest[prompt_version.prompt_id] = prompt_version.version

    def get(self, prompt_id: str, version: str) -> PromptVersion:
        """Retrieve a specific prompt version.

        Args:
            prompt_id: Logical prompt name
            version: Semver string

        Returns:
            The registered PromptVersion

        Raises:
            PromptRegistryError: If not found
        """
        key = (prompt_id, version)
        pv = self._store.get(key)
        if pv is None:
            raise PromptRegistryError(
                f"Prompt {prompt_id!r} version {version!r} not found in registry"
            )
        return pv

    def get_latest(self, prompt_id: str) -> PromptVersion:
        """Retrieve the latest registered version of a prompt.

        Args:
            prompt_id: Logical prompt name

        Returns:
            The PromptVersion with the highest registered version string

        Raises:
            PromptRegistryError: If no versions registered for this prompt_id
        """
        latest_version = self._latest.get(prompt_id)
        if latest_version is None:
            raise PromptRegistryError(
                f"No versions registered for prompt {prompt_id!r}"
            )
        return self.get(prompt_id, latest_version)

    def list_versions(self, prompt_id: str) -> list:
        """Return all registered version strings for a prompt_id, sorted ascending."""
        versions = [
            v for (pid, v) in self._store.keys()
            if pid == prompt_id
        ]
        return sorted(versions)
