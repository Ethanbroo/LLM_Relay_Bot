"""
Gemini API client for intent classification.

A thin wrapper around Google's google-genai SDK that exposes the same
generate() interface as RealClaudeClient, allowing it to be used as a
drop-in replacement for the classifier backend.

Uses Gemini Flash (free tier) for intent classification — same quality
as Haiku for simple JSON classification tasks, but at zero cost.

Free tier limits (as of 2025): ~15 RPM, ~1000 RPD for Flash models.
More than sufficient for a single-user or small-group bot.

Requires GEMINI_API_KEY environment variable (get one free at
https://aistudio.google.com/apikey).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiClassifierClient:
    """Gemini API wrapper matching RealClaudeClient.generate() interface.

    Only implements the subset needed by MessageClassifier:
      client.generate(system_prompt, user_message, model=..., max_tokens=..., temperature=...)
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey"
            )

        try:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        except ImportError:
            raise ImportError(
                "google-genai package not installed. Run: pip install google-genai"
            )

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Call Gemini with a system prompt and user message.

        Matches RealClaudeClient.generate() signature so MessageClassifier
        can use either backend without changes.
        """
        from google.genai import types

        # Ignore Anthropic model names — always use Gemini Flash
        gemini_model = GEMINI_DEFAULT_MODEL

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens or 150,
            temperature=temperature if temperature is not None else 0.0,
        )

        response = self._client.models.generate_content(
            model=gemini_model,
            contents=user_message,
            config=config,
        )

        return response.text
