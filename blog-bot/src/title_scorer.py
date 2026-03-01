"""Title Pre-Scoring Module.

Before generating a full blog, generates multiple candidate titles and scores
them against patterns from top-performing content. Uses the primary LLM for
title generation and sentence-transformers for uniqueness checking against
existing blog titles.

Scoring criteria (each 0-2 points, max 10):
1. Emotional Trigger
2. Specificity
3. Format Match
4. Length
5. Uniqueness vs. Existing Blog Registry
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"
FORMAT_WEIGHTS_PATH = CONFIG_DIR / "title_format_weights.json"

# Q-learner state (read-only — for updating format weights)
PROJECT_ROOT = Path(__file__).parent.parent.parent
Q_STATE_PATH = PROJECT_ROOT / "data" / "q_state.json"

# Shared model instance — set by pipeline.py at startup
_embedding_model = None

# Default format weights (overridden by title_format_weights.json if it exists)
DEFAULT_FORMAT_WEIGHTS = {
    "why": 2.0,
    "how_to_qualified": 2.0,
    "psychology_of": 1.5,
    "numbered_list": 1.0,
    "statement": 0.5,
}

# Mapping from q_learner title_style names to our format weight keys
_BANDIT_STYLE_TO_FORMAT = {
    "question": "why",
    "how-to": "how_to_qualified",
    "provocative": "psychology_of",
    "statement": "statement",
}

# Minimum data points in q_learner before we trust a style's avg_reward
_MIN_STYLE_COUNT = 5


def _load_format_weights() -> dict:
    """Load format weights from JSON file, falling back to defaults."""
    if FORMAT_WEIGHTS_PATH.exists():
        try:
            data = json.loads(FORMAT_WEIGHTS_PATH.read_text())
            weights = {
                k: v for k, v in data.items()
                if k not in ("last_updated", "source") and isinstance(v, (int, float))
            }
            if weights:
                return weights
        except Exception as e:
            logger.debug("Could not load format weights: %s", e)
    return dict(DEFAULT_FORMAT_WEIGHTS)


def update_format_weights_from_bandit() -> dict:
    """Update format weights using title style performance from q_learner.

    Reads data/q_state.json → title_styles and adjusts weights based on
    average reward per style. Only updates styles with enough data points.

    Returns dict with update status.
    """
    if not Q_STATE_PATH.exists():
        return {"updated": False, "reason": "no_q_state"}

    try:
        state = json.loads(Q_STATE_PATH.read_text())
    except Exception as e:
        return {"updated": False, "reason": f"read_error: {e}"}

    title_styles = state.get("title_styles", {})
    if not title_styles:
        return {"updated": False, "reason": "no_title_style_data"}

    current_weights = _load_format_weights()
    updated_any = False

    for bandit_style, format_key in _BANDIT_STYLE_TO_FORMAT.items():
        style_data = title_styles.get(bandit_style, {})
        count = style_data.get("count", 0)
        avg_reward = style_data.get("avg_reward", 0.0)

        if count < _MIN_STYLE_COUNT:
            continue  # Not enough data to be statistically meaningful

        # Adjust weight: base_weight * (1 + avg_reward)
        # avg_reward ranges 0-1, so this gives a 0% to 100% boost
        base = DEFAULT_FORMAT_WEIGHTS.get(format_key, 1.0)
        new_weight = round(base * (1.0 + avg_reward), 2)
        # Clamp to reasonable range
        new_weight = max(0.5, min(4.0, new_weight))

        if current_weights.get(format_key) != new_weight:
            current_weights[format_key] = new_weight
            updated_any = True
            logger.info(
                "Title format weight updated: %s = %.2f (base=%.1f, avg_reward=%.3f, n=%d)",
                format_key, new_weight, base, avg_reward, count,
            )

    if updated_any:
        from datetime import datetime
        save_data = dict(current_weights)
        save_data["last_updated"] = datetime.utcnow().isoformat()
        save_data["source"] = "q_learner_title_styles"
        FORMAT_WEIGHTS_PATH.write_text(json.dumps(save_data, indent=2))
        logger.info("Title format weights saved to %s", FORMAT_WEIGHTS_PATH)
        return {"updated": True, "weights": current_weights}

    return {"updated": False, "reason": "no_significant_changes"}


# Active format weights (loaded from JSON)
FORMAT_WEIGHTS = _load_format_weights()

TITLE_GENERATION_SYSTEM = (
    "You generate blog title candidates for a friendship/social connection blog "
    "targeting adults aged 16-60 in Ontario, Canada. Titles must resonate across "
    "a wide age range — avoid framing that only speaks to one generation or "
    "assumes a specific reader age (e.g., 'your dad' excludes readers who ARE dads)."
)

TITLE_GENERATION_USER = (
    "Generate 10 blog title candidates for the following topic: {topic}.\n"
    "Requirements:\n"
    "- Each title should take a different angle or format.\n"
    "- Include a mix of: 'Why...' questions, 'How to...' guides, provocative "
    "statements, and personal narrative angles.\n"
    "- Titles should feel like VICE or The Cut headlines, not generic SEO bait.\n"
    "- Output only the numbered list of titles, nothing else."
)


def set_embedding_model(model) -> None:
    """Set the shared embedding model instance (called by pipeline at startup)."""
    global _embedding_model
    _embedding_model = model


def _get_embedding_model():
    """Get or lazy-load the embedding model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded sentence-transformers model: all-MiniLM-L6-v2")
    return _embedding_model


