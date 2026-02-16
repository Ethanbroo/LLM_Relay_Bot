"""Snapshot and rollback for execution safety.

Non-negotiable invariants:
1. Snapshot-before-execute (mandatory for all tasks)
2. Rollback-before-retry (mandatory before re-enqueue)
3. Rollback failure is terminal (task becomes dead)
4. Deterministic snapshot_id and rollback_id

Phase 2 Implementation:
- Snapshot = copy of sandbox workspace
- Rollback = restore from snapshot
- Verification after rollback

Phase 3 will add:
- Filesystem snapshots (btrfs/zfs)
- Database transaction rollback
- External resource cleanup
"""

import hashlib
import shutil
from pathlib import Path
from typing import Optional
import json


class RollbackError(Exception):
    """Base exception for snapshot/rollback errors."""
    pass


class SnapshotManager:
    """Manages snapshots and rollback for task execution."""

    def __init__(self, snapshot_root: str = "/tmp/llm-relay/snapshots"):
        """Initialize snapshot manager.

        Args:
            snapshot_root: Root directory for all snapshots
        """
        self.snapshot_root = Path(snapshot_root)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)

    def _compute_snapshot_id(self, task_id: str, run_id: str, attempt: int) -> str:
        """Compute deterministic snapshot identifier.

        snapshot_id = snapshot_<SHA-256(task_id + run_id + attempt)>

        Args:
            task_id: Task identifier
            run_id: Run identifier
            attempt: Attempt number

        Returns:
            Snapshot ID with prefix
        """
        components = f"{task_id}|{run_id}|{attempt}"
        snapshot_hash = hashlib.sha256(components.encode('utf-8')).hexdigest()
        return f"snapshot_{snapshot_hash}"

    def _compute_rollback_id(self, snapshot_id: str, run_id: str) -> str:
        """Compute deterministic rollback identifier.

        rollback_id = rollback_<SHA-256(snapshot_id + run_id)>

        Args:
            snapshot_id: Snapshot identifier
            run_id: Run identifier

        Returns:
            Rollback ID with prefix
        """
        components = f"{snapshot_id}|{run_id}"
        rollback_hash = hashlib.sha256(components.encode('utf-8')).hexdigest()
        return f"rollback_{rollback_hash}"

    def create_snapshot(
        self,
        task_id: str,
        run_id: str,
        attempt: int,
        workspace_path: Path
    ) -> dict:
        """Create snapshot of workspace before execution.

        Args:
            task_id: Task identifier
            run_id: Run identifier
            attempt: Attempt number
            workspace_path: Path to sandbox workspace

        Returns:
            Snapshot metadata dict with snapshot_id, path, size

        Raises:
            RollbackError: If snapshot creation fails
        """
        snapshot_id = self._compute_snapshot_id(task_id, run_id, attempt)
        snapshot_path = self.snapshot_root / snapshot_id

        try:
            # Check if workspace exists
            if not workspace_path.exists():
                raise RollbackError(f"Workspace does not exist: {workspace_path}")

            # Create snapshot by copying workspace
            if snapshot_path.exists():
                # Remove existing snapshot (idempotent)
                shutil.rmtree(snapshot_path)

            shutil.copytree(workspace_path, snapshot_path, symlinks=False)

            # Compute snapshot size
            snapshot_size = sum(
                f.stat().st_size for f in snapshot_path.rglob('*') if f.is_file()
            )

            # Save snapshot metadata
            metadata = {
                "snapshot_id": snapshot_id,
                "task_id": task_id,
                "run_id": run_id,
                "attempt": attempt,
                "workspace_path": str(workspace_path),
                "snapshot_path": str(snapshot_path),
                "size_bytes": snapshot_size,
            }

            metadata_path = snapshot_path / ".snapshot_metadata.json"
            metadata_path.write_text(json.dumps(metadata, indent=2))

            return metadata

        except Exception as e:
            raise RollbackError(f"Failed to create snapshot: {e}") from e

    def rollback(
        self,
        snapshot_id: str,
        run_id: str,
        workspace_path: Path,
        verify: bool = True
    ) -> dict:
        """Rollback workspace to snapshot state.

        Args:
            snapshot_id: Snapshot identifier
            run_id: Run identifier (for rollback_id)
            workspace_path: Path to sandbox workspace
            verify: Verify rollback succeeded (default: True)

        Returns:
            Rollback metadata dict with rollback_id, success

        Raises:
            RollbackError: If rollback fails
        """
        rollback_id = self._compute_rollback_id(snapshot_id, run_id)
        snapshot_path = self.snapshot_root / snapshot_id

        try:
            # Check snapshot exists
            if not snapshot_path.exists():
                raise RollbackError(f"Snapshot does not exist: {snapshot_id}")

            # Load snapshot metadata
            metadata_path = snapshot_path / ".snapshot_metadata.json"
            if not metadata_path.exists():
                raise RollbackError(f"Snapshot metadata missing: {snapshot_id}")

            snapshot_metadata = json.loads(metadata_path.read_text())

            # Remove current workspace
            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            # Restore from snapshot
            shutil.copytree(snapshot_path, workspace_path, symlinks=False)

            # Verification: Check workspace state matches snapshot
            if verify:
                verification_result = self._verify_rollback(workspace_path, snapshot_path)
                if not verification_result["success"]:
                    raise RollbackError(
                        f"Rollback verification failed: {verification_result['error']}"
                    )

            # Build rollback metadata
            rollback_metadata = {
                "rollback_id": rollback_id,
                "snapshot_id": snapshot_id,
                "run_id": run_id,
                "workspace_path": str(workspace_path),
                "success": True,
                "verified": verify
            }

            return rollback_metadata

        except Exception as e:
            raise RollbackError(f"Rollback failed: {e}") from e

    def _verify_rollback(self, workspace_path: Path, snapshot_path: Path) -> dict:
        """Verify rollback succeeded by comparing workspace to snapshot.

        Args:
            workspace_path: Path to workspace
            snapshot_path: Path to snapshot

        Returns:
            Dict with success: bool, error: Optional[str]
        """
        try:
            # Get file lists
            workspace_files = set(
                str(f.relative_to(workspace_path))
                for f in workspace_path.rglob('*')
                if f.is_file() and f.name != '.snapshot_metadata.json'
            )

            snapshot_files = set(
                str(f.relative_to(snapshot_path))
                for f in snapshot_path.rglob('*')
                if f.is_file() and f.name != '.snapshot_metadata.json'
            )

            # Check file lists match
            if workspace_files != snapshot_files:
                missing = snapshot_files - workspace_files
                extra = workspace_files - snapshot_files
                return {
                    "success": False,
                    "error": f"File mismatch - missing: {missing}, extra: {extra}"
                }

            # Check file sizes match
            for rel_path in workspace_files:
                workspace_file = workspace_path / rel_path
                snapshot_file = snapshot_path / rel_path

                if workspace_file.stat().st_size != snapshot_file.stat().st_size:
                    return {
                        "success": False,
                        "error": f"File size mismatch: {rel_path}"
                    }

            return {"success": True, "error": None}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete snapshot after successful task completion.

        Args:
            snapshot_id: Snapshot identifier

        Raises:
            RollbackError: If deletion fails
        """
        snapshot_path = self.snapshot_root / snapshot_id

        try:
            if snapshot_path.exists():
                shutil.rmtree(snapshot_path)
        except Exception as e:
            raise RollbackError(f"Failed to delete snapshot: {e}") from e

    def get_snapshot_metadata(self, snapshot_id: str) -> Optional[dict]:
        """Get snapshot metadata.

        Args:
            snapshot_id: Snapshot identifier

        Returns:
            Snapshot metadata dict or None if not found
        """
        snapshot_path = self.snapshot_root / snapshot_id
        metadata_path = snapshot_path / ".snapshot_metadata.json"

        if not metadata_path.exists():
            return None

        try:
            return json.loads(metadata_path.read_text())
        except Exception:
            return None

    def list_snapshots(self, task_id: Optional[str] = None) -> list[dict]:
        """List all snapshots.

        Args:
            task_id: Filter by task_id (optional)

        Returns:
            List of snapshot metadata dicts
        """
        snapshots = []

        for snapshot_dir in self.snapshot_root.iterdir():
            if not snapshot_dir.is_dir():
                continue

            metadata = self.get_snapshot_metadata(snapshot_dir.name)
            if metadata:
                if task_id is None or metadata.get("task_id") == task_id:
                    snapshots.append(metadata)

        return snapshots
