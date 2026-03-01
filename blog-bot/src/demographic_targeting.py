"""Demographic-Adaptive Targeting Module.

Adjusts blog audience targeting based on real viewer demographics from
Google Analytics 4 (via Site Kit or direct GA4 API). Influences sub-context
selection so blogs are biased toward the most engaged demographics while
still exploring other audiences.

Strategy:
- Track which age/gender demographics actually read the blog
- Boost sub-contexts that match the dominant demographic
- Ensure variety: not every blog targets the dominant group
- Exploration rate decreases as demographic signal strengthens

Data flow:
1. analytics_sync pulls GA4 demographic data → demographic_profile.json
2. This module reads that profile at sub-context selection time
3. Returns demographic boost scores to sub_context_rotator
4. Sub-context rotator applies boosts alongside existing scoring
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
DEMOGRAPHIC_PROFILE_PATH = CONFIG_DIR / "demographic_profile.json"

# Minimum total sessions across all demographics before trusting the data
MIN_SESSIONS_FOR_SIGNAL = 50

# Maximum boost a dominant demographic can get (prevents tunnel vision)
MAX_DEMOGRAPHIC_BOOST = 2.0

# Minimum share of blogs that should target non-dominant demographics
# e.g., 0.30 means at least 30% of blogs explore outside the top demographic
EXPLORATION_FLOOR = 0.30

# Mapping from GA4 age groups to sub-context demographic IDs
# GA4 reports: 18-24, 25-34, 35-44, 45-54, 55-64, 65+
AGE_TO_SUBCONTEXT_AFFINITY = {
    "18-24": ["demo_late_20s", "cultural_gen_z"],
    "25-34": ["demo_late_20s", "demo_new_city", "demo_work_only", "cultural_remote_work"],
    "35-44": ["demo_work_only", "demo_new_city", "cultural_remote_work"],
    "45-54": ["cultural_loneliness_epidemic", "cultural_remote_work", "demo_work_only"],
    "55-64": ["cultural_loneliness_epidemic"],
    "65+": ["cultural_loneliness_epidemic"],
}


def _load_profile() -> dict:
    """Load the demographic profile from disk."""
    if DEMOGRAPHIC_PROFILE_PATH.exists():
        try:
            return json.loads(DEMOGRAPHIC_PROFILE_PATH.read_text())
        except Exception as e:
            logger.warning("Could not load demographic profile: %s", e)
    return {}


def _save_profile(profile: dict) -> None:
    """Save the demographic profile to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEMOGRAPHIC_PROFILE_PATH.write_text(json.dumps(profile, indent=2))


def update_demographic_profile(age_data: dict, gender_data: Optional[dict] = None) -> dict:
    """Update the stored demographic profile with new analytics data.

    Args:
        age_data: Dict mapping GA4 age groups to session counts.
                  Example: {"18-24": 120, "25-34": 450, "35-44": 200, ...}
        gender_data: Optional dict mapping gender to session counts.
                     Example: {"male": 400, "female": 350, "other": 50}

    Returns dict with update status.
    """
    profile = _load_profile()

    profile["age_groups"] = age_data
    if gender_data:
        profile["gender"] = gender_data

    total_sessions = sum(age_data.values())
    profile["total_sessions"] = total_sessions
    profile["last_updated"] = datetime.utcnow().isoformat()

    # Calculate percentages
    if total_sessions > 0:
        profile["age_percentages"] = {
            age: round(count / total_sessions, 4)
            for age, count in age_data.items()
        }
    else:
        profile["age_percentages"] = {}

    # Identify dominant demographic
    if age_data:
        dominant_age = max(age_data, key=age_data.get)
        profile["dominant_age_group"] = dominant_age
        profile["dominant_share"] = round(age_data[dominant_age] / max(total_sessions, 1), 4)
    else:
        profile["dominant_age_group"] = None
        profile["dominant_share"] = 0.0

    # Track history for trend detection (keep last 12 snapshots)
    history = profile.get("history", [])
    history.append({
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "age_percentages": profile.get("age_percentages", {}),
        "total_sessions": total_sessions,
        "dominant_age_group": profile.get("dominant_age_group"),
    })
    profile["history"] = history[-12:]

    _save_profile(profile)

    logger.info(
        "Demographic profile updated: %d sessions, dominant=%s (%.1f%%)",
        total_sessions,
        profile.get("dominant_age_group", "unknown"),
        profile.get("dominant_share", 0) * 100,
    )

    return {
        "updated": True,
        "total_sessions": total_sessions,
        "dominant_age_group": profile.get("dominant_age_group"),
        "dominant_share": profile.get("dominant_share"),
    }