def _load_registry() -> dict:
    """Load the blog registry."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _get_existing_embeddings(registry: dict) -> list[tuple[str, np.ndarray]]:
    """Load existing title embeddings from the registry.

    Returns list of (title, embedding) tuples. Generates missing embeddings
    and saves them back to the registry.
    """
    model = _get_embedding_model()
    results = []
    updated = False

    for blog in registry.get("blogs", []):
        if blog.get("status") != "published":
            continue
        title = blog.get("title", "")
        if not title:
            continue

        embedding = blog.get("embedding")
        if embedding is not None:
            results.append((title, np.array(embedding)))
        else:
            # Generate missing embedding
            emb = model.encode(title)
            blog["embedding"] = emb.tolist()
            results.append((title, emb))
            updated = True

    if updated:
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
        logger.info("Generated and saved missing title embeddings.")

    return results


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _generate_candidates(topic: str, api_key: str, model: str = "claude-sonnet-4-20250514") -> list[str]:
    """Call the LLM to generate 8-10 title candidates.

    Uses the primary model (Sonnet) with high temperature for creative variety.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=300,
        temperature=0.9,
        system=TITLE_GENERATION_SYSTEM,
        messages=[{
            "role": "user",
            "content": TITLE_GENERATION_USER.format(topic=topic),
        }],
    )

    raw = message.content[0].text.strip()
    # Parse numbered list
    titles = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Remove numbering (e.g., "1.", "1)", "1:")
        cleaned = re.sub(r"^\d+[\.\)\:]\s*", "", line).strip()
        # Remove surrounding quotes if present
        if cleaned.startswith('"') and cleaned.endswith('"'):
            cleaned = cleaned[1:-1]
        if cleaned:
            titles.append(cleaned)

    return titles


def _score_emotional_trigger(title: str) -> float:
    """Score 0-2: Does the title have an emotional hook?

    0 = No emotional hook
    1 = Mild emotional relevance
    2 = Strong emotional tension
    """
    title_lower = title.lower()

    # Strong emotional triggers
    strong_patterns = [
        r"impossible", r"nobody talks about", r"what nobody tells you",
        r"the truth about", r"why .+ feels", r"the hidden cost",
        r"what happens when", r"i tried", r"confess", r"afraid",
        r"terrif", r"lonely", r"loneliness", r"heartbreak", r"grief",
        r"struggling", r"desperate", r"anxious", r"exhausting",
        r"painful", r"awkward", r"embarrass", r"vulnerable",
        r"nearly impossible", r"silently", r"secretly",
    ]
    for pattern in strong_patterns:
        if re.search(pattern, title_lower):
            return 2.0

    # Mild emotional relevance
    mild_patterns = [
        r"hard", r"difficult", r"challeng", r"friend", r"connect",
        r"social", r"alone", r"community", r"belong", r"relationship",
        r"self-", r"mental health", r"burnout", r"stress",
    ]
    for pattern in mild_patterns:
        if re.search(pattern, title_lower):
            return 1.0

    return 0.0


