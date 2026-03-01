"""Sub-Context Rotation Module.

Injects a unique angle into each blog to prevent repetitive content and
improve topical freshness. Each blog generation request includes a sub-context
that gives the article a unique angle, preventing the "same blog, different
title" problem when topics overlap.

Sub-context categories:
- Seasonal Hooks
- Local / Regional Hooks
- Cultural Moment Hooks
- Demographic Hooks
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
SUB_CONTEXTS_PATH = CONFIG_DIR / "sub_contexts.json"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"

# Default sub-contexts seeded on first run
DEFAULT_CONTEXTS = [
    {
        "id": "seasonal_jan",
        "category": "seasonal",
        "text": "It's January — New Year's resolutions about being more social are already crumbling.",
        "applicable_months": [1, 2],
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "seasonal_summer",
        "category": "seasonal",
        "text": "Summer in Ontario means patios, festivals, and the unique loneliness of watching everyone else seem to have plans.",
        "applicable_months": [6, 7, 8],
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "seasonal_holidays",
        "category": "seasonal",
        "text": "The holidays amplify loneliness — this is peak season for people re-evaluating their social lives.",
        "applicable_months": [11, 12],
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "seasonal_spring",
        "category": "seasonal",
        "text": "Spring in Ontario brings that restless energy where everyone suddenly wants to 'get out more,' but nobody knows where to start.",
        "applicable_months": [3, 4, 5],
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "seasonal_fall",
        "category": "seasonal",
        "text": "Fall is when the social calendar resets — summer plans evaporate, and the long indoor months start feeling inevitable.",
        "applicable_months": [9, 10],
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "local_kw_tech",
        "category": "local",
        "text": "Kitchener-Waterloo's tech scene has created a wave of young transplants who don't know anyone.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "local_toronto_density",
        "category": "local",
        "text": "Toronto's density creates an illusion of social abundance that makes actual connection harder.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "local_ontario_winters",
        "category": "local",
        "text": "Ontario's long winters create seasonal isolation patterns that compound existing loneliness.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "cultural_remote_work",
        "category": "cultural",
        "text": "Remote work is now the default for many, and water-cooler friendships have disappeared with it.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "cultural_loneliness_epidemic",
        "category": "cultural",
        "text": "The Surgeon General called loneliness an epidemic — so why does nobody talk about it casually?",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "cultural_gen_z",
        "category": "cultural",
        "text": "Gen Z is the first generation to grow up with friendship mediated by algorithms.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "demo_late_20s",
        "category": "demographic",
        "text": "Angle this toward someone in their late 20s who just realized their college friend group has quietly dissolved.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "demo_new_city",
        "category": "demographic",
        "text": "Write for someone who moved cities for a job and has been 'meaning to put themselves out there' for six months.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
    {
        "id": "demo_work_only",
        "category": "demographic",
        "text": "Target someone who's socially functional at work but has zero close friends outside of it.",
        "applicable_months": None,
        "last_used": None,
        "use_count": 0,
    },
]


def _load_sub_contexts() -> dict:
    """Load sub-contexts from disk. Seed with defaults if file doesn't exist."""
    if SUB_CONTEXTS_PATH.exists():
        return json.loads(SUB_CONTEXTS_PATH.read_text())

    # First run — seed with defaults
    data = {"contexts": DEFAULT_CONTEXTS}
    _save_sub_contexts(data)
    logger.info("Seeded %d default sub-contexts.", len(DEFAULT_CONTEXTS))
    return data


def _save_sub_contexts(data: dict) -> None:
    """Save sub-contexts to disk."""
    SUB_CONTEXTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUB_CONTEXTS_PATH.write_text(json.dumps(data, indent=2))


