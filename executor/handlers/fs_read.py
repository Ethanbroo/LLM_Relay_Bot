"""Handler for fs.read action.

Reads file from sandbox workspace with:
- Offset and length support
- Encoding support (utf-8, ascii, etc.)
- Safe path handling (within sandbox)
"""

from typing import Any
from executor.handlers import HandlerError
from executor.sandbox import Sandbox, SandboxError


class FsReadHandler:
    """Handler for fs.read action."""

    def execute(self, validated_action: dict, sandbox: Sandbox) -> dict:
        """Execute file read within sandbox.

        Args:
            validated_action: ValidatedAction with fs.read payload
            sandbox: Sandbox instance

        Returns:
            Artifacts dict with content, bytes_read, encoding

        Raises:
            HandlerError: If read fails
        """
        try:
            # Extract payload
            payload = validated_action.get("sanitized_payload", {})
            path = payload["path"]
            offset = payload.get("offset", 0)
            length = payload.get("length", 1048576)  # Default 1MB
            encoding = payload.get("encoding", "utf-8")

            # Get file path within sandbox
            try:
                file_path = sandbox.get_workspace_path(path)
            except SandboxError as e:
                raise HandlerError(f"Invalid path: {e}") from e

            # Check file exists
            if not file_path.exists():
                raise HandlerError(f"File not found: {path}")

            if not file_path.is_file():
                raise HandlerError(f"Not a file: {path}")

            # Read file with offset and length
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    # Seek to offset
                    if offset > 0:
                        f.seek(offset)

                    # Read up to length bytes
                    content = f.read(length)
                    bytes_read = len(content.encode(encoding))

            except UnicodeDecodeError as e:
                raise HandlerError(
                    f"Failed to decode file with encoding {encoding}: {e}"
                ) from e
            except Exception as e:
                raise HandlerError(f"Failed to read file: {e}") from e

            # Build artifacts
            artifacts = {
                "content": content,
                "bytes_read": bytes_read,
                "encoding": encoding,
                "offset": offset,
                "path": path
            }

            return artifacts

        except HandlerError:
            raise
        except Exception as e:
            raise HandlerError(f"fs.read handler failed: {e}") from e