def _score_specificity(title: str) -> float:
    """Score 0-2: How specific is the title?

    0 = Vague/generic
    1 = Somewhat specific
    2 = Highly specific with qualifier
    """
    title_lower = title.lower()

    # Check for qualifiers that add specificity
    qualifiers = [
        r"\b(after \d+)\b", r"\b(in \d{4})\b", r"\b(in toronto)\b",
        r"\b(in ontario)\b", r"\b(in kitchener)\b", r"\b(in waterloo)\b",
        r"\b(as an adult)\b", r"\b(after college)\b", r"\b(after \w+)\b",
        r"\b(in your \d+s)\b", r"\b(at \d+)\b", r"\b(over \d+)\b",
        r"\b(under \d+)\b", r"\b(when you're)\b", r"\b(as a)\b",
    ]
    qualifier_count = sum(1 for q in qualifiers if re.search(q, title_lower))

    if qualifier_count >= 2:
        return 2.0
    elif qualifier_count == 1:
        return 1.5

    # Check for specific nouns/scenarios
    specific_terms = [
        r"introvert", r"extrovert", r"remote work", r"pandemic",
        r"dating app", r"coworker", r"roommate", r"neighbor",
        r"gym", r"bar", r"coffee shop", r"church", r"meetup",
    ]
    for term in specific_terms:
        if re.search(term, title_lower):
            return 1.0

    # Generic check
    generic_terms = [
        r"^friendship advice$", r"^making friends$", r"^social tips$",
        r"^how to be social$",
    ]
    for term in generic_terms:
        if re.search(term, title_lower):
            return 0.0

    return 0.5  # Default: somewhat specific


def _score_format_match(title: str) -> float:
    """Score 0-2: Does the title use a historically high-performing format?"""
    title_lower = title.lower()

    # "Why..." questions
    if re.match(r"^why\b", title_lower):
        return FORMAT_WEIGHTS["why"]

    # "How to..." with qualifier
    if re.match(r"^how to\b", title_lower):
        # Check for qualifier
        qualifiers = [
            r"after", r"in \d{4}", r"as an?", r"when", r"without",
            r"in toronto", r"in ontario", r"in your",
        ]
        for q in qualifiers:
            if re.search(q, title_lower):
                return FORMAT_WEIGHTS["how_to_qualified"]
        return FORMAT_WEIGHTS["how_to_qualified"] * 0.75  # Unqualified how-to

    # "The [Psychology/Science/Hidden Cost] of..."
    if re.match(r"^the (psychology|science|hidden cost|real cost|truth|secret)", title_lower):
        return FORMAT_WEIGHTS["psychology_of"]

    # Numbered lists
    if re.match(r"^\d+\s", title_lower):
        return FORMAT_WEIGHTS["numbered_list"]

    # Statement format (default)
    return FORMAT_WEIGHTS["statement"]


def _score_length(title: str) -> float:
    """Score 0-2: Is the title an optimal length?

    6-12 words: 2
    5 or 13-15 words: 1
    Under 5 or over 15: 0
    """
    word_count = len(title.split())
    if 6 <= word_count <= 12:
        return 2.0
    elif word_count == 5 or 13 <= word_count <= 15:
        return 1.0
    else:
        return 0.0


def _score_audience_alignment(title: str) -> float:
    """Score 0-2: Does the title work for the full target audience (16-60)?

    0 = Title alienates a major segment (assumes reader is young/old/specific age)
    1 = Slightly skewed but broadly acceptable
    2 = Inclusive — works across all age groups
    """
    title_lower = title.lower()

    # Patterns that assume reader is young (alienates 40-60 readers)
    young_reader_patterns = [
        r"\byour (dad|father|mom|mother|parent|parents)\b",
        r"\byour (grandpa|grandma|grandfather|grandmother)\b",
        r"\b(ok boomer|boomer)\b",
        r"\byour (old man|folks)\b",
    ]
    for pattern in young_reader_patterns:
        if re.search(pattern, title_lower):
            return 0.0

    # Patterns that assume reader is older (alienates teens/20s readers)
    old_reader_patterns = [
        r"\byour (kid|kids|children|teen|teenager|son|daughter)\b",
        r"\b(young people these days|kids these days)\b",
        r"\b(back in my day)\b",
    ]
    for pattern in old_reader_patterns:
        if re.search(pattern, title_lower):
            return 0.0

    # Patterns that skew toward one segment but aren't exclusionary
    mildly_skewed_patterns = [
        r"\b(gen z|zoomer|millennial|boomer)\b",
        r"\b(college|university|campus|dorm)\b",
        r"\b(retirement|retiring|pension|senior)\b",
        r"\b(midlife|mid-life)\b",
    ]
    for pattern in mildly_skewed_patterns:
        if re.search(pattern, title_lower):
            return 1.0

    return 2.0


