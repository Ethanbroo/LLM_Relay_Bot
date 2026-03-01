"""Few-Shot Example Auto-Refresher.

Automatically selects the best-performing blog excerpts as style reference
examples for the LLM, using Google Site Kit analytics data. Refreshes every
60 days so that as newer blogs outperform older ones, the LLM's style
reference evolves with the audience's actual preferences.

Dependency: Google Site Kit API access (already available in the bot's infrastructure).
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"
EXAMPLES_PATH = EXAMPLES_DIR / "few_shot_examples.md"

REFRESH_INTERVAL_DAYS = 60
MIN_BLOG_AGE_DAYS = 30
CTR_WEIGHT = 0.6
TIME_ON_PAGE_WEIGHT = 0.4
SCORE_CHURN_THRESHOLD = 0.05  # 5% — don't swap if scores are this close


def _load_registry() -> dict:
    """Load the blog registry from disk."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _save_registry(registry: dict) -> None:
    """Save the blog registry to disk."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _is_refresh_due(registry: dict) -> bool:
    """Check if a refresh is due based on the last refresh date."""
    history = registry.get("few_shot_history", [])
    if not history:
        return True  # Never refreshed — initial run
    last_refresh_str = history[-1].get("refreshed_at")
    if not last_refresh_str:
        return True
    last_refresh = datetime.fromisoformat(last_refresh_str)
    return (datetime.utcnow() - last_refresh).days >= REFRESH_INTERVAL_DAYS


def _get_eligible_blogs(registry: dict) -> list[dict]:
    """Filter blogs to those eligible for few-shot selection.

    Eligible means: published, with performance data, and at least
    MIN_BLOG_AGE_DAYS old.
    """
    cutoff = datetime.utcnow() - timedelta(days=MIN_BLOG_AGE_DAYS)
    eligible = []
    for blog in registry.get("blogs", []):
        if blog.get("status") != "published":
            continue
        perf = blog.get("performance", {})
        if not perf or perf.get("ctr") is None or perf.get("avg_time_on_page") is None:
            continue
        pub_date_str = blog.get("publish_date")
        if not pub_date_str:
            continue
        try:
            pub_date = datetime.fromisoformat(pub_date_str)
        except ValueError:
            # Try date-only format
            try:
                pub_date = datetime.strptime(pub_date_str, "%Y-%m-%d")
            except ValueError:
                continue
        if pub_date > cutoff:
            continue
        eligible.append(blog)
    return eligible


def _compute_composite_scores(blogs: list[dict]) -> list[tuple[dict, float]]:
    """Compute composite score for each blog using min-max normalized CTR and time on page.

    Returns list of (blog, score) tuples sorted by score descending.
    """
    if not blogs:
        return []

    ctrs = [b["performance"]["ctr"] for b in blogs]
    times = [b["performance"]["avg_time_on_page"] for b in blogs]

    ctr_min, ctr_max = min(ctrs), max(ctrs)
    time_min, time_max = min(times), max(times)

    ctr_range = ctr_max - ctr_min if ctr_max != ctr_min else 1.0
    time_range = time_max - time_min if time_max != time_min else 1.0

    scored = []
    for blog in blogs:
        norm_ctr = (blog["performance"]["ctr"] - ctr_min) / ctr_range
        norm_time = (blog["performance"]["avg_time_on_page"] - time_min) / time_range
        score = (norm_ctr * CTR_WEIGHT) + (norm_time * TIME_ON_PAGE_WEIGHT)
        scored.append((blog, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _blog_mentions_fc(blog: dict) -> bool:
    """Check if a blog's content mentions Friendly Connections."""
    content = blog.get("content", "")
    return "friendly connections" in content.lower()


