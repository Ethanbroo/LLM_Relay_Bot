"""Visual Intent Layer — narrative-aligned image search.

Instead of searching for "what the blog is about" (topic keywords),
we search for "how the blog feels" (emotional/narrative coherence).

Flow: blog → tone_classifier → visual_intent → image_search

Tone categories: struggle, aspiration, conflict, relief, belonging,
reflection, warning.

Avoid: alcohol, nightlife, party, luxury, staged enthusiasm,
group celebrations (unless explicitly relevant).
"""

import os
import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Tone categories (narrative archetypes)
TONE_STRUGGLE = "struggle"
TONE_ASPIRATION = "aspiration"
TONE_CONFLICT = "conflict"
TONE_RELIEF = "relief"
TONE_BELONGING = "belonging"
TONE_REFLECTION = "reflection"
TONE_WARNING = "warning"

# Tone → emotional / narrative-aligned search terms
# CRITICAL: ALL searches must include "person" or "people" to avoid abstract objects
# These retrieve images that match the FEEL of the article, not just the topic
TONE_TO_VISUAL_INTENT: dict[str, list[str]] = {
    TONE_STRUGGLE: [
        "person sitting alone window",
        "solitary person quiet room",
        "person tired fatigue",
        "person overwhelmed stress",
        "lone person contemplative",
    ],
    TONE_ASPIRATION: [
        "person morning light hopeful",
        "person new beginning fresh",
        "person growth mindful",
        "person quiet determination",
        "person sunrise peaceful",
    ],
    TONE_CONFLICT: [
        "person internal struggle",
        "person laptop late night",
        "person work tension",
        "person emotional distance",
        "person burnout exhaustion",
    ],
    TONE_RELIEF: [
        "person peaceful calm",
        "person relief comfort",
        "people gentle support",
        "person rest recovery",
        "person calm light",
    ],
    TONE_BELONGING: [
        "people gathering informal",
        "people connection genuine",
        "small group people conversation",
        "people neighbors friendly",
        "people shared space cozy",
    ],
    TONE_REFLECTION: [
        "person contemplative alone",
        "person introspection quiet",
        "person thinking solitude",
        "person journal writing",
        "person peaceful alone",
    ],
    TONE_WARNING: [
        "person caution attention",
        "person overwhelmed signs",
        "person stress warning",
        "person phone disconnection",
        "person isolation modern",
    ],
}

# Risk cues: images containing these often contradict articles
# about loneliness, burnout, disconnection. Penalize in scoring.
NEGATIVE_CONTEXT_TERMS: frozenset[str] = frozenset({
    "alcohol", "wine", "beer", "cocktail", "drink", "drinks",
    "nightlife", "party", "celebration", "celebrating", "cheers",
    "rooftop", "bar", "club",
    "luxury", "expensive", "yacht", "designer",
    "corporate", "conference", "boardroom", "presentation",
    "happy hour", "networking event", "business lunch",
    "staged", "posed", "professional photo shoot",
    "romantic", "couple", "dating",
    "crowd", "large group", "festival", "concert",
    "wealth", "success", "trophy", "achievement",
})

# Fallback when tone can't be determined (MUST include person/people)
DEFAULT_VISUAL_INTENT = [
    "person contemplative alone",
    "people community connection",
    "people quiet connection",
    "people genuine human moment",
]


@dataclass
class VisualIntentResult:
    """Result of tone classification and visual intent derivation."""
    tone: str
    visual_intent_keywords: list[str]
    raw_response: Optional[str] = None


def classify_tone_and_intent(
    title: str,
    excerpt: str,
    content_preview: str = "",
) -> VisualIntentResult:
    """Classify article tone and derive narrative-aligned visual intent keywords.

    Uses Claude to classify into: struggle, aspiration, conflict, relief,
    belonging, reflection, warning. Maps to emotional search terms that
    retrieve images matching HOW the article feels, not just the topic.

    Args:
        title: Article title
        excerpt: Article excerpt/summary
        content_preview: First ~500 chars of content (optional, for more context)

    Returns:
        VisualIntentResult with tone and visual_intent_keywords
    """
    api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("No Anthropic API key — using topic-based fallback for visual intent")
        return _fallback_visual_intent(title, excerpt)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Classify this blog article's NARRATIVE TONE and suggest VISUAL INTENT keywords for image search.

