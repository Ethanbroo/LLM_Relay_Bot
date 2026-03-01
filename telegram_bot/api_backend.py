"""
API backend adapter — wraps RealClaudeClient to satisfy the LLMBackend protocol.

This adapter lives inside telegram_bot/ (not multi_agent_v2/) to maintain
the isolation boundary. multi_agent_v2/real_claude.py is READ ONLY.

Used in local dev mode when CLAUDE_MAX_SESSION_TOKEN is not set.
"""

from __future__ import annotations

import asyncio
import logging

from multi_agent_v2.real_claude import RealClaudeClient as _RealClaudeClient

logger = logging.getLogger(__name__)


class APIBackend:
    """Wraps RealClaudeClient to satisfy the LLMBackend protocol.

    Provides both the classify() method (for MessageClassifier) and a
    general-purpose run() method (for handlers that need LLM calls).
    """

    def __init__(self, api_key: str | None = None):
        """
        Args:
            api_key: Anthropic API key. If None, RealClaudeClient reads
                     from ANTHROPIC_API_KEY env var.
        """
        self._client = _RealClaudeClient()

    async def classify(self, prompt: str, model: str) -> str:
        """LLMBackend protocol: single-shot classification call.

        Runs the synchronous RealClaudeClient.generate() in a thread
        to avoid blocking the async event loop.
        """
        response = await asyncio.to_thread(
            self._client.generate,
            "You are a helpful assistant.",
            prompt,
            model=model,
            max_tokens=50,
        )
        return response.strip()

    async def run(
        self,
        prompt: str,
        model: str = "claude-sonnet-4-5-20250929",
        max_turns: int = 1,
        session_id: str | None = None,
        system_prompt_append: str | None = None,
        **kwargs,
    ) -> "APIResponse":
        """General-purpose LLM call for handlers (Q&A, research, etc.).

        Wraps the synchronous generate() call in a thread. Returns an
        APIResponse that matches the shape handlers expect from
        ClaudeCodeClient.run().
        """
        system = system_prompt_append or "You are a helpful assistant."

        try:
            response_text = await asyncio.to_thread(
                self._client.generate,
                system,
                prompt,
                model=model,
                max_tokens=4096,
            )
            return APIResponse(
                text=response_text.strip(),
                session_id=None,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                model=model,
            )
        except Exception as e:
            logger.error("APIBackend.run() failed: %s", e, exc_info=True)
            return APIResponse(
                text="",
                session_id=None,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                model=model,
                is_error=True,
                error_message=str(e),
            )


class APIResponse:
    """Response from APIBackend.run() — matches ClaudeResponse shape."""

    def __init__(
        self,
        text: str,
        session_id: str | None,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        model: str,
        is_error: bool = False,
        error_message: str = "",
    ):
        self.text = text
        self.session_id = session_id
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model
        self.is_error = is_error
        self.error_message = error_message