def get_subcontext_demographic_boosts(recent_demographic_ids: Optional[list[str]] = None) -> dict:
    """Calculate boost scores for sub-contexts based on demographic data.

    Returns a dict mapping sub_context_id to a float boost value.
    Positive = boost (matches dominant demographic).
    Zero = no data or exploration slot.

    The system ensures variety by applying an exploration mechanism:
    - If the last N blogs all targeted the dominant demographic, the boost
      is suppressed to force exploration of other audiences.

    Args:
        recent_demographic_ids: List of sub-context IDs from recent blogs
                                (used to detect over-targeting).

    Returns dict: {sub_context_id: boost_score}
    """
    profile = _load_profile()
    boosts = {}

    # Not enough data to make decisions
    total_sessions = profile.get("total_sessions", 0)
    if total_sessions < MIN_SESSIONS_FOR_SIGNAL:
        logger.debug(
            "Insufficient demographic data (%d sessions, need %d) — no boosts applied.",
            total_sessions, MIN_SESSIONS_FOR_SIGNAL,
        )
        return boosts

    age_percentages = profile.get("age_percentages", {})
    if not age_percentages:
        return boosts

    # Check if we need to force exploration (prevent tunnel vision)
    force_exploration = False
    recent = recent_demographic_ids or []
    if len(recent) >= 3:
        # If last 3 blogs all used demographic sub-contexts aligned with dominant group
        dominant_age = profile.get("dominant_age_group")
        dominant_contexts = set(AGE_TO_SUBCONTEXT_AFFINITY.get(dominant_age, []))
        recent_in_dominant = sum(1 for r in recent[-3:] if r in dominant_contexts)
        if recent_in_dominant >= 3:
            force_exploration = True
            logger.info(
                "Forcing demographic exploration — last 3 blogs targeted dominant group (%s).",
                dominant_age,
            )

    if force_exploration:
        # Invert: boost non-dominant demographics, suppress dominant
        dominant_age = profile.get("dominant_age_group")
        dominant_contexts = set(AGE_TO_SUBCONTEXT_AFFINITY.get(dominant_age, []))
        for age_group, share in age_percentages.items():
            for ctx_id in AGE_TO_SUBCONTEXT_AFFINITY.get(age_group, []):
                if ctx_id in dominant_contexts:
                    boosts[ctx_id] = boosts.get(ctx_id, 0) - 1.0
                else:
                    boosts[ctx_id] = boosts.get(ctx_id, 0) + 1.5
        return boosts

    # Normal mode: boost proportionally to demographic share
    # Higher share = higher boost, but capped at MAX_DEMOGRAPHIC_BOOST
    for age_group, share in age_percentages.items():
        # Scale: 50%+ share = max boost, <10% share = minimal boost
        if share <= 0.05:
            boost = 0.0
        elif share >= 0.50:
            boost = MAX_DEMOGRAPHIC_BOOST
        else:
            # Linear interpolation between 0.05 and 0.50
            boost = ((share - 0.05) / 0.45) * MAX_DEMOGRAPHIC_BOOST

        for ctx_id in AGE_TO_SUBCONTEXT_AFFINITY.get(age_group, []):
            # Use max if multiple age groups map to same context
            boosts[ctx_id] = max(boosts.get(ctx_id, 0), round(boost, 2))

    return boosts


def get_demographic_summary() -> dict:
    """Return a human-readable summary of current demographic targeting state.

    Used for logging and debugging.
    """
    profile = _load_profile()

    if not profile:
        return {"status": "no_data", "message": "No demographic profile exists yet."}

    total = profile.get("total_sessions", 0)
    if total < MIN_SESSIONS_FOR_SIGNAL:
        return {
            "status": "insufficient_data",
            "message": f"Only {total} sessions (need {MIN_SESSIONS_FOR_SIGNAL}). No targeting active.",
            "total_sessions": total,
        }

    return {
        "status": "active",
        "total_sessions": total,
        "dominant_age_group": profile.get("dominant_age_group"),
        "dominant_share": profile.get("dominant_share"),
        "age_breakdown": profile.get("age_percentages", {}),
        "last_updated": profile.get("last_updated"),
        "history_snapshots": len(profile.get("history", [])),
    }