def _load_registry() -> dict:
    """Load the blog registry to check recent sub-context usage."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": []}


def _get_last_used_category(registry: dict) -> Optional[str]:
    """Get the sub-context category used by the most recently generated blog."""
    blogs = registry.get("blogs", [])
    if not blogs:
        return None

    # Sort by publish date descending to find most recent
    dated_blogs = [
        b for b in blogs
        if b.get("publish_date") and b.get("sub_context_id")
    ]
    if not dated_blogs:
        return None

    dated_blogs.sort(key=lambda b: b.get("publish_date", ""), reverse=True)
    last_context_id = dated_blogs[0].get("sub_context_id")

    # Find the category for this context ID
    data = _load_sub_contexts()
    for ctx in data.get("contexts", []):
        if ctx["id"] == last_context_id:
            return ctx["category"]

    return None


def _is_seasonally_applicable(context: dict, month: int) -> bool:
    """Check if a context is applicable for the given month.

    Contexts without applicable_months are available year-round.
    """
    applicable = context.get("applicable_months")
    if applicable is None:
        return True
    return month in applicable


def _topic_category_affinity(topic: str, category: str) -> float:
    """Simple heuristic for how well a sub-context category matches a topic.

    Returns a bonus score (0.0 to 1.0) for topic-category relevance.
    """
    topic_lower = topic.lower()

    if category == "local":
        local_keywords = [
            "toronto", "ontario", "kitchener", "waterloo", "gta",
            "hamilton", "ottawa", "city", "urban", "downtown",
            "neighborhood", "neighbourhood",
        ]
        if any(kw in topic_lower for kw in local_keywords):
            return 1.0

    if category == "seasonal":
        seasonal_keywords = [
            "winter", "summer", "spring", "fall", "autumn",
            "holiday", "christmas", "new year", "valentine",
            "season", "weather",
        ]
        if any(kw in topic_lower for kw in seasonal_keywords):
            return 1.0

    if category == "cultural":
        cultural_keywords = [
            "remote", "work from home", "app", "online", "digital",
            "social media", "algorithm", "generation", "gen z",
            "millennial", "pandemic", "epidemic", "trend",
        ]
        if any(kw in topic_lower for kw in cultural_keywords):
            return 1.0

    if category == "demographic":
        demo_keywords = [
            "adult", "college", "university", "career", "job",
            "moved", "relocat", "introvert", "extrovert", "age",
            "20s", "30s", "40s", "young", "mid-life",
        ]
        if any(kw in topic_lower for kw in demo_keywords):
            return 1.0

    return 0.0


def select(topic: str, recent_history: Optional[list[str]] = None, demographic_boosts: Optional[dict] = None) -> dict:
    """Select an appropriate sub-context for a blog topic.

    Args:
        topic: The blog topic or title.
        recent_history: Optional list of recently used sub-context IDs
                        (from blog_registry). Used to avoid immediate repeats.
        demographic_boosts: Optional dict mapping sub_context_id to float boost
                           scores from demographic targeting. Positive values
                           boost contexts matching the dominant viewer demographic.

    Returns a dict with:
        id: The selected sub-context ID
        category: The sub-context category
        text: The sub-context text to inject into the template
    """
    data = _load_sub_contexts()
    contexts = data.get("contexts", [])

    if not contexts:
        logger.warning("No sub-contexts available.")
        return {
            "id": "none",
            "category": "none",
            "text": "(No sub-context available.)",
        }

    now = datetime.utcnow()
    current_month = now.month

    # Get last used category to avoid consecutive same-category
    registry = _load_registry()
    last_category = _get_last_used_category(registry)

    # Also use recent_history if provided
    recent_ids = set(recent_history or [])

    # Demographic boosts (from GA4 viewer data)
    demo_boosts = demographic_boosts or {}

    # Score each candidate
    candidates = []
    for ctx in contexts:
        # Filter: must be seasonally applicable
        if not _is_seasonally_applicable(ctx, current_month):
            continue

        score = 0.0

        # Penalize if same category as last blog
        if last_category and ctx["category"] == last_category:
            score -= 2.0

        # Penalize recently used
        if ctx["id"] in recent_ids:
            score -= 3.0

        # Prefer less-used contexts
        use_count = ctx.get("use_count", 0)
        score -= use_count * 0.5

        # Bonus for topic-category affinity
        affinity = _topic_category_affinity(topic, ctx["category"])
        score += affinity * 2.0

        # Bonus for freshness (never used or used long ago)
        last_used = ctx.get("last_used")
        if last_used is None:
            score += 1.0
        else:
            try:
                last_dt = datetime.fromisoformat(last_used)
                days_since = (now - last_dt).days
                if days_since > 30:
                    score += 0.5
            except ValueError:
                score += 0.5

        # Performance bonus: reward sub-contexts that produced engaging content
        avg_reward = ctx.get("avg_reward")
        if avg_reward is not None and avg_reward > 0:
            score += avg_reward * 1.5

        # Demographic targeting bonus: boost contexts matching viewer demographics
        demo_boost = demo_boosts.get(ctx["id"], 0.0)
        if demo_boost != 0.0:
            score += demo_boost
            logger.debug(
                "Demographic boost for %s: %+.2f", ctx["id"], demo_boost,
            )

        candidates.append((ctx, score))

    if not candidates:
        # All contexts are seasonally inapplicable — fall back to year-round ones
        logger.warning(
            "No seasonally applicable contexts for month %d — using any available.",
            current_month,
        )
        candidates = [(ctx, 0.0) for ctx in contexts]

    # Sort by score descending
    candidates.sort(key=lambda x: x[1], reverse=True)

    selected_ctx, selected_score = candidates[0]

    # Update usage tracking
    selected_ctx["last_used"] = now.isoformat()
    selected_ctx["use_count"] = selected_ctx.get("use_count", 0) + 1
    _save_sub_contexts(data)

    logger.info(
        "Selected sub-context: [%s] %s (score: %.1f)",
        selected_ctx["category"],
        selected_ctx["id"],
        selected_score,
    )

    return {
        "id": selected_ctx["id"],
        "category": selected_ctx["category"],
        "text": selected_ctx["text"],
    }


def add_context(
    context_id: str,
    category: str,
    text: str,
    applicable_months: Optional[list[int]] = None,
) -> None:
    """Add a new sub-context to the pool.

    Args:
        context_id: Unique identifier (e.g., "cultural_ai_friends")
        category: One of: seasonal, local, cultural, demographic
        text: The sub-context text to inject into blog prompts
        applicable_months: List of months (1-12) when applicable, or None for year-round
    """
    data = _load_sub_contexts()

    # Check for duplicate ID
    existing_ids = {ctx["id"] for ctx in data.get("contexts", [])}
    if context_id in existing_ids:
        logger.warning("Sub-context '%s' already exists — skipping.", context_id)
        return

    new_ctx = {
        "id": context_id,
        "category": category,
        "text": text,
        "applicable_months": applicable_months,
        "last_used": None,
        "use_count": 0,
    }
    data.setdefault("contexts", []).append(new_ctx)
    _save_sub_contexts(data)
    logger.info("Added sub-context: %s (%s)", context_id, category)


def list_contexts() -> list[dict]:
    """Return all sub-contexts with their usage stats."""
    data = _load_sub_contexts()
    return data.get("contexts", [])


def update_context_rewards() -> dict:
    """Update avg_reward for each sub-context using blog registry performance data.

    Looks at which sub-context each blog used (stored in registry),
    computes average engagement score, and writes it back to the context.

    Returns dict with update status.
    """
    data = _load_sub_contexts()
    contexts = data.get("contexts", [])
    registry = _load_registry()
    blogs = registry.get("blogs", [])

    # Build mapping: sub_context_id → list of performance scores
    context_scores: dict[str, list[float]] = {}
    for blog in blogs:
        ctx_id = blog.get("sub_context_id")
        if not ctx_id:
            continue
        perf = blog.get("performance", {})
        ctr = perf.get("ctr")
        time_on_page = perf.get("avg_time_on_page")
        if ctr is None or time_on_page is None:
            continue
        # Composite score (matching few_shot_refresher's formula)
        # Normalize time_on_page: assume 300s = perfect score of 1.0
        norm_time = min(time_on_page / 300.0, 1.0)
        score = ctr * 0.6 + norm_time * 0.4
        context_scores.setdefault(ctx_id, []).append(score)

    updated_count = 0
    for ctx in contexts:
        scores = context_scores.get(ctx["id"])
        if scores:
            avg = round(sum(scores) / len(scores), 4)
            ctx["avg_reward"] = avg
            updated_count += 1
        elif "avg_reward" not in ctx:
            ctx["avg_reward"] = None

    if updated_count > 0:
        _save_sub_contexts(data)
        logger.info("Sub-context rewards updated: %d context(s) with data.", updated_count)

    return {"updated": updated_count, "total_contexts": len(contexts)}