def _extract_excerpt(blog: dict) -> str:
    """Extract opening, middle, and closing sections from a blog for few-shot use.

    Target: ~400-500 words per example.
    """
    content = blog.get("content", "")
    if not content:
        return "(No content available)"

    # Split content into sections by H3 headers
    sections = re.split(r'(?=###\s)', content)
    sections = [s.strip() for s in sections if s.strip()]

    if not sections:
        # No H3 headers — fall back to paragraph splitting
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        opening = "\n\n".join(paragraphs[:3])
        closing = paragraphs[-1] if paragraphs else ""
        middle = paragraphs[len(paragraphs) // 2] if len(paragraphs) > 3 else ""
        excerpt = f"{opening}\n\n{middle}\n\n{closing}"
        return _trim_excerpt(excerpt)

    # Opening: first 2-3 paragraphs (first section or intro before first H3)
    opening_section = sections[0] if sections else ""
    opening_paragraphs = [p.strip() for p in opening_section.split("\n\n") if p.strip()]
    opening = "\n\n".join(opening_paragraphs[:3])

    # Middle: section with highest paragraph count between 3rd and 7th H3
    middle = ""
    if len(sections) > 2:
        candidate_sections = sections[2:min(7, len(sections))]
        if candidate_sections:
            best_middle = max(
                candidate_sections,
                key=lambda s: len([p for p in s.split("\n\n") if p.strip()])
            )
            middle = best_middle

    # Closing: final paragraph
    last_section = sections[-1] if sections else ""
    closing_paragraphs = [p.strip() for p in last_section.split("\n\n") if p.strip()]
    closing = closing_paragraphs[-1] if closing_paragraphs else ""

    excerpt = f"{opening}\n\n{middle}\n\n{closing}"
    return _trim_excerpt(excerpt)


def _extract_fc_mention(blog: dict) -> str:
    """Extract the sentences containing a Friendly Connections mention."""
    content = blog.get("content", "")
    if not content:
        return "(No FC mention found)"

    sentences = re.split(r'(?<=[.!?])\s+', content)
    fc_sentences = [s for s in sentences if "friendly connections" in s.lower()]

    if not fc_sentences:
        return "(No FC mention found)"

    return " ".join(fc_sentences[:3])


def _trim_excerpt(text: str, max_words: int = 600) -> str:
    """Trim excerpt to max word count, preferring to cut from the middle."""
    words = text.split()
    if len(words) <= max_words:
        return text

    # Find the middle section and trim it
    parts = text.split("\n\n")
    if len(parts) >= 3:
        # Trim middle sections to their first 2 paragraphs worth
        middle_parts = parts[1:-1]
        trimmed_middle = []
        word_budget = max_words - len(parts[0].split()) - len(parts[-1].split())
        for part in middle_parts:
            part_paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
            trimmed = "\n\n".join(part_paragraphs[:2])
            if len(trimmed.split()) <= word_budget:
                trimmed_middle.append(trimmed)
                word_budget -= len(trimmed.split())
        return "\n\n".join([parts[0]] + trimmed_middle + [parts[-1]])

    # Simple truncation fallback
    return " ".join(words[:max_words])


def _scores_within_threshold(old_score: float, new_score: float) -> bool:
    """Check if two scores are within the churn threshold (no significant difference)."""
    if old_score == 0:
        return new_score == 0
    return abs(new_score - old_score) / max(old_score, 0.001) < SCORE_CHURN_THRESHOLD


def refresh_few_shot_examples(force: bool = False) -> dict:
    """Main entry point: refresh few-shot examples if due (or forced).

    Returns a dict with refresh results including which examples were
    selected and any changes from previous selection.
    """
    registry = _load_registry()

    if not force and not _is_refresh_due(registry):
        logger.info("Few-shot refresh not due yet — skipping.")
        return {"refreshed": False, "reason": "not_due"}

    eligible = _get_eligible_blogs(registry)

    if not eligible:
        logger.warning("No eligible blogs for few-shot selection — skipping refresh.")
        return {"refreshed": False, "reason": "no_eligible_blogs"}

    if len(eligible) < 3:
        logger.warning(
            "Only %d eligible blogs (fewer than 3) — using all available.", len(eligible)
        )

    scored = _compute_composite_scores(eligible)

    # Check for score differentiation
    if len(scored) >= 2:
        top_score = scored[0][1]
        bottom_score = scored[-1][1]
        if top_score > 0 and (top_score - bottom_score) / top_score < SCORE_CHURN_THRESHOLD:
            # Check if we already have examples — if so, keep them
            if EXAMPLES_PATH.exists():
                logger.info(
                    "No significant score differentiation — keeping current examples."
                )
                return {"refreshed": False, "reason": "no_score_differentiation"}

    # Select top 2 by composite score
    example_1 = scored[0] if len(scored) >= 1 else None
    example_2 = scored[1] if len(scored) >= 2 else None

    # Select Example 3: best FC-mention blog
    selected_ids = set()
    if example_1:
        selected_ids.add(example_1[0].get("id"))
    if example_2:
        selected_ids.add(example_2[0].get("id"))

    example_3 = None
    fc_blogs = [(b, s) for b, s in scored if _blog_mentions_fc(b) and b.get("id") not in selected_ids]
    if fc_blogs:
        example_3 = fc_blogs[0]
    else:
        # All FC-mention blogs already selected, or none exist
        fc_all = [(b, s) for b, s in scored if _blog_mentions_fc(b)]
        if fc_all:
            # Pick next best FC blog even if already selected
            for b, s in fc_all:
                if b.get("id") not in selected_ids:
                    example_3 = (b, s)
                    break
        if example_3 is None:
            logger.warning("No blogs mention Friendly Connections — skipping Example 3.")

    # Detect changes from previous selection
    history = registry.get("few_shot_history", [])
    previous = history[-1] if history else None
    changes = []

    if previous:
        prev_examples = previous.get("examples", [])
        new_examples = []
        if example_1:
            new_examples.append(example_1[0].get("title", ""))
        if example_2:
            new_examples.append(example_2[0].get("title", ""))

        for i, new_title in enumerate(new_examples):
            if i < len(prev_examples):
                old_title = prev_examples[i].get("title", "")
                if old_title != new_title:
                    old_score = prev_examples[i].get("composite_score", 0)
                    new_score = scored[i][1] if i < len(scored) else 0
                    change_msg = (
                        f"STYLE REFERENCE UPDATED: '{old_title}' replaced by "
                        f"'{new_title}' — new blog outperformed on composite score "
                        f"({old_score:.3f} → {new_score:.3f})."
                    )
                    changes.append(change_msg)
                    logger.info(change_msg)

    # Build the examples markdown
    now = datetime.utcnow()
    next_refresh = now + timedelta(days=REFRESH_INTERVAL_DAYS)
    lines = [
        "# Reference Style Examples",
        f"# Auto-generated on {now.strftime('%Y-%m-%d')} by few_shot_refresher.py",
        f"# Next refresh: {next_refresh.strftime('%Y-%m-%d')}",
        "# Selection basis: CTR (60%) + Time on Page (40%) from Google Site Kit",
        "",
    ]

    history_entry = {
        "refreshed_at": now.isoformat(),
        "next_refresh_at": next_refresh.isoformat(),
        "examples": [],
        "changes": changes,
    }

    if example_1:
        blog_1, score_1 = example_1
        perf_1 = blog_1.get("performance", {})
        lines.extend([
            f'## EXAMPLE 1: "{blog_1.get("title", "Unknown")}"',
            f'## Composite Score: {score_1:.3f} | CTR: {perf_1.get("ctr", 0):.4f} | Avg Time: {perf_1.get("avg_time_on_page", 0):.0f}s',
            "",
            _extract_excerpt(blog_1),
            "",
        ])
        history_entry["examples"].append({
            "title": blog_1.get("title", ""),
            "id": blog_1.get("id"),
            "composite_score": score_1,
            "role": "top_performer_1",
        })

    if example_2:
        blog_2, score_2 = example_2
        perf_2 = blog_2.get("performance", {})
        lines.extend([
            f'## EXAMPLE 2: "{blog_2.get("title", "Unknown")}"',
            f'## Composite Score: {score_2:.3f} | CTR: {perf_2.get("ctr", 0):.4f} | Avg Time: {perf_2.get("avg_time_on_page", 0):.0f}s',
            "",
            _extract_excerpt(blog_2),
            "",
        ])
        history_entry["examples"].append({
            "title": blog_2.get("title", ""),
            "id": blog_2.get("id"),
            "composite_score": score_2,
            "role": "top_performer_2",
        })

    if example_3:
        blog_3, score_3 = example_3
        lines.extend([
            f'## EXAMPLE 3 (FC Integration Reference): "{blog_3.get("title", "Unknown")}"',
            f'## Selected for: Best-performing blog containing a Friendly Connections mention',
            "",
            _extract_fc_mention(blog_3),
            "",
        ])
        history_entry["examples"].append({
            "title": blog_3.get("title", ""),
            "id": blog_3.get("id"),
            "composite_score": score_3,
            "role": "fc_integration_reference",
        })
    else:
        lines.extend([
            "## EXAMPLE 3 (FC Integration Reference): SKIPPED",
            "## No published blogs currently contain a Friendly Connections mention.",
            "",
        ])

    # Write examples file
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    EXAMPLES_PATH.write_text("\n".join(lines))
    logger.info("Wrote few-shot examples to %s", EXAMPLES_PATH)

    # Update registry history
    few_shot_history = registry.setdefault("few_shot_history", [])
    few_shot_history.append(history_entry)
    _save_registry(registry)

    return {
        "refreshed": True,
        "examples_selected": len(history_entry["examples"]),
        "changes": changes,
        "next_refresh": next_refresh.isoformat(),
    }


def get_few_shot_examples() -> str:
    """Load the current few-shot examples text for injection into the per-blog template.

    If no examples file exists, triggers an initial refresh.
    """
    if not EXAMPLES_PATH.exists():
        logger.info("No few-shot examples found — triggering initial refresh.")
        refresh_few_shot_examples(force=True)

    if EXAMPLES_PATH.exists():
        return EXAMPLES_PATH.read_text()
    return "(No few-shot examples available yet — generate blogs and sync analytics first.)"
