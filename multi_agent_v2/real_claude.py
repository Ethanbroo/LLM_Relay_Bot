"""
Real Claude API client for multi_agent_v2.

A thin, direct wrapper around the Anthropic SDK.
No stubs. Requires ANTHROPIC_API_KEY in environment.

This is separate from llm_integration/claude_client.py (which is tied to
the v1.0 Phase 8 request/response schema). This one takes a plain system
prompt + user message and returns a plain string — exactly what the
v2.0 agents need.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


class RealClaudeClient:
    """
    Direct Claude API wrapper for v2.0 agents.

    Exposes a single method: generate(system_prompt, user_message) -> str
    Compatible with all v2.0 agent expectations.
    """

    MODEL = "claude-sonnet-4-5-20250929"
    MAX_TOKENS = 4096

    def __init__(self) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            print(
                "\n[ERROR] ANTHROPIC_API_KEY is not set.\n"
                "Set it with:\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            )
            sys.exit(1)

        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        except ImportError:
            print("\n[ERROR] anthropic package not installed. Run: pip install anthropic\n")
            sys.exit(1)

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Call Claude with a system prompt and user message.
        Returns the text response as a plain string.

        Args:
            system_prompt: The system prompt for Claude.
            user_message: The user message to send.
            model: Override the default model (e.g., "claude-haiku-4-5-20251001"
                   for fast/cheap classification calls).
            max_tokens: Override the default max_tokens.
            temperature: Set temperature (default: not sent, uses API default).
        """
        kwargs = {
            "model": model or self.MODEL,
            "max_tokens": max_tokens or self.MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        message = self._client.messages.create(**kwargs)
        return message.content[0].text

    def generate_combined(self, combined_prompt: str) -> str:
        """
        Convenience method: takes a single combined prompt string.
        Used by agents that call generate(prompt) with one argument.
        Splits on the first double-newline to separate system from user context.
        """
        # Agents pass system + user as one string; route it through as user message
        return self.generate(
            system_prompt="You are a helpful assistant.",
            user_message=combined_prompt,
        )


class AgentLLMAdapter:
    """
    Adapter that presents RealClaudeClient to v2.0 agents.

    Agents call self._llm.generate(prompt) with ONE argument (combined).
    This adapter routes that correctly to the real API.
    """

    def __init__(self, client: RealClaudeClient) -> None:
        self._client = client

    def generate(self, prompt: str) -> str:
        """Called by CriticalThinkingAgent, SemanticAnchorAgent, etc."""
        return self._client.generate_combined(prompt)

    def chat(self, system: str, user: str) -> str:
        """Alternative interface used by some agents."""
        return self._client.generate(system, user)
