"""
MessageClassifier — Haiku intent classification with dual-mode backend.

A thin wrapper around a single Haiku API call that reads every incoming
text message and outputs a structured JSON classification. It runs before
the ConversationHandler routes the message to a specific handler, acting
as a pre-processing step that enriches the update context.

The classifier stores its result in context.user_data["last_classification"]
so downstream handlers can access it without re-classifying.

The backend is injected via the constructor — either RealClaudeClient (direct
Anthropic API, local dev) or ClaudeCodeClient (claude -p, VPS). Both satisfy
the LLMBackend protocol through structural subtyping.

Cost: ~$0.0004 per call (200 input tokens + 40 output tokens at Haiku 4.5).
At 100 messages/day, that's ~$1.20/month for classification alone.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    """The six mutually exclusive intent categories."""
    NEW_BUILD = "NEW_BUILD"
    EDIT_FIX = "EDIT_FIX"
    QUESTION = "QUESTION"
    RESEARCH = "RESEARCH"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"
    CONVERSATIONAL = "CONVERSATIONAL"


@dataclass(frozen=True)
class Classification:
    """Immutable result of a classifier call.

    Frozen dataclass ensures downstream code cannot accidentally mutate
    a classification after it's been produced and logged.
    """
    intent: Intent
    confidence: float
    secondary_intent: Optional[Intent]
    raw_message: str              # The original user message (for audit logging)
    latency_ms: float             # Classifier call duration (for cost monitoring)
    needs_clarification: bool     # True if confidence < CLARIFICATION_THRESHOLD


# Confidence below this threshold triggers a clarification question
# before routing. This value is deliberately conservative — it's better
# to ask one extra question than to route to the wrong pipeline.
CLARIFICATION_THRESHOLD = 0.60

# Confidence above this threshold means we route immediately with no
# confirmation. Between 0.60 and 0.85, we route but store the classification
# as "soft" so the user can correct it if they notice the bot misunderstood.
HIGH_CONFIDENCE_THRESHOLD = 0.85


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol for any LLM backend — direct API or Claude Code CLI.

    Uses structural subtyping (Protocol) instead of ABC to avoid import
    coupling between the classifier and its backends.
    """
    async def classify(self, prompt: str, model: str) -> str:
        """Send a classification prompt and return the raw text response."""
        ...


CLASSIFIER_SYSTEM_PROMPT = """You are the intent classifier for an AI-powered code relay bot. Your sole job is to read a user message and classify it into exactly one of six intent categories. You also assign a confidence score between 0.0 and 1.0.

<categories>
NEW_BUILD — The user wants something created from scratch. This includes code projects, websites, scripts, blog posts, social media content, videos, documents, or any other creative artifact that does not yet exist. Keywords that strongly suggest this category: "build", "create", "make", "write", "generate", "design", "set up", "start", "new".

EDIT_FIX — The user wants to change, fix, update, or extend something that already exists. The message references a prior project, a specific file, a previously built feature, or a known bug. Keywords: "change", "fix", "update", "modify", "edit", "add to", "remove", "tweak", "adjust", "that thing I built", "the one from last week".

QUESTION — The user is asking a question about code or a project that was previously built. They want an explanation, not a change. Keywords: "how does", "what does", "explain", "why did you", "can you walk me through", "what's the status of".

RESEARCH — The user wants information gathered, synthesized, or analyzed but does NOT want code or artifacts produced. They want to learn something. Keywords: "find out", "research", "what are the best", "compare", "look into", "investigate", "summarize".

EXTERNAL_ACTION — The user wants an action performed in an external service: Google Docs, Google Calendar, email, file delivery to a specific destination, video generation, or social media posting. Keywords: "create a doc", "schedule", "send to", "post to Instagram", "upload to", "email".

CONVERSATIONAL — The user is making small talk, asking about the bot's status, requesting cost information, saying hello/goodbye, or asking meta-questions about the bot's capabilities. This is the catch-all for messages that don't fit the other five categories.
</categories>

<rules>
1. Classify based on the user's PRIMARY intent. If a message contains multiple intents ("build me a landing page and research competitor pricing"), classify by the dominant action — the one that requires the most complex pipeline.
2. Confidence thresholds:
   - 0.85–1.0: Unambiguous, route immediately without confirmation.
   - 0.60–0.84: Likely correct, route but be prepared for user correction.
   - Below 0.60: Ambiguous, ask the user to clarify before routing.
3. A message referencing a previous project defaults toward EDIT_FIX unless the user explicitly says "new" or "from scratch".
4. "Hey" or "hi" alone is CONVERSATIONAL with 0.95 confidence. "Hey, build me a..." is NEW_BUILD — the greeting is incidental.
5. Never classify as EXTERNAL_ACTION unless the message explicitly names an external service or delivery destination.
</rules>

<examples>
User: "Build me a React dashboard for tracking sales metrics"
→ NEW_BUILD, 0.95

User: "Can you change the header color on the main site to dark blue?"
→ EDIT_FIX, 0.90

User: "How does the authentication work in the app you built last week?"
→ QUESTION, 0.88

User: "Research what our competitors are doing with AI chatbots on their websites"
→ RESEARCH, 0.92

User: "Create a Google Doc summarizing yesterday's meeting notes"
→ EXTERNAL_ACTION, 0.91

User: "Hey, what's up?"
→ CONVERSATIONAL, 0.95

User: "I need something for the blog"
→ NEW_BUILD, 0.55 (ambiguous — could be new post, could be edit to existing blog system)

User: "Fix it"
→ EDIT_FIX, 0.45 (ambiguous — no referent for "it", needs clarification)
</examples>

Respond with ONLY the JSON object. No preamble, no explanation, no markdown formatting."""


