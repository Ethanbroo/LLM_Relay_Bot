"""CodeDiffProposal dataclass for Layer 2 dev.generate_code action type.

Layer 2 Invariants:
- diff_identity_hash is canonical_hash(structured diff) — separate from proposal_hash
- workspace_root is resolved via os.path.realpath() at construction time
- Size limits are enforced at construction, not at execution time
- File paths are stored as-is (raw, not NFC-normalized) for filesystem operations
- NFC normalization is used only for the proposal_text field (inherited from Layer 1)

Intent safety is explicitly OUT OF SCOPE for this module. Schema validation and
workspace enforcement gate execution mechanics, not intent safety. A structurally
valid diff with malicious content passes these checks by design — intent safety
is a separate, human-review concern.
"""

import os
import hashlib
from dataclasses import dataclass, field
from typing import Tuple

from orchestration.canonical import canonical_hash
from orchestration.errors import CodeProposalInvalidError
from orchestration.workspace_guard import validate_all_paths

# ── Size limits (enforced at construction) ────────────────────────────────────
MAX_DIFF_ENTRIES: int = 50
MAX_DIFF_TOTAL_BYTES: int = 512_000   # 512 KB across all content fields
MAX_FILE_PATH_LENGTH: int = 512

# Closed set of valid operations
VALID_OPERATIONS: frozenset = frozenset({"create", "modify", "delete"})


@dataclass(frozen=True)
class DiffEntry:
    """A single file change in a structured diff.

    Invariants:
    - file_path is stored as-is for filesystem operations (not NFC-normalized)
    - content is empty string for delete operations
    - operation is one of: "create", "modify", "delete"
    """
    file_path: str
    operation: str
    content: str = field(default="")

    def __post_init__(self) -> None:
        if object.__getattribute__(self, "operation") not in VALID_OPERATIONS:
            op = object.__getattribute__(self, "operation")
            raise CodeProposalInvalidError(
                f"Invalid operation {op!r}: must be one of {sorted(VALID_OPERATIONS)}"
            )
        if len(object.__getattribute__(self, "file_path")) > MAX_FILE_PATH_LENGTH:
            raise CodeProposalInvalidError(
                f"file_path exceeds MAX_FILE_PATH_LENGTH ({MAX_FILE_PATH_LENGTH}): "
                f"{len(object.__getattribute__(self, 'file_path'))} chars"
            )

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for canonical_hash."""
        return {
            "content": self.content,
            "file_path": self.file_path,
            "operation": self.operation,
        }


@dataclass(frozen=True)
class CodeDiffProposal:
    """Structured code-change proposal for the dev.generate_code action type.

    Fields:
        diff_entries: Immutable tuple of DiffEntry objects
        diff_identity_hash: canonical_hash of the structured diff — used for
            structural consensus and replay verification. Separate from
            proposal_hash (which is the hash of the normalized proposal_text).
        workspace_root: realpath()-resolved workspace root, captured at
            construction time for deterministic replay.
        proposal_hash: SHA-256 of the normalized proposal_text (Layer 1 field,
            passed through for audit correlation)

    Construction validates:
    1. len(diff_entries) <= MAX_DIFF_ENTRIES
    2. Total byte size of all content fields <= MAX_DIFF_TOTAL_BYTES
    3. All file paths within workspace_root (via workspace_guard)
    4. diff_identity_hash matches canonical_hash(serialized entries)
    """
    diff_entries: Tuple[DiffEntry, ...]
    workspace_root: str
    proposal_hash: str
    diff_identity_hash: str = field(default="")

    def __post_init__(self) -> None:
        entries = object.__getattribute__(self, "diff_entries")
        workspace = object.__getattribute__(self, "workspace_root")

        # Resolve workspace_root via realpath at construction
        real_root = os.path.realpath(workspace)
        object.__setattr__(self, "workspace_root", real_root)

        # Size limit: entry count
        if len(entries) > MAX_DIFF_ENTRIES:
            raise CodeProposalInvalidError(
                f"Too many diff entries: {len(entries)} > MAX_DIFF_ENTRIES ({MAX_DIFF_ENTRIES})"
            )

        # Size limit: total content bytes
        total_bytes = sum(len(e.content.encode("utf-8")) for e in entries)
        if total_bytes > MAX_DIFF_TOTAL_BYTES:
            raise CodeProposalInvalidError(
                f"Diff payload too large: {total_bytes} bytes > MAX_DIFF_TOTAL_BYTES ({MAX_DIFF_TOTAL_BYTES})"
            )

        # Workspace boundary check (fail-fast on first escape)
        validate_all_paths(entries, real_root)

        # Compute and set diff_identity_hash
        computed_hash = _compute_diff_identity_hash(entries)
        object.__setattr__(self, "diff_identity_hash", computed_hash)

    def to_dict(self) -> dict:
        """Serialize to a dict for audit logging and replay records."""
        return {
            "diff_entries": [e.to_dict() for e in self.diff_entries],
            "diff_identity_hash": self.diff_identity_hash,
            "proposal_hash": self.proposal_hash,
            "workspace_root": self.workspace_root,
        }


def _compute_diff_identity_hash(entries: Tuple[DiffEntry, ...]) -> str:
    """Compute canonical_hash of the structured diff.

    The hash is computed over a list of entry dicts sorted by file_path so
    that entry ordering does not affect the identity hash.

    Args:
        entries: Tuple of DiffEntry objects

    Returns:
        64-character hex SHA-256 digest
    """
    serializable = sorted(
        [e.to_dict() for e in entries],
        key=lambda d: d["file_path"]
    )
    return canonical_hash({"entries": serializable})
