"""
Claude Code CLI wrapper for VPS mode.

Calls `claude -p` as a subprocess for headless LLM interactions.
Runs INSIDE the relay-bot container (which has Node.js + Claude Code
installed in the Dockerfile).

Two output modes:
  - run()            → --output-format json   → single ClaudeResponse
  - run_streaming()  → --output-format stream-json → AsyncIterator[dict]
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class ClaudeResponse:
    """Parsed response from a claude -p call."""
    text: str                    # The LLM's text output
    session_id: str | None       # Session ID for --resume
    cost_usd: float              # Cost in USD (from Max subscription tracking)
    input_tokens: int
    output_tokens: int
    model: str
    is_error: bool = False
    error_message: str = ""


class ClaudeCodeClient:
    """Async wrapper around the Claude Code CLI (claude -p).

    Satisfies the LLMBackend protocol via classify() and provides
    run() / run_streaming() for general-purpose LLM calls.
    """

    def __init__(
        self,
        session_token: str | None = None,
        workspace_path: str = "/app/workspace",
        default_timeout: int = 300,
    ):
        self.session_token = session_token
        self.workspace_path = workspace_path
        self.default_timeout = default_timeout

        # Environment for subprocess — inherits current env + adds auth.
        # os.environ.copy() is critical: passing a partial env= dict to
        # create_subprocess_exec REPLACES the entire environment.
        self._env = os.environ.copy()
        if session_token and session_token != "api-key-auth":
            # Real OAuth token (Max subscription) — set explicitly.
            # When session_token is "api-key-auth", the CLI authenticates
            # via ANTHROPIC_API_KEY already present in the inherited env.
            self._env["CLAUDE_CODE_OAUTH_TOKEN"] = session_token

    async def classify(self, prompt: str, model: str) -> str:
        """LLMBackend protocol: single-shot classification call."""
        response = await self.run(
            prompt=prompt,
            model=model,
            max_turns=1,
            timeout=30,
        )
        return response.text

    async def run(
        self,
        prompt: str,
        model: str = "sonnet",
        max_turns: int = 10,
        timeout: int | None = None,
        session_id: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        system_prompt_append: str | None = None,
    ) -> ClaudeResponse:
        """Execute a claude -p call and return the parsed response."""

        cmd = ["claude", "-p", prompt, "--output-format", "json"]

        if model and model != "sonnet":
            cmd += ["--model", model]

        cmd += ["--max-turns", str(max_turns)]

        if session_id:
            cmd += ["--resume", session_id]

        if allowed_tools:
            cmd += ["--allowedTools"] + allowed_tools
        if disallowed_tools:
            cmd += ["--disallowedTools"] + disallowed_tools

        if system_prompt_append:
            cmd += ["--append-system-prompt", system_prompt_append]

        effective_timeout = timeout or self.default_timeout
        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_path,
                env=self._env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if process.returncode != 0:
                logger.error(
                    "claude -p exited %d: %s",
                    process.returncode, stderr[:500],
                )
                return ClaudeResponse(
                    text="",
                    session_id=None,
                    cost_usd=0.0,
                    input_tokens=0,
                    output_tokens=0,
                    model=model,
                    is_error=True,
                    error_message=self._parse_error(stderr),
                )

            return self._parse_json_response(stdout, model)

        except asyncio.TimeoutError:
            logger.error("claude -p timed out after %ds", effective_timeout)
            if process:
                process.kill()
            return ClaudeResponse(
                text="",
                session_id=None,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                model=model,
                is_error=True,
                error_message=f"Timed out after {effective_timeout}s",
            )

    async def run_streaming(
        self,
        prompt: str,
        model: str = "sonnet",
        max_turns: int = 10,
        session_id: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        system_prompt_append: str | None = None,
    ) -> AsyncIterator[dict]:
        """Execute claude -p with streaming JSON output (NDJSON).

        Yields parsed JSON events as they arrive.
        Used by pipeline_adapter.py for real-time progress.
        """
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json"]

        if model and model != "sonnet":
            cmd += ["--model", model]
        cmd += ["--max-turns", str(max_turns)]
        if session_id:
            cmd += ["--resume", session_id]
        if allowed_tools:
            cmd += ["--allowedTools"] + allowed_tools
        if disallowed_tools:
            cmd += ["--disallowedTools"] + disallowed_tools
        if system_prompt_append:
            cmd += ["--append-system-prompt", system_prompt_append]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workspace_path,
            env=self._env,
        )

        # Read stdout line-by-line (NDJSON: one JSON object per line)
        async for line in process.stdout:
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            try:
                event = json.loads(decoded)
                yield event
            except json.JSONDecodeError:
                logger.warning("Non-JSON line from claude -p: %s", decoded[:200])

        await process.wait()

        if process.returncode != 0:
            stderr = await process.stderr.read()
            logger.error(
                "Streaming claude -p exited %d: %s",
                process.returncode, stderr.decode()[:500],
            )

    def _parse_json_response(self, stdout: str, model: str) -> ClaudeResponse:
        """Parse the JSON output from claude -p --output-format json."""
        try:
            data = json.loads(stdout)
            return ClaudeResponse(
                text=data.get("result", ""),
                session_id=data.get("session_id"),
                cost_usd=data.get("cost_usd", 0.0),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                model=data.get("model", model),
            )
        except json.JSONDecodeError:
            logger.error("Failed to parse claude -p JSON: %s", stdout[:500])
            return ClaudeResponse(
                text=stdout,
                session_id=None,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                model=model,
                is_error=True,
                error_message="Failed to parse JSON response",
            )

    def _parse_error(self, stderr: str) -> str:
        """Extract a human-readable error from claude -p stderr."""
        if "429" in stderr or "rate limit" in stderr.lower():
            return "Rate limited by Claude Max subscription. Retry in a few minutes."
        if "unauthorized" in stderr.lower() or "401" in stderr:
            return "Authentication failed. Check CLAUDE_MAX_SESSION_TOKEN."
        return stderr[:300] if stderr else "Unknown error"