class MessageClassifier:
    """Classifies incoming Telegram messages into intent categories.

    Uses Claude Haiku via the existing real_claude.py wrapper to keep
    all API key management centralized. Each call costs approximately
    $0.0004 (200 input tokens + 40 output tokens at Haiku 4.5 pricing).

    Usage:
        classifier = MessageClassifier(claude_client)
        result = await classifier.classify("Build me a landing page")
        if result.needs_clarification:
            # Ask the user what they meant
        else:
            # Route to result.intent pipeline
    """

    # Valid intent categories for fuzzy match fallback
    VALID_INTENTS = {e.value for e in Intent}

    def __init__(self, claude_client, model: str = "claude-haiku-4-5-20251001"):
        """
        Args:
            claude_client: An instance of multi_agent_v2.real_claude.RealClaudeClient
                          (or any object with a generate() method matching
                          the same signature).
            model: The Anthropic model identifier. Defaults to Haiku 4.5.
                   Override in tests with a mock or cheaper model.
        """
        self._client = claude_client
        self._model = model

    async def classify(self, message_text: str) -> Classification:
        """Classify a single message into an intent category.

        This method is the hot path — it runs on every single incoming text
        message. It must be fast (target: <1 second) and cheap (target: <$0.001).

        Args:
            message_text: The raw text from the Telegram message. Voice messages
                         should be transcribed before being passed here.

        Returns:
            A frozen Classification dataclass with the intent, confidence,
            optional secondary intent, and metadata.
        """
        start_time = time.monotonic()

        try:
            # Call Haiku through the existing wrapper.
            # RealClaudeClient.generate() is synchronous — run it in a thread
            # to avoid blocking the async event loop.
            import asyncio
            response_text = await asyncio.to_thread(
                self._client.generate,
                CLASSIFIER_SYSTEM_PROMPT,
                message_text,
                model=self._model,
                max_tokens=150,          # JSON response is ~30-60 tokens
                temperature=0.0,         # Deterministic classification
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Parse the JSON response. Haiku with temperature=0 and the
            # "respond with ONLY the JSON object" instruction produces clean
            # JSON reliably, but we strip markdown fences defensively.
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            data = json.loads(cleaned)

            # Validate and construct the Classification
            raw_intent = data["intent"].strip().upper()

            # Fuzzy match: handle "NEW_BUILD — reason" or "NEW_BUILD: explanation"
            intent_value = raw_intent
            if raw_intent not in self.VALID_INTENTS:
                for valid in self.VALID_INTENTS:
                    if raw_intent.startswith(valid):
                        logger.warning(
                            "Classifier returned intent '%s', extracted '%s'",
                            raw_intent, valid,
                        )
                        intent_value = valid
                        break
                else:
                    logger.warning(
                        "Classifier returned unrecognized intent: '%s', "
                        "defaulting to CONVERSATIONAL",
                        raw_intent,
                    )
                    intent_value = "CONVERSATIONAL"

            intent = Intent(intent_value)
            confidence = float(data["confidence"])

            secondary_raw = data.get("secondary_intent")
            secondary = Intent(secondary_raw) if secondary_raw else None

            return Classification(
                intent=intent,
                confidence=confidence,
                secondary_intent=secondary,
                raw_message=message_text,
                latency_ms=elapsed_ms,
                needs_clarification=confidence < CLARIFICATION_THRESHOLD,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Classifier parse error: %s | Raw response: %s",
                str(e),
                response_text[:200] if "response_text" in dir() else "N/A",
            )
            # Graceful degradation: treat parse failures as CONVERSATIONAL
            # with zero confidence, which will trigger clarification.
            return Classification(
                intent=Intent.CONVERSATIONAL,
                confidence=0.0,
                secondary_intent=None,
                raw_message=message_text,
                latency_ms=elapsed_ms,
                needs_clarification=True,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error("Classifier call failed: %s", str(e), exc_info=True)
            return Classification(
                intent=Intent.CONVERSATIONAL,
                confidence=0.0,
                secondary_intent=None,
                raw_message=message_text,
                latency_ms=elapsed_ms,
                needs_clarification=True,
            )