Article:
Title: {title}
Excerpt: {excerpt}
{f"Content preview: {content_preview[:400]}..." if content_preview else ""}

Choose ONE primary tone from: struggle, aspiration, conflict, relief, belonging, reflection, warning

- struggle: loneliness, isolation, hardship, fatigue, overwhelm
- aspiration: hope, growth, new beginnings, improvement
- conflict: tension, burnout, work-life imbalance, internal struggle
- relief: peace, comfort, support, recovery, calm
- belonging: community, connection, informal gathering, neighbors
- reflection: contemplation, introspection, quiet solitude
- warning: caution, risk, signs of trouble

Then provide 5 search phrases for Unsplash that capture HOW the article FEELS (emotional posture).

CRITICAL IMAGE REQUIREMENTS:
1. EVERY phrase MUST include "person" or "people" (we need human subjects, NOT abstract objects)
2. Focus on emotional posture (contemplative, isolated, connected, overwhelmed, etc.)
3. Avoid: party, alcohol, corporate, networking, celebrations, luxury, romantic couples

Good examples:
- "person sitting alone window"
- "people quiet connection"
- "person tired overwhelmed"
- "lone person contemplative"
- "small group people conversation"

Return ONLY valid JSON:
{{"tone": "<one word>", "visual_intent": ["person/people phrase1", "person/people phrase2", "person/people phrase3", "person/people phrase4", "person/people phrase5"]}}"""

        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        tone = (data.get("tone") or "belonging").lower()
        intent = data.get("visual_intent") or []

        # Normalize tone to known category
        if tone not in TONE_TO_VISUAL_INTENT:
            tone = TONE_BELONGING

        # Prefer classifier's intent; fall back to our mapping if too few
        if len(intent) >= 3:
            keywords = [p.strip() for p in intent[:5] if p and isinstance(p, str)]
        else:
            keywords = TONE_TO_VISUAL_INTENT.get(tone, DEFAULT_VISUAL_INTENT)[:5]

        if not keywords:
            keywords = DEFAULT_VISUAL_INTENT[:5]

        logger.info("Visual intent: tone=%s keywords=%s", tone, keywords)
        return VisualIntentResult(
            tone=tone,
            visual_intent_keywords=keywords,
            raw_response=raw[:200],
        )
    except Exception as e:
        logger.warning("Tone classification failed: %s — using fallback", e)
        return _fallback_visual_intent(title, excerpt)


def _fallback_visual_intent(title: str, excerpt: str) -> VisualIntentResult:
    """Rule-based fallback when Claude is unavailable or fails."""
    text = f"{title} {excerpt}".lower()
    if any(w in text for w in ["lonely", "loneliness", "isolation", "alone", "disconnect"]):
        tone = TONE_STRUGGLE
    elif any(w in text for w in ["burnout", "stress", "overwhelm", "exhaust"]):
        tone = TONE_CONFLICT
    elif any(w in text for w in ["hope", "build", "improve", "grow"]):
        tone = TONE_ASPIRATION
    elif any(w in text for w in ["community", "neighbor", "belong", "together"]):
        tone = TONE_BELONGING
    elif any(w in text for w in ["reflect", "contemplate", "introspect"]):
        tone = TONE_REFLECTION
    elif any(w in text for w in ["relief", "peace", "calm", "support"]):
        tone = TONE_RELIEF
    else:
        tone = TONE_BELONGING

    keywords = TONE_TO_VISUAL_INTENT.get(tone, DEFAULT_VISUAL_INTENT)[:5]
    return VisualIntentResult(tone=tone, visual_intent_keywords=keywords)


def get_negative_context_terms() -> frozenset[str]:
    """Return terms that, when present in image metadata, signal potential mismatch."""
    return NEGATIVE_CONTEXT_TERMS
