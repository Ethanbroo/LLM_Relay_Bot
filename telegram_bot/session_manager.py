"""Session CRUD operations backed by Redis.

Provides a clean API for session lifecycle management that all handlers
use instead of writing raw Redis commands.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SESSION_TTL = 30 * 86400       # 30 days
PROGRESS_TTL = 24 * 3600       # 24 hours


@dataclass
class SessionRecord:
    """Deserialized session from Redis."""
    session_id: str
    project_name: str
    semantic_anchor: str = ""
    architecture_plan: dict[str, Any] | None = None
    file_manifest: list[str] = field(default_factory=list)
    review_status: str = ""
    review_score: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    created_at: float = 0.0
    last_accessed: float = 0.0
    phase_reached: int = 0
    handoff_written: bool = False


class SessionManager:
    """Manages session lifecycle in Redis."""

    def __init__(self, redis_client):
        self.redis = redis_client

    # ── CRUD ─────────────────────────────────────────────────

    async def save(self, session: SessionRecord) -> None:
        """Save or update a session record."""
        if not self.redis:
            return

        data = {
            "session_id": session.session_id,
            "project_name": session.project_name,
            "semantic_anchor": session.semantic_anchor,
            "architecture_plan": json.dumps(session.architecture_plan) if session.architecture_plan else "",
            "file_manifest": json.dumps(session.file_manifest),
            "review_status": session.review_status,
            "review_score": str(session.review_score),
            "total_tokens": str(session.total_tokens),
            "cost_usd": str(session.cost_usd),
            "created_at": str(session.created_at or time.time()),
            "last_accessed": str(time.time()),
            "phase_reached": str(session.phase_reached),
            "handoff_written": str(session.handoff_written).lower(),
        }

        pipe = self.redis.pipeline()
        pipe.hset(f"session:{session.session_id}", mapping=data)
        pipe.expire(f"session:{session.session_id}", SESSION_TTL)
        pipe.zadd(
            f"project:{session.project_name}:sessions",
            {session.session_id: time.time()},
        )
        await pipe.execute()

    async def get(self, session_id: str) -> SessionRecord | None:
        """Retrieve a session by ID. Refreshes TTL on access."""
        if not self.redis:
            return None

        data = await self.redis.hgetall(f"session:{session_id}")
        if not data:
            return None

        # Refresh TTL
        pipe = self.redis.pipeline()
        pipe.hset(f"session:{session_id}", "last_accessed", str(time.time()))
        pipe.expire(f"session:{session_id}", SESSION_TTL)
        await pipe.execute()

        return self._deserialize(data)

    async def get_latest_for_project(self, project_name: str) -> SessionRecord | None:
        """Get the most recent session for a project."""
        if not self.redis:
            return None

        session_ids = await self.redis.zrevrange(
            f"project:{project_name}:sessions", 0, 0,
        )
        if not session_ids:
            return None

        session = await self.get(session_ids[0])

        # If session hash expired but sorted set entry remains, clean up
        if session is None:
            await self.redis.zrem(f"project:{project_name}:sessions", session_ids[0])
            # Try next most recent
            session_ids = await self.redis.zrevrange(
                f"project:{project_name}:sessions", 0, 0,
            )
            if session_ids:
                return await self.get(session_ids[0])

        return session

    async def list_for_project(self, project_name: str, limit: int = 10) -> list[SessionRecord]:
        """List sessions for a project, newest first."""
        if not self.redis:
            return []

        session_ids = await self.redis.zrevrange(
            f"project:{project_name}:sessions", 0, limit - 1,
        )

        sessions = []
        for sid in session_ids:
            s = await self.get(sid)
            if s:
                sessions.append(s)
        return sessions

    async def delete(self, session_id: str) -> None:
        """Delete a session and remove from project's sorted set."""
        if not self.redis:
            return

        data = await self.redis.hgetall(f"session:{session_id}")
        if data:
            project_name = data.get("project_name", "")
            pipe = self.redis.pipeline()
            pipe.delete(f"session:{session_id}")
            pipe.zrem(f"project:{project_name}:sessions", session_id)
            await pipe.execute()

    # ── Progress tracking ────────────────────────────────────

    async def update_progress(
        self, session_id: str, phase: int, phase_name: str,
        tokens_used: int, files_created: int, status: str = "running",
    ) -> None:
        """Update live progress for a running pipeline."""
        if not self.redis:
            return

        data = {
            "current_phase": str(phase),
            "phase_name": phase_name,
            "tokens_used": str(tokens_used),
            "files_created": str(files_created),
            "last_update": str(time.time()),
            "status": status,
        }
        pipe = self.redis.pipeline()
        pipe.hset(f"progress:{session_id}", mapping=data)
        pipe.expire(f"progress:{session_id}", PROGRESS_TTL)
        await pipe.execute()

    async def get_progress(self, session_id: str) -> dict | None:
        """Get current pipeline progress for a session."""
        if not self.redis:
            return None
        data = await self.redis.hgetall(f"progress:{session_id}")
        return data if data else None

    # ── Deserialization ──────────────────────────────────────

    def _deserialize(self, data: dict[str, str]) -> SessionRecord:
        arch_plan = None
        if data.get("architecture_plan"):
            try:
                arch_plan = json.loads(data["architecture_plan"])
            except json.JSONDecodeError:
                pass

        file_manifest = []
        if data.get("file_manifest"):
            try:
                file_manifest = json.loads(data["file_manifest"])
            except json.JSONDecodeError:
                pass

        return SessionRecord(
            session_id=data.get("session_id", ""),
            project_name=data.get("project_name", ""),
            semantic_anchor=data.get("semantic_anchor", ""),
            architecture_plan=arch_plan,
            file_manifest=file_manifest,
            review_status=data.get("review_status", ""),
            review_score=int(data.get("review_score", "0")),
            total_tokens=int(data.get("total_tokens", "0")),
            cost_usd=float(data.get("cost_usd", "0")),
            created_at=float(data.get("created_at", "0")),
            last_accessed=float(data.get("last_accessed", "0")),
            phase_reached=int(data.get("phase_reached", "0")),
            handoff_written=data.get("handoff_written", "false") == "true",
        )
