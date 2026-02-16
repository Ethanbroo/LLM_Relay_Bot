"""LocalFS connector for file system operations.

Phase 5 Invariants:
- Workspace boundary enforcement
- Deterministic idempotency via file hashing
- Rollback with pre-execution snapshots
"""

import os
import hashlib
import shutil
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from connectors.base import BaseConnector, ConnectorRequest, ConnectorContext
from connectors.results import (
    ConnectorResult,
    ConnectorStatus,
    RollbackResult,
    RollbackStatus,
    ExecutionArtifact,
    ArtifactType,
    VerificationMethod
)
from connectors.errors import ConnectorError


@dataclass
class FileSnapshot:
    """Snapshot of file state before modification."""
    path: str
    existed: bool
    content_hash: Optional[str] = None
    content: Optional[bytes] = None


class LocalFSConnector(BaseConnector):
    """LocalFS connector for file operations.

    Phase 5 Invariants:
    - All paths must be within workspace_root
    - File operations are atomic where possible
    - Snapshots taken before modifications
    - Rollback restores from snapshots
    """

    connector_type = "local_fs"

    def __init__(self):
        """Initialize LocalFS connector."""
        self._workspace_root: Optional[Path] = None
        self._snapshots: dict[str, FileSnapshot] = {}

    def connect(self, ctx: ConnectorContext) -> None:
        """Establish connection to local filesystem.

        Args:
            ctx: ConnectorContext

        Raises:
            ConnectorError: If workspace_root invalid
        """
        workspace_path = Path(ctx.workspace_root)

        if not workspace_path.exists():
            raise ConnectorError(
                f"Workspace root does not exist: {ctx.workspace_root}",
                error_code="WORKSPACE_NOT_FOUND"
            )

        if not workspace_path.is_dir():
            raise ConnectorError(
                f"Workspace root is not a directory: {ctx.workspace_root}",
                error_code="WORKSPACE_NOT_DIRECTORY"
            )

        self._workspace_root = workspace_path.resolve()

    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        """Execute file system operation.

        Supported actions:
        - fs.write_file: Write content to file
        - fs.read_file: Read file content
        - fs.delete_file: Delete file
        - fs.create_directory: Create directory
        - fs.list_directory: List directory contents

        Args:
            req: ConnectorRequest

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If execution fails
        """
        if self._workspace_root is None:
            raise ConnectorError(
                "Connector not connected",
                error_code="NOT_CONNECTED"
            )

        # Parse payload
        payload = json.loads(req.payload_canonical)

        # Route to handler
        if req.action == "fs.write_file":
            return self._write_file(req, payload)
        elif req.action == "fs.read_file":
            return self._read_file(req, payload)
        elif req.action == "fs.delete_file":
            return self._delete_file(req, payload)
        elif req.action == "fs.create_directory":
            return self._create_directory(req, payload)
        elif req.action == "fs.list_directory":
            return self._list_directory(req, payload)
        else:
            raise ConnectorError(
                f"Unknown action: {req.action}",
                error_code="UNKNOWN_ACTION"
            )

    def rollback(
        self,
        req: ConnectorRequest,
        artifact: Optional[ExecutionArtifact]
    ) -> RollbackResult:
        """Rollback file system operation.

        Args:
            req: Original ConnectorRequest
            artifact: ExecutionArtifact from execute

        Returns:
            RollbackResult

        Raises:
            ConnectorError: If rollback fails
        """
        snapshot_key = req.idempotency_key
        snapshot = self._snapshots.get(snapshot_key)

        if snapshot is None:
            # No snapshot = read-only operation or operation never executed
            return RollbackResult(
                rollback_status=RollbackStatus.NOT_APPLICABLE,
                verification_method=VerificationMethod.NOT_APPLICABLE,
                notes="No snapshot found (read-only or not executed)"
            )

        try:
            target_path = Path(snapshot.path)

            if snapshot.existed:
                # Restore previous content
                if snapshot.content is not None:
                    target_path.write_bytes(snapshot.content)

                    # Verify restoration
                    restored_hash = self._compute_file_hash(target_path)
                    if restored_hash != snapshot.content_hash:
                        return RollbackResult(
                            rollback_status=RollbackStatus.FAILED,
                            verification_method=VerificationMethod.FILE_HASH,
                            notes="Restored content hash mismatch"
                        )

                    return RollbackResult(
                        rollback_status=RollbackStatus.SUCCESS,
                        verification_method=VerificationMethod.FILE_HASH,
                        verification_artifact_hash=restored_hash,
                        notes="File restored to previous state"
                    )
            else:
                # File did not exist before - delete it
                if target_path.exists():
                    if target_path.is_file():
                        target_path.unlink()
                    elif target_path.is_dir():
                        shutil.rmtree(target_path)

                    # Verify deletion
                    if target_path.exists():
                        return RollbackResult(
                            rollback_status=RollbackStatus.FAILED,
                            verification_method=VerificationMethod.FILE_HASH,
                            notes="Failed to delete created file/directory"
                        )

                return RollbackResult(
                    rollback_status=RollbackStatus.SUCCESS,
                    verification_method=VerificationMethod.FILE_HASH,
                    notes="Created file/directory removed"
                )

            return RollbackResult(
                rollback_status=RollbackStatus.NOT_APPLICABLE,
                verification_method=VerificationMethod.NOT_APPLICABLE,
                notes="Unknown rollback scenario"
            )

        except Exception as e:
            return RollbackResult(
                rollback_status=RollbackStatus.FAILED,
                verification_method=VerificationMethod.FILE_HASH,
                notes=f"Rollback error: {str(e)[:200]}"
            )

    def disconnect(self) -> None:
        """Clean up connector resources."""
        self._workspace_root = None
        self._snapshots.clear()

    def _resolve_path(self, relative_path: str) -> Path:
        """Resolve path within workspace boundary.

        Args:
            relative_path: Relative path within workspace

        Returns:
            Resolved absolute path

        Raises:
            ConnectorError: If path escapes workspace
        """
        if self._workspace_root is None:
            raise ConnectorError(
                "Connector not connected",
                error_code="NOT_CONNECTED"
            )

        # Resolve path
        target_path = (self._workspace_root / relative_path).resolve()

        # Verify within workspace
        try:
            target_path.relative_to(self._workspace_root)
        except ValueError:
            raise ConnectorError(
                f"Path escapes workspace: {relative_path}",
                error_code="PATH_ESCAPE"
            )

        return target_path

    def _compute_file_hash(self, path: Path) -> str:
        """Compute SHA-256 hash of file.

        Args:
            path: File path

        Returns:
            SHA-256 hex digest
        """
        hasher = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _take_snapshot(self, path: Path, idempotency_key: str) -> None:
        """Take snapshot of file before modification.

        Args:
            path: File path
            idempotency_key: Idempotency key for snapshot storage
        """
        snapshot = FileSnapshot(
            path=str(path),
            existed=path.exists()
        )

        if snapshot.existed and path.is_file():
            snapshot.content = path.read_bytes()
            snapshot.content_hash = self._compute_file_hash(path)

        self._snapshots[idempotency_key] = snapshot

    def _write_file(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Write content to file.

        Payload:
            path: str - Relative path within workspace
            content: str - File content
            encoding: str - Text encoding (default: utf-8)

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If write fails
        """
        try:
            # Extract parameters
            relative_path = payload.get("path")
            content = payload.get("content")
            encoding = payload.get("encoding", "utf-8")

            if not relative_path or content is None:
                raise ConnectorError(
                    "Missing required fields: path, content",
                    error_code="INVALID_PAYLOAD"
                )

            # Resolve path
            target_path = self._resolve_path(relative_path)

            # Take snapshot before modification
            self._take_snapshot(target_path, req.idempotency_key)

            # Ensure parent directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            content_bytes = content.encode(encoding)
            target_path.write_bytes(content_bytes)

            # Compute result hash
            file_hash = self._compute_file_hash(target_path)
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{file_hash}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                artifacts={"file_hash": file_hash},
                side_effect_summary=f"Wrote {len(content_bytes)} bytes to {relative_path}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Write file failed: {str(e)}",
                error_code="WRITE_FAILED"
            )

    def _read_file(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Read file content.

        Payload:
            path: str - Relative path within workspace
            encoding: str - Text encoding (default: utf-8)

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If read fails
        """
        try:
            # Extract parameters
            relative_path = payload.get("path")
            encoding = payload.get("encoding", "utf-8")

            if not relative_path:
                raise ConnectorError(
                    "Missing required field: path",
                    error_code="INVALID_PAYLOAD"
                )

            # Resolve path
            target_path = self._resolve_path(relative_path)

            # Check file exists
            if not target_path.exists():
                raise ConnectorError(
                    f"File not found: {relative_path}",
                    error_code="FILE_NOT_FOUND"
                )

            if not target_path.is_file():
                raise ConnectorError(
                    f"Not a file: {relative_path}",
                    error_code="NOT_A_FILE"
                )

            # Read file
            content_bytes = target_path.read_bytes()
            file_hash = self._compute_file_hash(target_path)

            # Compute result hash (read-only, no snapshot needed)
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{file_hash}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                artifacts={"file_hash": file_hash},
                side_effect_summary=f"Read {len(content_bytes)} bytes from {relative_path}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Read file failed: {str(e)}",
                error_code="READ_FAILED"
            )

    def _delete_file(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Delete file.

        Payload:
            path: str - Relative path within workspace

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If delete fails
        """
        try:
            # Extract parameters
            relative_path = payload.get("path")

            if not relative_path:
                raise ConnectorError(
                    "Missing required field: path",
                    error_code="INVALID_PAYLOAD"
                )

            # Resolve path
            target_path = self._resolve_path(relative_path)

            # Take snapshot before deletion
            self._take_snapshot(target_path, req.idempotency_key)

            # Delete file
            if target_path.exists():
                if target_path.is_file():
                    target_path.unlink()
                    deleted = True
                else:
                    raise ConnectorError(
                        f"Not a file: {relative_path}",
                        error_code="NOT_A_FILE"
                    )
            else:
                deleted = False

            # Compute result hash
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:deleted={deleted}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                side_effect_summary=f"Deleted {relative_path}" if deleted else f"File not found: {relative_path}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Delete file failed: {str(e)}",
                error_code="DELETE_FAILED"
            )

    def _create_directory(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Create directory.

        Payload:
            path: str - Relative path within workspace
            parents: bool - Create parent directories (default: true)

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If creation fails
        """
        try:
            # Extract parameters
            relative_path = payload.get("path")
            create_parents = payload.get("parents", True)

            if not relative_path:
                raise ConnectorError(
                    "Missing required field: path",
                    error_code="INVALID_PAYLOAD"
                )

            # Resolve path
            target_path = self._resolve_path(relative_path)

            # Take snapshot
            self._take_snapshot(target_path, req.idempotency_key)

            # Create directory
            if target_path.exists():
                if not target_path.is_dir():
                    raise ConnectorError(
                        f"Path exists but is not a directory: {relative_path}",
                        error_code="NOT_A_DIRECTORY"
                    )
                created = False
            else:
                target_path.mkdir(parents=create_parents, exist_ok=True)
                created = True

            # Compute result hash
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:created={created}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                side_effect_summary=f"Created directory {relative_path}" if created else f"Directory already exists: {relative_path}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Create directory failed: {str(e)}",
                error_code="MKDIR_FAILED"
            )

    def _list_directory(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """List directory contents.

        Payload:
            path: str - Relative path within workspace

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If listing fails
        """
        try:
            # Extract parameters
            relative_path = payload.get("path", ".")

            # Resolve path
            target_path = self._resolve_path(relative_path)

            # Check directory exists
            if not target_path.exists():
                raise ConnectorError(
                    f"Directory not found: {relative_path}",
                    error_code="DIRECTORY_NOT_FOUND"
                )

            if not target_path.is_dir():
                raise ConnectorError(
                    f"Not a directory: {relative_path}",
                    error_code="NOT_A_DIRECTORY"
                )

            # List directory
            entries = []
            for entry in sorted(target_path.iterdir()):
                entries.append({
                    "name": entry.name,
                    "is_file": entry.is_file(),
                    "is_dir": entry.is_dir()
                })

            # Compute result hash (read-only)
            entries_str = json.dumps(entries, sort_keys=True)
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{entries_str}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                side_effect_summary=f"Listed {len(entries)} entries in {relative_path}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"List directory failed: {str(e)}",
                error_code="LIST_FAILED"
            )
