"""Pydantic model for fs.list_dir action."""

from pydantic import BaseModel, Field, field_validator, ConfigDict
from pathlib import Path
import unicodedata


class FsListDirAction(BaseModel):
    """
    Filesystem list directory action payload.

    Same strict path validation as fs.read.
    """

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
    )

    path: str = Field(
        min_length=1,
        max_length=1024,
        description="Relative directory path from workspace root"
    )

    max_entries: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of entries to return"
    )

    sort_order: str = Field(
        default="name_asc",
        pattern=r'^(name_asc|name_desc|mtime_asc|mtime_desc)$',
        description="Deterministic sort order"
    )

    include_hidden: bool = Field(
        default=False,
        description="Include hidden files (starting with .)"
    )

    recursive: bool = Field(
        default=False,
        description="List recursively (subdirectories)"
    )

    @field_validator('path')
    @classmethod
    def validate_path_safety(cls, v: str) -> str:
        """Validate path is safe (same rules as fs.read)."""
        # Reject null bytes
        if '\x00' in v:
            raise ValueError("Path contains null byte")

        # Reject control characters
        if any(ord(c) < 32 for c in v if c not in '\n\t'):
            raise ValueError("Path contains control characters")

        # Unicode NFC normalization
        normalized = unicodedata.normalize('NFC', v)

        # Reject absolute paths
        if normalized.startswith('/') or normalized.startswith('\\'):
            raise ValueError("Absolute paths not allowed")

        # Reject Windows drive letters
        if len(normalized) >= 2 and normalized[1] == ':':
            raise ValueError("Windows drive letters not allowed")

        # Normalize separators
        normalized = normalized.replace('\\', '/')

        # Check for parent directory traversal
        path_obj = Path(normalized)
        parts = path_obj.parts
        for part in parts:
            if part == '..':
                raise ValueError("Parent directory traversal (..) not allowed")

        return normalized
