# telegram_bot/project_registry.py
"""
Project registry interface and filesystem-based stub implementation.

The registry tracks projects that have been built by the relay bot.
Phase 1 uses a FilesystemProjectRegistry that scans workspace directories.
Phase 3 replaces it with RedisProjectRegistry that stores richer metadata
(semantic anchors, session IDs, architecture plans, file manifests).

Both implementations conform to ProjectRegistryProtocol, so Phase 1
code never needs to change when the backend switches.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectInfo:
    """Minimal project metadata returned by the registry.

    Phase 3 extends this with semantic_anchor, architecture_plan,
    session_ids, file_manifest, and cost_history. Phase 1 only needs
    the fields below for project selection and display.
    """

    name: str                       # Directory name (e.g., "customer-feedback-form")
    display_name: str               # Human-friendly name (e.g., "Customer Feedback Form")
    path: Path                      # Absolute path on disk
    last_modified_timestamp: float  # For sorting by recency
    file_count: int                 # Number of files in the project


@runtime_checkable
class ProjectRegistryProtocol(Protocol):
    """Interface that all registry implementations must satisfy.

    Using a Protocol (structural subtyping) instead of an ABC means
    the filesystem stub and the Redis implementation don't need to
    inherit from a common base class. They just need to have these
    methods with matching signatures.
    """

    def list_projects(self, limit: int = 20) -> list[ProjectInfo]:
        """Return projects sorted by most recently modified first."""
        ...

    def find_project(self, query: str) -> Optional[ProjectInfo]:
        """Find a single project by name. Case-insensitive substring match.
        Returns the best match, or None if no match found."""
        ...

    def get_project(self, name: str) -> Optional[ProjectInfo]:
        """Get a project by exact directory name."""
        ...


class FilesystemProjectRegistry:
    """Scans workspace directories to discover projects.

    Treats any subdirectory of the workspace root that contains at
    least one non-hidden file as a project.

    Section 4: Optionally enriched with Redis metadata (session count,
    last build timestamp, tech stack). Filesystem remains ground truth
    for "what projects exist."

    Projects are sorted by most-recently-modified first (based on
    the most recent mtime of any file in the directory).
    """

    def __init__(self, workspace_path: Path, redis_client=None):
        self._workspace = workspace_path
        self._redis = redis_client

    def list_projects(self, limit: int = 20) -> list[ProjectInfo]:
        if not self._workspace.exists():
            return []

        projects = []
        for entry in self._workspace.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue

            # Count non-hidden files
            files = [
                f
                for f in entry.rglob("*")
                if f.is_file()
                and not f.name.startswith(".")
                and "node_modules" not in f.parts
                and "__pycache__" not in f.parts
                and ".git" not in f.parts
            ]

            if not files:
                continue

            # Most recent file modification time
            latest_mtime = max(f.stat().st_mtime for f in files)

            projects.append(
                ProjectInfo(
                    name=entry.name,
                    display_name=self._name_to_display(entry.name),
                    path=entry,
                    last_modified_timestamp=latest_mtime,
                    file_count=len(files),
                )
            )

        # Sort by most recently modified first
        projects.sort(key=lambda p: p.last_modified_timestamp, reverse=True)
        return projects[:limit]

    def find_project(self, query: str) -> Optional[ProjectInfo]:
        """Case-insensitive substring match against project names.

        Matches against both the directory name and the display name.
        Returns the most recently modified match if multiple match.
        """
        query_lower = query.lower().strip()
        if not query_lower:
            return None

        matches = []
        for project in self.list_projects(limit=100):
            if (
                query_lower in project.name.lower()
                or query_lower in project.display_name.lower()
            ):
                matches.append(project)

        if not matches:
            return None

        # Return the most recently modified match
        return matches[0]

    def get_project(self, name: str) -> Optional[ProjectInfo]:
        project_path = self._workspace / name
        if not project_path.is_dir():
            return None

        files = [
            f
            for f in project_path.rglob("*")
            if f.is_file() and not f.name.startswith(".")
        ]
        if not files:
            return None

        return ProjectInfo(
            name=name,
            display_name=self._name_to_display(name),
            path=project_path,
            last_modified_timestamp=max(f.stat().st_mtime for f in files),
            file_count=len(files),
        )

    async def get_project_info(self, project_name: str) -> dict:
        """Get combined filesystem + Redis metadata for a project.

        Returns a dict with filesystem data (always present) plus Redis
        metadata (session counts, build/edit totals) if Redis is available.
        """
        project = self.get_project(project_name)
        info = {
            "name": project_name,
            "path": str(self._workspace / project_name),
            "exists_on_disk": project is not None,
            "display_name": self._name_to_display(project_name),
            "file_count": project.file_count if project else 0,
            "last_modified": project.last_modified_timestamp if project else 0,
        }

        if self._redis and project:
            try:
                meta = await self._redis.hgetall(f"project:{project_name}:meta")
                if meta:
                    info["total_builds"] = int(meta.get("total_builds", 0))
                    info["total_edits"] = int(meta.get("total_edits", 0))
                    info["created_at"] = meta.get("created_at", "")
                    info["tech_stack"] = meta.get("tech_stack", "")
                    info["last_session_id"] = meta.get("last_session_id", "")
            except Exception as e:
                logger.warning("Redis metadata fetch failed for %s: %s", project_name, e)

        return info

    async def increment_build_count(self, project_name: str) -> None:
        """Increment the total_builds counter for a project in Redis."""
        if not self._redis:
            return
        key = f"project:{project_name}:meta"
        await self._redis.hincrby(key, "total_builds", 1)

    async def increment_edit_count(self, project_name: str) -> None:
        """Increment the total_edits counter for a project in Redis."""
        if not self._redis:
            return
        key = f"project:{project_name}:meta"
        await self._redis.hincrby(key, "total_edits", 1)

    async def update_project_meta(self, project_name: str, **fields) -> None:
        """Update arbitrary Redis metadata fields for a project."""
        if not self._redis:
            return
        key = f"project:{project_name}:meta"
        if fields:
            await self._redis.hset(key, mapping={k: str(v) for k, v in fields.items()})

    @staticmethod
    def _name_to_display(dirname: str) -> str:
        """Convert 'customer-feedback-form' to 'Customer Feedback Form'."""
        return dirname.replace("-", " ").replace("_", " ").title()
