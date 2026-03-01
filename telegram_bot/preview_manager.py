"""Live preview lifecycle management.

Manages starting a dev server inside the claude-code container,
exposing it through Nginx, and auto-cleanup after timeout.

Only ONE preview at a time — multiple concurrent previews would need
multiple ports and dynamic Nginx config. For a single-user system,
one preview at a time is sufficient.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class PreviewManager:
    """Manages live preview processes inside the claude-code container."""

    def __init__(self, claude_client, config, redis_client=None):
        self.claude = claude_client
        self.config = config
        self.redis = redis_client
        self._active_preview: dict | None = None
        self._cleanup_task: asyncio.Task | None = None

    async def start(self, project_name: str) -> dict:
        """Start a live preview for a project.

        Returns:
            {"url": str, "project": str, "started_at": float}
            or {"error": str} on failure.
        """
        # Kill any existing preview first
        if self._active_preview:
            await self.stop()

        # Detect project type and start command
        start_cmd = await self._detect_start_command(project_name)
        if not start_cmd:
            return {
                "error": (
                    f"Cannot detect how to preview '{project_name}'. "
                    f"No package.json, requirements.txt, or recognized entry point found."
                )
            }

        # Start the dev server as a background process
        response = await self.claude.run(
            prompt=(
                f"Start the development server for the project in /workspace/{project_name}. "
                f"Run: {start_cmd}\n"
                f"The server should listen on port 3000. "
                f"If the default port is different, set PORT=3000 in the environment. "
                f"Run it in the background (append & to the command). "
                f"Verify the server is running by checking if port 3000 is listening."
            ),
            model="haiku",
            max_turns=3,
            timeout=60,
            allowed_tools=["Bash", "Read"],
            disallowed_tools=["Edit", "Write", "web_search"],
        )

        if response.is_error:
            return {"error": f"Failed to start preview: {response.error_message}"}

        preview_url = f"https://{self.config.domain}/preview/{project_name}/"

        self._active_preview = {
            "project": project_name,
            "url": preview_url,
            "started_at": time.time(),
            "port": 3000,
        }

        # Schedule auto-cleanup
        self._cleanup_task = asyncio.create_task(
            self._auto_cleanup(self.config.preview_timeout_seconds)
        )

        # Store in Redis for /status visibility
        if self.redis:
            await self.redis.hset("preview:active", mapping={
                "project": project_name,
                "url": preview_url,
                "started_at": str(time.time()),
            })
            await self.redis.expire("preview:active", self.config.preview_timeout_seconds)

        logger.info("Preview started: %s", preview_url)
        return self._active_preview

    async def stop(self) -> None:
        """Stop the active preview."""
        if not self._active_preview:
            return

        project_name = self._active_preview["project"]

        # Kill the dev server process
        response = await self.claude.run(
            prompt=(
                f"Kill any running dev server processes for /workspace/{project_name}. "
                f"Use: pkill -f 'node.*{project_name}' or kill the process on port 3000. "
                f"Verify port 3000 is no longer listening."
            ),
            model="haiku",
            max_turns=2,
            timeout=30,
            allowed_tools=["Bash"],
            disallowed_tools=["Edit", "Write", "Read", "web_search"],
        )

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

        if self.redis:
            await self.redis.delete("preview:active")

        logger.info("Preview stopped: %s", project_name)
        self._active_preview = None

    async def status(self) -> dict | None:
        """Get the active preview status."""
        if not self._active_preview:
            return None

        elapsed = time.time() - self._active_preview["started_at"]
        remaining = self.config.preview_timeout_seconds - elapsed

        return {
            **self._active_preview,
            "elapsed_seconds": int(elapsed),
            "remaining_seconds": max(0, int(remaining)),
        }

    async def _auto_cleanup(self, timeout_seconds: int):
        """Background task: auto-stop preview after timeout."""
        try:
            await asyncio.sleep(timeout_seconds)
            logger.info("Preview auto-cleanup after %ds", timeout_seconds)
            await self.stop()
        except asyncio.CancelledError:
            pass  # Task was cancelled (preview manually stopped)

    async def _detect_start_command(self, project_name: str) -> str | None:
        """Detect the appropriate start command based on project type."""
        response = await self.claude.run(
            prompt=(
                f"Check what type of project is in /workspace/{project_name}. "
                f"Look for package.json, requirements.txt, Cargo.toml, go.mod, etc. "
                f"Return ONLY the start command as a single line, nothing else. Examples:\n"
                f"- Node.js: npm start\n"
                f"- Python: python -m flask run --port 3000\n"
                f"- Next.js: npx next dev -p 3000\n"
                f"- Vite: npx vite --port 3000\n"
                f"If you can't determine the project type, return: UNKNOWN"
            ),
            model="haiku",
            max_turns=2,
            timeout=30,
            allowed_tools=["Read", "Bash"],
            disallowed_tools=["Edit", "Write", "web_search"],
        )

        if response.is_error or "UNKNOWN" in response.text.upper():
            return None

        return response.text.strip()
