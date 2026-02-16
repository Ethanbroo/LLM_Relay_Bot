"""Pydantic model for fs.read action."""

from pydantic import BaseModel, Field, field_validator, ConfigDict
from pathlib import Path
import unicodedata


class FsReadAction(BaseModel):
    """
    Filesystem read action payload.

    Strict path validation:
    - No absolute paths
    - No .. (parent directory traversal)
    - No null bytes
    - Unicode NFC normalization
    """

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
    )

    path: str = Field(
        min_length=1,
        max_length=1024,
        description="Relative path from workspace root"
    )

    offset: int = Field(
        default=0,
        ge=0,
        le=10485760,  # 10MB max offset
        description="Byte offset to start reading"
    )

    length: int = Field(
        default=1048576,
        ge=1,
        le=1048576,  # 1MB max read
        description="Maximum bytes to read"
    )

    encoding: str = Field(
        default="utf-8",
        pattern=r'^(utf-8|ascii|binary)$',
        description="File encoding"
    )

    @field_validator('path')
    @classmethod
    def validate_path_safety(cls, v: str) -> str:
        """
        Validate path is safe for workspace access.

        Rejects:
        - Absolute paths
        - Parent directory traversal (..)
        - Null bytes
        - Control characters

        Normalizes:
        - Unicode to NFC form
        - Path separators to /
        """
        # Reject null bytes
        if '\x00' in v:
            raise ValueError("Path contains null byte")

        # Reject control characters (except newline/tab which shouldn't be in paths anyway)
        if any(ord(c) < 32 for c in v if c not in '\n\t'):
            raise ValueError("Path contains control characters")

        # Unicode NFC normalization
        normalized = unicodedata.normalize('NFC', v)

        # Reject absolute paths
        if normalized.startswith('/') or normalized.startswith('\\'):
            raise ValueError("Absolute paths not allowed (must be relative to workspace)")

        # Reject Windows drive letters
        if len(normalized) >= 2 and normalized[1] == ':':
            raise ValueError("Windows drive letters not allowed (must be relative to workspace)")

        # Normalize path separators
        normalized = normalized.replace('\\', '/')

        # Check for parent directory traversal
        path_obj = Path(normalized)
        try:
            # This will raise ValueError if path tries to escape workspace
            parts = path_obj.parts
            for part in parts:
                if part == '..':
                    raise ValueError("Parent directory traversal (..) not allowed")
        except ValueError as e:
            raise ValueError(f"Invalid path: {e}")

        return normalized
