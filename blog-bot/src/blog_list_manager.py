"""Blog List Manager.

Maintains the current list of published blogs available for cross-linking.
Reads from blog_registry.json and formats the blog lists for injection
into the per-blog template, grouped by category.

Only includes blogs with status "published" (excludes "consolidated" or "draft").
The list is regenerated from blog_registry.json each time a blog is generated
to ensure cross-link targets are always current.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"

# Known categories and their display order
CATEGORIES = ["Lifestyle", "Learning", "Uncategorized", "News / Press"]

# Category normalization map
CATEGORY_MAP = {
    "lifestyle": "Lifestyle",
    "learning": "Learning",
    "uncategorized": "Uncategorized",
    "news": "News / Press",
    "press": "News / Press",
    "news / press": "News / Press",
    "news/press": "News / Press",
}


def _load_registry() -> dict:
    """Load the blog registry from disk."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _save_registry(registry: dict) -> None:
    """Save the blog registry to disk."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _normalize_category(category: str) -> str:
    """Normalize a category string to its canonical form."""
    return CATEGORY_MAP.get(category.lower().strip(), "Uncategorized")


def get_published_blogs() -> list[dict]:
    """Return all published blogs from the registry."""
    registry = _load_registry()
    return [
        b for b in registry.get("blogs", [])
        if b.get("status") == "published"
    ]


def get_blogs_by_category() -> dict[str, list[dict]]:
    """Return published blogs grouped by category.

    Returns a dict mapping category name to list of blog dicts,
    sorted by ID within each category.
    """
    published = get_published_blogs()
    grouped: dict[str, list[dict]] = {cat: [] for cat in CATEGORIES}

    for blog in published:
        category = _normalize_category(blog.get("category", "Uncategorized"))
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(blog)

    # Sort each category by ID
    for cat in grouped:
        grouped[cat].sort(key=lambda b: b.get("id", 0))

    return grouped


def format_blog_list(category: str) -> str:
    """Format a numbered list of blog titles for a single category.

    Returns a string like:
    1. Why Is Making Friends In 2025 Nearly Impossible?
    2. How to Maintain Friendships When Life Gets Busy
    """
    grouped = get_blogs_by_category()
    normalized = _normalize_category(category)
    blogs = grouped.get(normalized, [])

    if not blogs:
        return "(No published blogs in this category)"

    lines = []
    for i, blog in enumerate(blogs, 1):
        title = blog.get("title", "(Untitled)")
        lines.append(f"{i}. {title}")

    return "\n".join(lines)


def format_all_lists() -> dict[str, str]:
    """Format blog lists for all categories.

    Returns a dict mapping category key (for template injection) to
    the formatted numbered list string.

    Keys match the per-blog template variables:
    - blog_list_lifestyle
    - blog_list_learning
    - blog_list_uncategorized
    - blog_list_news
    """
    grouped = get_blogs_by_category()

    key_map = {
        "Lifestyle": "blog_list_lifestyle",
        "Learning": "blog_list_learning",
        "Uncategorized": "blog_list_uncategorized",
        "News / Press": "blog_list_news",
    }

    result = {}
    for category, template_key in key_map.items():
        blogs = grouped.get(category, [])
        if not blogs:
            result[template_key] = "(No published blogs in this category)"
        else:
            lines = []
            for i, blog in enumerate(blogs, 1):
                title = blog.get("title", "(Untitled)")
                lines.append(f"{i}. {title}")
            result[template_key] = "\n".join(lines)

    return result


def add_blog(
    blog_id: int,
    title: str,
    category: str,
    url: str,
    publish_date: Optional[str] = None,
    content: Optional[str] = None,
    embedding: Optional[list[float]] = None,
    image: Optional[dict] = None,
) -> bool:
    """Add a new published blog to the registry.

    Called when a blog is approved and published.

    Args:
        blog_id: WordPress post ID.
        title: Blog title.
        category: Blog category (will be normalized).
        url: Relative URL path (e.g., "/blog/slug").
        publish_date: ISO format date string. Defaults to now.
        content: Full blog content (optional, used for FC mention detection).
        embedding: Pre-computed title embedding (optional).
        image: AI image generation metadata (optional, for feedback tracking).

    Returns True if added, False if already exists.
    """
    registry = _load_registry()
    existing_ids = {b.get("id") for b in registry.get("blogs", [])}

    if blog_id in existing_ids:
        logger.info("Blog #%d already in registry — skipping.", blog_id)
        return False

    entry = {
        "id": blog_id,
        "title": title,
        "category": _normalize_category(category),
        "url": url,
        "publish_date": publish_date or datetime.utcnow().strftime("%Y-%m-%d"),
        "embedding": embedding,
        "status": "published",
        "consolidated_into": None,
        "redirect_target": None,
        "content": content or "",
        "performance": {
            "views_30d": None,
            "ctr": None,
            "avg_time_on_page": None,
            "last_updated": None,
        },
    }

    if image:
        entry["image"] = image

    registry.setdefault("blogs", []).append(entry)
    _save_registry(registry)

    logger.info("Added blog #%d to registry: '%s' [%s]", blog_id, title, entry["category"])
    return True


def remove_blog(blog_id: int) -> bool:
    """Remove a blog from the registry entirely.

    Use this only for blogs that were deleted — for consolidation,
    use duplicate_consolidator.confirm_consolidation() instead.

    Returns True if removed, False if not found.
    """
    registry = _load_registry()
    original_count = len(registry.get("blogs", []))
    registry["blogs"] = [
        b for b in registry.get("blogs", [])
        if b.get("id") != blog_id
    ]

    if len(registry["blogs"]) < original_count:
        _save_registry(registry)
        logger.info("Removed blog #%d from registry.", blog_id)
        return True

    logger.warning("Blog #%d not found in registry.", blog_id)
    return False


def update_blog_status(blog_id: int, status: str) -> bool:
    """Update a blog's status (e.g., "published", "draft", "consolidated").

    Returns True if updated, False if not found.
    """
    registry = _load_registry()

    for blog in registry.get("blogs", []):
        if blog.get("id") == blog_id:
            old_status = blog.get("status")
            blog["status"] = status
            _save_registry(registry)
            logger.info(
                "Updated blog #%d status: '%s' → '%s'.",
                blog_id, old_status, status,
            )
            return True

    logger.warning("Blog #%d not found in registry.", blog_id)
    return False


def get_blog_count() -> dict[str, int]:
    """Return count of blogs by status."""
    registry = _load_registry()
    counts: dict[str, int] = {}
    for blog in registry.get("blogs", []):
        status = blog.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def search_blogs(query: str) -> list[dict]:
    """Simple title search across all blogs in the registry.

    Returns matching blogs (case-insensitive substring match).
    """
    registry = _load_registry()
    query_lower = query.lower()
    return [
        {
            "id": b.get("id"),
            "title": b.get("title", ""),
            "category": b.get("category", ""),
            "status": b.get("status", ""),
            "url": b.get("url", ""),
        }
        for b in registry.get("blogs", [])
        if query_lower in b.get("title", "").lower()
    ]
