"""Sandbox manager for isolated task execution.

Phase 2 Implementation:
- Per-task sandbox (isolated workspace)
- No network access (simulated - actual restriction in Phase 3)
- No shell access (handlers execute in Python)
- Resource limits (simulated - actual limits in Phase 3)
- Deterministic sandbox_id

Phase 3 will add:
- Container-based isolation (Docker/Podman)
- Actual network blocking (iptables)
- cgroup resource limits (memory, CPU)
- seccomp syscall filtering
"""

import hashlib
import shutil
from pathlib import Path
from typing import Optional
import tempfile


class SandboxError(Exception):
    """Base exception for sandbox errors."""
    pass


class Sandbox:
    """Isolated execution environment for a single task.

    Each task gets its own sandbox with:
    - Isolated workspace directory
    - No network (Phase 3)
    - No shell access
    - Resource limits (Phase 3)
    """

    def __init__(
        self,
        task_id: str,
        run_id: str,
        workspace_root: str = "/tmp/llm-relay/sandboxes"
    ):
        """Create a new sandbox for task execution.

        Args:
            task_id: Deterministic task identifier
            run_id: Execution attempt identifier (UUID v7)
            workspace_root: Root directory for all sandboxes

        Raises:
            SandboxError: If sandbox creation fails
        """
        self.task_id = task_id
        self.run_id = run_id
        self.workspace_root = Path(workspace_root)

        # Compute deterministic sandbox_id
        self.sandbox_id = self._compute_sandbox_id(task_id, run_id)

        # Sandbox workspace path
        self.workspace_path = self.workspace_root / self.sandbox_id

        # State
        self.is_active = False
        self.is_destroyed = False

    def _compute_sandbox_id(self, task_id: str, run_id: str) -> str:
        """Compute deterministic sandbox identifier.

        sandbox_id = sandbox_<SHA-256(task_id + run_id)>

        Args:
            task_id: Task identifier
            run_id: Run identifier

        Returns:
            Sandbox ID with prefix
        """
        components = f"{task_id}|{run_id}"
        sandbox_hash = hashlib.sha256(components.encode('utf-8')).hexdigest()
        return f"sandbox_{sandbox_hash}"

    def create(self) -> None:
        """Create sandbox workspace.

        Raises:
            SandboxError: If sandbox already active or creation fails
        """
        if self.is_active:
            raise SandboxError(f"Sandbox {self.sandbox_id} already active")

        if self.is_destroyed:
            raise SandboxError(f"Sandbox {self.sandbox_id} already destroyed")

        try:
            # Create sandbox workspace directory
            self.workspace_path.mkdir(parents=True, exist_ok=False)

            # Phase 3: Apply network restrictions, resource limits, etc.

            self.is_active = True

        except FileExistsError as e:
            raise SandboxError(
                f"Sandbox workspace already exists: {self.workspace_path}"
            ) from e
        except Exception as e:
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    def destroy(self) -> None:
        """Destroy sandbox and clean up workspace.

        Raises:
            SandboxError: If destruction fails
        """
        if self.is_destroyed:
            # Idempotent - already destroyed
            return

        try:
            # Remove workspace directory
            if self.workspace_path.exists():
                shutil.rmtree(self.workspace_path)

            # Phase 3: Clean up container, network namespace, etc.

            self.is_active = False
            self.is_destroyed = True

        except Exception as e:
            raise SandboxError(f"Failed to destroy sandbox: {e}") from e

    def get_workspace_path(self, relative_path: str = ".") -> Path:
        """Get absolute path within sandbox workspace.

        Args:
            relative_path: Relative path within workspace (default: root)

        Returns:
            Absolute path within sandbox

        Raises:
            SandboxError: If path escapes sandbox or sandbox not active
        """
        if not self.is_active:
            raise SandboxError("Sandbox not active")

        # Resolve path and check it's within sandbox
        try:
            # Normalize and resolve path
            target_path = (self.workspace_path / relative_path).resolve()

            # Security: Ensure path is within sandbox
            if not str(target_path).startswith(str(self.workspace_path.resolve())):
                raise SandboxError(
                    f"Path {relative_path} escapes sandbox: {target_path}"
                )

            return target_path

        except Exception as e:
            raise SandboxError(f"Invalid path {relative_path}: {e}") from e

    def write_file(self, relative_path: str, content: str) -> Path:
        """Write file within sandbox workspace.

        Args:
            relative_path: Relative path within workspace
            content: File content (string)

        Returns:
            Absolute path to written file

        Raises:
            SandboxError: If write fails
        """
        file_path = self.get_workspace_path(relative_path)

        try:
            # Create parent directories
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            file_path.write_text(content, encoding='utf-8')

            return file_path

        except Exception as e:
            raise SandboxError(f"Failed to write file {relative_path}: {e}") from e

    def read_file(self, relative_path: str) -> str:
        """Read file from sandbox workspace.

        Args:
            relative_path: Relative path within workspace

        Returns:
            File content (string)

        Raises:
            SandboxError: If read fails
        """
        file_path = self.get_workspace_path(relative_path)

        try:
            return file_path.read_text(encoding='utf-8')
        except Exception as e:
            raise SandboxError(f"Failed to read file {relative_path}: {e}") from e

    def list_dir(self, relative_path: str = ".") -> list[dict]:
        """List directory contents within sandbox.

        Args:
            relative_path: Relative directory path (default: root)

        Returns:
            List of entries with name, type, size

        Raises:
            SandboxError: If listing fails
        """
        dir_path = self.get_workspace_path(relative_path)

        try:
            if not dir_path.is_dir():
                raise SandboxError(f"Not a directory: {relative_path}")

            entries = []
            for entry in dir_path.iterdir():
                entries.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None
                })

            # Sort by name for determinism
            entries.sort(key=lambda e: e["name"])

            return entries

        except Exception as e:
            raise SandboxError(f"Failed to list directory {relative_path}: {e}") from e

    def __enter__(self):
        """Context manager entry - create sandbox."""
        self.create()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - destroy sandbox."""
        self.destroy()
        return False  # Don't suppress exceptions
