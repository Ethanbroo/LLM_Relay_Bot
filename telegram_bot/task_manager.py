"""
Task state management for browser agent tasks.

Provides TaskState (the in-memory state object for a running task) and
TaskManager (tracking active tasks, session timeout monitoring, and
task history persistence to Redis).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from telegram_bot.action_classifier import extract_domains_from_text

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_SECONDS = int(os.environ.get("SESSION_TIMEOUT_SECONDS", "1800"))
TASK_HISTORY_RETENTION_DAYS = int(os.environ.get("TASK_HISTORY_RETENTION_DAYS", "7"))


class TaskState:
    """In-memory state for a single browser automation task."""

    def __init__(self, user_task: str, chat_id: int, user_id: int):
        self.task_id: str = secrets.token_hex(6)
        self.user_task: str = user_task
        self.chat_id: int = chat_id
        self.user_id: int = user_id
        self.session_id: str | None = None
        self.expected_domains: set[str] = extract_domains_from_text(user_task)
        self.action_history: list[dict] = []
        self.step_count: int = 0
        self.status: str = "running"  # running, paused, waiting_approval, completed, failed, expired, cancelled
        self.created_at: datetime = datetime.now(timezone.utc)
        self.last_activity: datetime = datetime.now(timezone.utc)
        self.current_url: str = ""
        self.approval_message_id: int | None = None
        self.last_approved_domain: str | None = None
        self.last_approval_time: datetime | None = None
        self.total_tokens: int = 0

    def touch(self):
        """Update last_activity timestamp."""
        self.last_activity = datetime.now(timezone.utc)

    def to_history_dict(self) -> dict[str, Any]:
        """Serialize to a dict for Redis storage."""
        return {
            "task_id": self.task_id,
            "user_task": self.user_task,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "status": self.status,
            "step_count": self.step_count,
            "created_at": self.created_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "total_tokens": self.total_tokens,
        }


class TaskManager:
    """Manages active browser tasks and persists history to Redis."""

    def __init__(self, redis_client=None):
        self._active: dict[str, TaskState] = {}
        self._redis = redis_client
        self._monitor_task: asyncio.Task | None = None

    def create_task(self, user_task: str, chat_id: int, user_id: int) -> TaskState:
        """Create and register a new task."""
        state = TaskState(user_task, chat_id, user_id)
        self._active[state.task_id] = state
        return state

    def get_task(self, task_id: str) -> TaskState | None:
        return self._active.get(task_id)

    def get_active_tasks(self) -> list[TaskState]:
        return list(self._active.values())

    def get_tasks_for_user(self, user_id: int) -> list[TaskState]:
        return [t for t in self._active.values() if t.user_id == user_id]

    def remove_task(self, task_id: str):
        self._active.pop(task_id, None)

    async def complete_task(self, task_id: str, status: str = "completed"):
        """Mark a task as complete and save to history."""
        task = self._active.pop(task_id, None)
        if task:
            task.status = status
            await self._save_history(task)

    async def _save_history(self, task: TaskState):
        """Save completed task to Redis history."""
        if not self._redis:
            return
        try:
            key = f"browser_task_history:{task.user_id}"
            entry = json.dumps(task.to_history_dict())
            await self._redis.lpush(key, entry)
            # Trim to keep last 50 entries
            await self._redis.ltrim(key, 0, 49)
            # Set expiry on the list
            await self._redis.expire(
                key, TASK_HISTORY_RETENTION_DAYS * 86400
            )
        except Exception:
            logger.warning("Failed to save task history to Redis", exc_info=True)

    async def get_history(self, user_id: int, limit: int = 10) -> list[dict]:
        """Retrieve task history from Redis."""
        if not self._redis:
            return []
        try:
            key = f"browser_task_history:{user_id}"
            entries = await self._redis.lrange(key, 0, limit - 1)
            return [json.loads(e) for e in entries]
        except Exception:
            logger.warning("Failed to load task history from Redis", exc_info=True)
            return []

    def start_monitor(self, browser_client, bot):
        """Start the session timeout monitor background task."""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._session_timeout_monitor(browser_client, bot)
            )

    async def stop_monitor(self):
        """Stop the session timeout monitor."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _session_timeout_monitor(self, browser_client, bot):
        """Background task that cleans up inactive browser sessions."""
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)

            for task in list(self._active.values()):
                idle_seconds = (now - task.last_activity).total_seconds()
                if idle_seconds > SESSION_TIMEOUT_SECONDS and task.session_id:
                    try:
                        await browser_client.destroy_session(task.session_id)
                    except Exception:
                        pass

                    task.status = "expired"
                    old_session = task.session_id
                    task.session_id = None

                    try:
                        timeout_mins = SESSION_TIMEOUT_SECONDS // 60
                        desc = task.user_task[:50]
                        await bot.send_message(
                            chat_id=task.chat_id,
                            text=(
                                f"Browser session for task \"{desc}\" was closed "
                                f"after {timeout_mins} minutes of inactivity.\n"
                                f"Steps completed: {task.step_count}\n"
                                f"Use /history to see details."
                            ),
                        )
                    except Exception:
                        logger.warning("Failed to notify user of session timeout")

                    await self.complete_task(task.task_id, status="expired")
                    logger.info(
                        "Session %s expired for task %s",
                        old_session[:8] if old_session else "?",
                        task.task_id,
                    )
