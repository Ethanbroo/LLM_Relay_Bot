"""Blog Generator — LLM API Call Handler.

Handles the actual LLM API calls with support for multiple providers
(Anthropic, OpenAI, DeepSeek). The pipeline orchestrator manages the
fallback chain; this module handles individual API calls.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def generate(
    system_prompt: str,
    user_message: str,
    model_name: str = "claude-sonnet-4-20250514",
    provider: str = "anthropic",
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """Generate blog content via LLM API call.

    Args:
        system_prompt: The full system prompt (static + any calibration overrides).
        user_message: The assembled per-blog instruction block.
        model_name: Model identifier for the API.
        provider: One of "anthropic", "openai", "deepseek".
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.

    Returns the raw LLM output text.

    Raises:
        RuntimeError: If the API call fails.
        ValueError: If the provider is unsupported.
    """
    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_message, model_name, temperature, max_tokens)
    elif provider == "openai":
        return _call_openai(system_prompt, user_message, model_name, temperature, max_tokens)
    elif provider == "deepseek":
        return _call_deepseek(system_prompt, user_message, model_name, temperature, max_tokens)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


def _call_anthropic(
    system_prompt: str,
    user_message: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call the Anthropic (Claude) API."""
    api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("No Anthropic API key (LLM_RELAY_SECRET_ANTHROPIC_API_KEY).")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    logger.info(
        "Calling Anthropic: model=%s temp=%.1f max_tokens=%d",
        model_name, temperature, max_tokens,
    )

    message = client.messages.create(
        model=model_name,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text
    logger.info(
        "Anthropic response: %d chars, stop_reason=%s",
        len(raw), message.stop_reason,
    )

    if message.stop_reason == "max_tokens":
        logger.warning("Output was truncated (hit max_tokens=%d).", max_tokens)

    return raw


def _call_openai(
    system_prompt: str,
    user_message: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call the OpenAI (GPT) API."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No OpenAI API key (OPENAI_API_KEY).")

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "openai package not installed. Install with: pip install openai"
        )

    client = OpenAI(api_key=api_key)

    logger.info(
        "Calling OpenAI: model=%s temp=%.1f max_tokens=%d",
        model_name, temperature, max_tokens,
    )

    response = client.chat.completions.create(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason
    logger.info(
        "OpenAI response: %d chars, finish_reason=%s",
        len(raw), finish_reason,
    )

    if finish_reason == "length":
        logger.warning("Output was truncated (hit max_tokens=%d).", max_tokens)

    return raw


def _call_deepseek(
    system_prompt: str,
    user_message: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call the DeepSeek API (OpenAI-compatible endpoint)."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("No DeepSeek API key (DEEPSEEK_API_KEY).")

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "openai package not installed. Install with: pip install openai"
        )

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    logger.info(
        "Calling DeepSeek: model=%s temp=%.1f max_tokens=%d",
        model_name, temperature, max_tokens,
    )

    response = client.chat.completions.create(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason
    logger.info(
        "DeepSeek response: %d chars, finish_reason=%s",
        len(raw), finish_reason,
    )

    if finish_reason == "length":
        logger.warning("Output was truncated (hit max_tokens=%d).", max_tokens)

    return raw