def _score_uniqueness(title: str, existing: list[tuple[str, np.ndarray]]) -> tuple[float, Optional[str]]:
    """Score 0-2: How unique is this title vs existing blog titles?

    Returns (score, conflicting_title_or_None).

    High semantic distance from all existing: 2
    Moderate distance: 1
    Too close (similarity > 0.85): 0 — flagged for dedup
    """
    if not existing:
        return 2.0, None  # No existing blogs to compare against

    model = _get_embedding_model()
    title_embedding = model.encode(title)

    max_sim = 0.0
    most_similar_title = None

    for existing_title, existing_emb in existing:
        sim = _cosine_similarity(title_embedding, existing_emb)
        if sim > max_sim:
            max_sim = sim
            most_similar_title = existing_title

    if max_sim > 0.85:
        return 0.0, most_similar_title
    elif max_sim > 0.70:
        return 1.0, most_similar_title
    else:
        return 2.0, None


def score_title(title: str, existing_embeddings: list[tuple[str, np.ndarray]]) -> dict:
    """Score a single title across all criteria.

    Returns a dict with individual scores, total, and any flags.
    """
    emotional = _score_emotional_trigger(title)
    specificity = _score_specificity(title)
    format_match = _score_format_match(title)
    length = _score_length(title)
    uniqueness, conflict = _score_uniqueness(title, existing_embeddings)
    audience = _score_audience_alignment(title)

    total = emotional + specificity + format_match + length + uniqueness + audience

    result = {
        "title": title,
        "scores": {
            "emotional_trigger": emotional,
            "specificity": specificity,
            "format_match": format_match,
            "length": length,
            "uniqueness": uniqueness,
            "audience_alignment": audience,
        },
        "total": total,
        "flags": [],
    }

    if conflict and uniqueness == 0.0:
        result["flags"].append(f"BLOCKED: Too similar to '{conflict}' (similarity > 0.85)")
    elif conflict and uniqueness == 1.0:
        result["flags"].append(f"CAUTION: Overlaps with '{conflict}' (similarity 0.70-0.85)")

    if audience == 0.0:
        result["flags"].append("BLOCKED: Title alienates a major audience segment (age-exclusive framing)")

    return result


def generate_and_score(topic: str, api_key: Optional[str] = None) -> dict:
    """Main entry point: generate title candidates and return the best one.

    Returns a dict with the selected title, all candidates with scores,
    and any flags.
    """
    if api_key is None:
        api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("No Anthropic API key available for title generation.")

    # Load existing titles for uniqueness checking
    registry = _load_registry()
    existing_embeddings = _get_existing_embeddings(registry)

    # Generate candidates
    logger.info("Generating title candidates for topic: %s", topic)
    candidates = _generate_candidates(topic, api_key)

    if not candidates:
        raise RuntimeError("LLM returned no title candidates.")

    logger.info("Generated %d title candidates.", len(candidates))

    # Score each candidate
    scored = []
    for title in candidates:
        result = score_title(title, existing_embeddings)
        scored.append(result)
        logger.debug(
            "  %.1f — %s%s",
            result["total"],
            title,
            f" [{', '.join(result['flags'])}]" if result["flags"] else "",
        )

    # Sort by total score descending
    scored.sort(key=lambda x: x["total"], reverse=True)

    # Select the best non-blocked title (dedup blocks AND audience blocks)
    selected = None
    for candidate in scored:
        blocked = any("BLOCKED" in f for f in candidate["flags"])
        if not blocked:
            selected = candidate
            break

    if selected is None:
        # All titles are blocked — return the highest-scoring one with a warning
        selected = scored[0]
        logger.warning(
            "All title candidates are blocked by dedup — returning best anyway: %s",
            selected["title"],
        )

    logger.info(
        "Selected title: '%s' (score: %.1f)",
        selected["title"],
        selected["total"],
    )

    return {
        "selected": selected,
        "all_candidates": scored,
        "topic": topic,
        "candidates_generated": len(candidates),
    }
