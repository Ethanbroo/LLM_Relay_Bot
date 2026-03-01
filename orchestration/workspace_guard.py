"""Workspace boundary enforcement for Layer 2.

Layer 2 Invariant: No diff entry may target a path outside the workspace root.

realpath() is used to resolve symlinks at both the file path and the workspace
root before comparison, closing all known path traversal vectors:
  - ../../ sequences
  - Absolute paths outside the root (e.g. /etc/passwd)
  - Symlink indirection (symlink -> /etc)

Filesystem Unicode note:
  - Path comparison uses raw bytes from realpath — not NFC-normalized strings.
  - NFC normalization is applied only for hashing (canonical.py), never for
    filesystem operations, because macOS HFS+ stores filenames as NFD internally.
"""

import os
from orchestration.errors import PathEscapeError


def assert_within_workspace(file_path: str, workspace_root: str) -> None:
    """Raise PathEscapeError if file_path resolves outside workspace_root.

    Layer 2 Invariant: realpath() is always called on both arguments before
    any comparison.  This makes the check immune to:
      - Relative path traversal (../../etc/passwd)
      - Absolute paths outside the root
      - Symlinks that point outside the workspace

    Args:
        file_path: Path from the diff entry (may be relative or absolute)
        workspace_root: The authoritative workspace root directory

    Raises:
        PathEscapeError: If file_path resolves to a location outside workspace_root
    """
    real_root = os.path.realpath(workspace_root)
    real_target = os.path.realpath(os.path.join(real_root, file_path) if not os.path.isabs(file_path) else file_path)

    # Must be exactly the root (unlikely for a file) or strictly under it
    # The os.sep suffix prevents /workspace-extra from matching /workspace
    if real_target != real_root and not real_target.startswith(real_root + os.sep):
        raise PathEscapeError(file_path, workspace_root)


def validate_all_paths(diff_entries, workspace_root: str) -> None:
    """Apply assert_within_workspace to every entry in a diff.

    Args:
        diff_entries: Iterable of objects with a .file_path attribute
        workspace_root: The authoritative workspace root directory

    Raises:
        PathEscapeError: On the first entry that fails the check (fail-fast)
    """
    for entry in diff_entries:
        assert_within_workspace(entry.file_path, workspace_root)
