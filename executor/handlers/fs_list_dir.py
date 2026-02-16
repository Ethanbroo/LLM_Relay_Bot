"""Handler for fs.list_dir action.

Lists directory contents from sandbox workspace with:
- Max entries limit
- Sort order (name_asc, name_desc, mtime_asc, mtime_desc, size_asc, size_desc)
- Hidden file filtering
- Recursive listing (optional)
"""

from typing import Any
from pathlib import Path
from executor.handlers import HandlerError
from executor.sandbox import Sandbox, SandboxError


class FsListDirHandler:
    """Handler for fs.list_dir action."""

    def execute(self, validated_action: dict, sandbox: Sandbox) -> dict:
        """Execute directory listing within sandbox.

        Args:
            validated_action: ValidatedAction with fs.list_dir payload
            sandbox: Sandbox instance

        Returns:
            Artifacts dict with entries list

        Raises:
            HandlerError: If listing fails
        """
        try:
            # Extract payload
            payload = validated_action.get("sanitized_payload", {})
            path = payload["path"]
            max_entries = payload.get("max_entries", 100)
            sort_order = payload.get("sort_order", "name_asc")
            include_hidden = payload.get("include_hidden", False)
            recursive = payload.get("recursive", False)

            # Get directory path within sandbox
            try:
                dir_path = sandbox.get_workspace_path(path)
            except SandboxError as e:
                raise HandlerError(f"Invalid path: {e}") from e

            # Check directory exists
            if not dir_path.exists():
                raise HandlerError(f"Directory not found: {path}")

            if not dir_path.is_dir():
                raise HandlerError(f"Not a directory: {path}")

            # List directory entries
            entries = []

            if recursive:
                # Recursive listing
                for entry_path in dir_path.rglob('*'):
                    entry = self._build_entry(entry_path, dir_path, include_hidden)
                    if entry:
                        entries.append(entry)
            else:
                # Non-recursive listing
                for entry_path in dir_path.iterdir():
                    entry = self._build_entry(entry_path, dir_path, include_hidden)
                    if entry:
                        entries.append(entry)

            # Sort entries
            entries = self._sort_entries(entries, sort_order)

            # Apply max_entries limit
            if len(entries) > max_entries:
                entries = entries[:max_entries]
                truncated = True
            else:
                truncated = False

            # Build artifacts
            artifacts = {
                "entries": entries,
                "count": len(entries),
                "truncated": truncated,
                "path": path,
                "sort_order": sort_order
            }

            return artifacts

        except HandlerError:
            raise
        except Exception as e:
            raise HandlerError(f"fs.list_dir handler failed: {e}") from e

    def _build_entry(
        self,
        entry_path: Path,
        base_path: Path,
        include_hidden: bool
    ) -> dict | None:
        """Build entry dict for a file/directory.

        Args:
            entry_path: Absolute path to entry
            base_path: Base directory path
            include_hidden: Include hidden files?

        Returns:
            Entry dict or None if filtered out
        """
        # Filter hidden files
        if not include_hidden and entry_path.name.startswith('.'):
            return None

        # Get relative path
        try:
            relative_path = entry_path.relative_to(base_path)
        except ValueError:
            # Entry is not relative to base (shouldn't happen)
            return None

        # Get entry info
        stat = entry_path.stat()

        entry = {
            "name": entry_path.name,
            "path": str(relative_path),
            "type": "directory" if entry_path.is_dir() else "file",
        }

        # Add file-specific fields
        if entry_path.is_file():
            entry["size"] = stat.st_size
            entry["mtime"] = int(stat.st_mtime)  # Unix timestamp
        else:
            # Directories don't have size/mtime in response
            pass

        return entry

    def _sort_entries(self, entries: list[dict], sort_order: str) -> list[dict]:
        """Sort entries by sort_order.

        Args:
            entries: List of entry dicts
            sort_order: Sort order (name_asc, name_desc, etc.)

        Returns:
            Sorted list of entries
        """
        if sort_order == "name_asc":
            return sorted(entries, key=lambda e: e["name"])
        elif sort_order == "name_desc":
            return sorted(entries, key=lambda e: e["name"], reverse=True)
        elif sort_order == "mtime_asc":
            return sorted(entries, key=lambda e: e.get("mtime", 0))
        elif sort_order == "mtime_desc":
            return sorted(entries, key=lambda e: e.get("mtime", 0), reverse=True)
        elif sort_order == "size_asc":
            return sorted(entries, key=lambda e: e.get("size", 0))
        elif sort_order == "size_desc":
            return sorted(entries, key=lambda e: e.get("size", 0), reverse=True)
        else:
            # Default: name_asc
            return sorted(entries, key=lambda e: e["name"])
