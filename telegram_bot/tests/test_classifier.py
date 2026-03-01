"""
Tests for classifier.py (MessageClassifier).

Tests the MessageClassifier with mocked Claude responses. Validates all
six intents, confidence threshold behavior, JSON parse error handling,
and graceful degradation on API failure. Does not make real API calls.
"""

from __future__ import annotations

import json
import pytest

from telegram_bot.classifier import (
    Intent,
    Classification,
    MessageClassifier,
    CLARIFICATION_THRESHOLD,
    HIGH_CONFIDENCE_THRESHOLD,
)


class FakeClaude:
    """Mock Claude client for testing the classifier."""

    def __init__(self, response: str | Exception = ""):
        self._response = response

    def generate(self, system_prompt, user_message, *, model=None, max_tokens=None, temperature=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _make_response(intent: str, confidence: float, secondary: str | None = None) -> str:
    data = {"intent": intent, "confidence": confidence}
    if secondary:
        data["secondary_intent"] = secondary
    return json.dumps(data)


@pytest.mark.asyncio
async def test_classify_new_build_high_confidence():
    client = FakeClaude(_make_response("NEW_BUILD", 0.95))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Build me a landing page")

    assert result.intent == Intent.NEW_BUILD
    assert result.confidence == 0.95
    assert result.needs_clarification is False
    assert result.raw_message == "Build me a landing page"
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_classify_edit_fix():
    client = FakeClaude(_make_response("EDIT_FIX", 0.90))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Fix the button color")

    assert result.intent == Intent.EDIT_FIX
    assert result.confidence == 0.90
    assert result.needs_clarification is False


@pytest.mark.asyncio
async def test_classify_question():
    client = FakeClaude(_make_response("QUESTION", 0.88))
    classifier = MessageClassifier(client)
    result = await classifier.classify("How does the auth flow work?")

    assert result.intent == Intent.QUESTION


@pytest.mark.asyncio
async def test_classify_research():
    client = FakeClaude(_make_response("RESEARCH", 0.92))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Research competitor pricing")

    assert result.intent == Intent.RESEARCH


@pytest.mark.asyncio
async def test_classify_external_action():
    client = FakeClaude(_make_response("EXTERNAL_ACTION", 0.91))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Create a Google Doc")

    assert result.intent == Intent.EXTERNAL_ACTION


@pytest.mark.asyncio
async def test_classify_conversational():
    client = FakeClaude(_make_response("CONVERSATIONAL", 0.95))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Hey, what's up?")

    assert result.intent == Intent.CONVERSATIONAL


@pytest.mark.asyncio
async def test_low_confidence_needs_clarification():
    client = FakeClaude(_make_response("NEW_BUILD", 0.45))
    classifier = MessageClassifier(client)
    result = await classifier.classify("I need something for the blog")

    assert result.intent == Intent.NEW_BUILD
    assert result.confidence == 0.45
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_secondary_intent():
    client = FakeClaude(_make_response("NEW_BUILD", 0.55, "EDIT_FIX"))
    classifier = MessageClassifier(client)
    result = await classifier.classify("I need a contact form")

    assert result.intent == Intent.NEW_BUILD
    assert result.secondary_intent == Intent.EDIT_FIX


@pytest.mark.asyncio
async def test_json_parse_error_graceful_degradation():
    client = FakeClaude("This is not JSON at all")
    classifier = MessageClassifier(client)
    result = await classifier.classify("some message")

    assert result.intent == Intent.CONVERSATIONAL
    assert result.confidence == 0.0
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_api_failure_graceful_degradation():
    client = FakeClaude(RuntimeError("API connection failed"))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Build me something")

    assert result.intent == Intent.CONVERSATIONAL
    assert result.confidence == 0.0
    assert result.needs_clarification is True


@pytest.mark.asyncio
async def test_markdown_fences_stripped():
    raw = "```json\n" + _make_response("RESEARCH", 0.85) + "\n```"
    client = FakeClaude(raw)
    classifier = MessageClassifier(client)
    result = await classifier.classify("Research AI trends")

    assert result.intent == Intent.RESEARCH
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_threshold_constants():
    assert CLARIFICATION_THRESHOLD == 0.60
    assert HIGH_CONFIDENCE_THRESHOLD == 0.85


@pytest.mark.asyncio
async def test_classification_is_frozen():
    client = FakeClaude(_make_response("NEW_BUILD", 0.95))
    classifier = MessageClassifier(client)
    result = await classifier.classify("Build something")

    with pytest.raises(AttributeError):
        result.intent = Intent.EDIT_FIX  # type: ignore[misc]
