"""Topic Deduplication Module.

Prevents keyword cannibalization by detecting when a proposed blog topic is
too semantically similar to existing published content. Uses local embeddings
(sentence-transformers/all-MiniLM-L6-v2) for fast, free similarity checks.

Similarity thresholds:
  < 0.70  → CLEAR: proceed with this topic
  0.70-0.85 → CAUTION: overlaps with existing content, differentiate angle
  > 0.85  → BLOCKED: too close, do not proceed without manual override
"""

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"

CLEAR_THRESHOLD = 0.70
BLOCKED_THRESHOLD = 0.85

# Shared model instance — set by pipeline.py at startup
_embedding_model = None


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
    """Load the blog registry from disk."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _save_registry(registry: dict) -> None:
    """Save the blog registry to disk."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _ensure_embeddings(registry: dict) -> list[dict]:
    """Ensure all published blogs have embeddings. Generate missing ones.

    Returns list of published blogs with embeddings populated.
    """
    model = _get_embedding_model()
    updated = False
    published = []

    for blog in registry.get("blogs", []):
        if blog.get("status") != "published":
            continue

        title = blog.get("title", "")
        if not title:
            continue

        if blog.get("embedding") is None:
            emb = model.encode(title)
            blog["embedding"] = emb.tolist()
            updated = True
            logger.debug("Generated embedding for: %s", title)

        published.append(blog)

    if updated:
        _save_registry(registry)
        logger.info("Generated and saved missing title embeddings.")

    return published


def check_similarity(topic: str, override: bool = False) -> dict:
    """Check a proposed topic against all existing published blog titles.

    Args:
        topic: The proposed topic or title string.
        override: If True, return results but don't block even if similarity
                  exceeds the BLOCKED threshold.

    Returns a dict with:
        status: "CLEAR" | "CAUTION" | "BLOCKED"
        max_similarity: float — highest similarity score found
        similar_blogs: list of dicts with title, similarity, id for matches
                       above CLEAR_THRESHOLD
        topic: the input topic
        override: whether override was requested
    """
    registry = _load_registry()
    published = _ensure_embeddings(registry)

    if not published:
        logger.info("No published blogs in registry — topic is CLEAR by default.")
        return {
            "status": "CLEAR",
            "max_similarity": 0.0,
            "similar_blogs": [],
            "topic": topic,
            "override": override,
        }

    model = _get_embedding_model()
    topic_embedding = model.encode(topic)

    similar_blogs = []
    max_sim = 0.0

    for blog in published:
        blog_embedding = np.array(blog["embedding"])
        sim = _cosine_similarity(topic_embedding, blog_embedding)

        if sim > max_sim:
            max_sim = sim

        if sim >= CLEAR_THRESHOLD:
            similar_blogs.append({
                "id": blog.get("id"),
                "title": blog.get("title", ""),
                "similarity": round(sim, 4),
                "url": blog.get("url", ""),
                "category": blog.get("category", ""),
            })

    # Sort by similarity descending
    similar_blogs.sort(key=lambda x: x["similarity"], reverse=True)

    # Determine status
    if max_sim > BLOCKED_THRESHOLD:
        status = "BLOCKED" if not override else "BLOCKED_OVERRIDE"
    elif max_sim >= CLEAR_THRESHOLD:
        status = "CAUTION"
    else:
        status = "CLEAR"

    if status == "BLOCKED":
        top_match = similar_blogs[0] if similar_blogs else {}
        logger.warning(
            "Topic BLOCKED: '%s' is too similar to '%s' (similarity: %.3f). "
            "Use override=True to proceed anyway.",
            topic,
            top_match.get("title", "unknown"),
            max_sim,
        )
    elif status == "BLOCKED_OVERRIDE":
        top_match = similar_blogs[0] if similar_blogs else {}
        logger.warning(
            "Topic BLOCKED but override requested: '%s' similar to '%s' (similarity: %.3f).",
            topic,
            top_match.get("title", "unknown"),
            max_sim,
        )
    elif status == "CAUTION":
        logger.info(
            "Topic CAUTION: '%s' overlaps with existing content (max similarity: %.3f). "
            "Consider differentiating the angle.",
            topic,
            max_sim,
        )
    else:
        logger.info(
            "Topic CLEAR: '%s' (max similarity: %.3f).",
            topic,
            max_sim,
        )

    return {
        "status": status,
        "max_similarity": round(max_sim, 4),
        "similar_blogs": similar_blogs,
        "topic": topic,
        "override": override,
    }


def seed_registry(blogs: list[dict]) -> int:
    """Seed the registry with existing blog titles and generate embeddings.

    Use this for initial setup to populate the registry with all existing
    published blogs.

    Args:
        blogs: List of dicts with at minimum:
            - id: int
            - title: str
            - category: str (e.g., "Lifestyle", "Learning")
            - url: str (relative URL path)
            - publish_date: str (ISO format or YYYY-MM-DD)
            Optional:
            - status: str (defaults to "published")
            - content: str

    Returns the number of blogs seeded.
    """
    registry = _load_registry()
    existing_ids = {b.get("id") for b in registry.get("blogs", [])}
    model = _get_embedding_model()
    added = 0

    for blog in blogs:
        blog_id = blog.get("id")
        if blog_id in existing_ids:
            logger.debug("Skipping blog #%s — already in registry.", blog_id)
            continue

        title = blog.get("title", "")
        if not title:
            continue

        embedding = model.encode(title)

        entry = {
            "id": blog_id,
            "title": title,
            "category": blog.get("category", "Uncategorized"),
            "url": blog.get("url", ""),
            "publish_date": blog.get("publish_date", ""),
            "embedding": embedding.tolist(),
            "status": blog.get("status", "published"),
            "consolidated_into": None,
            "redirect_target": None,
            "content": blog.get("content", ""),
            "performance": {
                "views_30d": None,
                "ctr": None,
                "avg_time_on_page": None,
                "last_updated": None,
            },
        }
        registry.setdefault("blogs", []).append(entry)
        existing_ids.add(blog_id)
        added += 1
        logger.debug("Seeded blog #%s: %s", blog_id, title)

    if added > 0:
        _save_registry(registry)
        logger.info("Seeded %d blogs into registry.", added)

    return added


def get_all_published_titles() -> list[str]:
    """Return a list of all published blog titles in the registry."""
    registry = _load_registry()
    return [
        b.get("title", "")
        for b in registry.get("blogs", [])
        if b.get("status") == "published" and b.get("title")
    ]
