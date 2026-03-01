"""Duplicate Blog Consolidation Module.

Identifies and manages consolidation of existing duplicate blog posts that
are cannibalizing each other's SEO performance. Also provides ongoing
monitoring to prevent future duplicates.

The bot CANNOT perform consolidation autonomously because it requires:
- Manual content review and merging
- WordPress/CMS redirect configuration
- Editorial judgment on which content to keep

The bot's role is to:
1. Flag duplicate clusters with performance data.
2. Recommend which blog to keep (based on analytics).
3. Provide a diff of unique content in each losing blog.
4. Wait for manual confirmation that consolidation is complete.
5. Update blog_registry.json after confirmation.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"

# Similarity threshold for flagging clusters
CLUSTER_THRESHOLD = 0.75

# Known duplicate clusters (pre-identified)
KNOWN_CLUSTERS = [
    {
        "name": "Post-College Friendship Difficulty",
        "blog_ids": [6, 9, 22],
        "titles": [
            "Why Making Friends After College Feels So Hard",
            "Why It's Hard to Make Friends After College",
            "Why Do Friendships Fade After High School or College?",
        ],
        "default_winner_id": 22,
        "default_winner_reason": (
            "Blog #22 has the broadest scope (covers both high school AND "
            "college) and is most likely to capture the widest keyword intent. "
            "Analytics data overrides this recommendation."
        ),
        "status": "pending",
    },
    {
        "name": "New City Friendship",
        "blog_ids": [14, 34],
        "titles": [
            "How to Build a Social Life From Scratch in a New City",
            "Making Friends in a New City",
        ],
        "default_winner_id": 14,
        "default_winner_reason": (
            "Blog #14's title is more specific and actionable, which performs "
            "better for SEO. Analytics data overrides if #34 outperforms."
        ),
        "status": "pending",
    },
    {
        "name": "City-Specific Friend-Making",
        "blog_ids": [23, 24],
        "titles": [
            "How to Meet New People in Kitchener as an Adult",
            "How to Make Friends in Toronto After 25",
        ],
        "default_winner_id": None,
        "default_winner_reason": (
            "NO ACTION NEEDED. These target different cities and different "
            "search intents ('friends Kitchener' vs 'friends Toronto' are "
            "distinct audiences). Flagged as CAUTION so future city-specific "
            "blogs don't create a third variant without good reason."
        ),
        "status": "no_action",
    },
]


def _load_registry() -> dict:
    """Load the blog registry from disk."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _save_registry(registry: dict) -> None:
    """Save the blog registry to disk."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _get_blog_by_id(registry: dict, blog_id: int) -> Optional[dict]:
    """Find a blog in the registry by ID."""
    for blog in registry.get("blogs", []):
        if blog.get("id") == blog_id:
            return blog
    return None


def _compute_composite_score(blog: dict) -> float:
    """Compute composite score for a blog (CTR * 0.6 + time_on_page * 0.4).

    Returns 0.0 if performance data is missing.
    """
    perf = blog.get("performance", {})
    ctr = perf.get("ctr")
    time_on_page = perf.get("avg_time_on_page")

    if ctr is None or time_on_page is None:
        return 0.0

    # Normalize: CTR is typically 0-0.1, time is 0-300s
    # Use raw values for comparison within a cluster
    return (ctr * 0.6) + (time_on_page / 300.0 * 0.4)


def _extract_unique_sections(content: str) -> list[str]:
    """Extract distinct content sections from a blog for diff comparison."""
    if not content:
        return []

    # Split by headers
    sections = re.split(r"(?=#{1,4}\s)", content)
    return [s.strip() for s in sections if s.strip() and len(s.strip()) > 50]


def check_known_clusters() -> list[dict]:
    """Check the status of known duplicate clusters and return recommendations.

    Returns a list of cluster reports with performance data and recommendations.
    """
    registry = _load_registry()
    reports = []

    for cluster in KNOWN_CLUSTERS:
        report = {
            "name": cluster["name"],
            "status": cluster["status"],
            "blog_ids": cluster["blog_ids"],
            "blogs": [],
            "recommendation": None,
        }

        if cluster["status"] == "no_action":
            report["recommendation"] = cluster["default_winner_reason"]
            reports.append(report)
            continue

        # Pull performance data for each blog in the cluster
        best_score = -1.0
        best_blog = None

        for blog_id in cluster["blog_ids"]:
            blog = _get_blog_by_id(registry, blog_id)
            if blog is None:
                report["blogs"].append({
                    "id": blog_id,
                    "title": "(not in registry)",
                    "composite_score": 0.0,
                    "performance": None,
                })
                continue

            score = _compute_composite_score(blog)
            blog_info = {
                "id": blog_id,
                "title": blog.get("title", ""),
                "composite_score": round(score, 4),
                "performance": blog.get("performance", {}),
                "status": blog.get("status", "published"),
            }
            report["blogs"].append(blog_info)

            if score > best_score and blog.get("status") == "published":
                best_score = score
                best_blog = blog_info

        # Determine recommendation
        if best_blog and best_score > 0:
            report["recommendation"] = (
                f"KEEP blog #{best_blog['id']} ('{best_blog['title']}') — "
                f"highest composite score ({best_blog['composite_score']}). "
                f"Consolidate others into this one with 301 redirects."
            )
        else:
            report["recommendation"] = (
                f"DEFAULT: {cluster['default_winner_reason']} "
                f"(No analytics data available to override.)"
            )

        reports.append(report)

    return reports


def get_consolidation_plan(cluster_name: str) -> Optional[dict]:
    """Get a detailed consolidation plan for a specific cluster.

    Returns the plan with winner recommendation, content diff, and
    step-by-step instructions.
    """
    registry = _load_registry()

    # Find the cluster
    cluster = None
    for c in KNOWN_CLUSTERS:
        if c["name"] == cluster_name:
            cluster = c
            break

    if cluster is None:
        logger.warning("Cluster '%s' not found.", cluster_name)
        return None

    if cluster["status"] == "no_action":
        return {
            "cluster": cluster_name,
            "action": "none",
            "reason": cluster["default_winner_reason"],
        }

    # Gather blog data
    blogs = []
    for blog_id in cluster["blog_ids"]:
        blog = _get_blog_by_id(registry, blog_id)
        if blog:
            blogs.append(blog)

    if not blogs:
        return {
            "cluster": cluster_name,
            "action": "cannot_plan",
            "reason": "No blogs found in registry for this cluster.",
        }

    # Determine winner
    scored = [(b, _compute_composite_score(b)) for b in blogs]
    scored.sort(key=lambda x: x[1], reverse=True)

    winner = scored[0][0]
    losers = [b for b, _ in scored[1:]]

    # Extract unique content from losers
    winner_sections = set(_extract_unique_sections(winner.get("content", "")))
    unique_content = {}

    for loser in losers:
        loser_sections = _extract_unique_sections(loser.get("content", ""))
        unique = [s for s in loser_sections if s not in winner_sections]
        if unique:
            unique_content[loser.get("id")] = {
                "title": loser.get("title", ""),
                "unique_sections": unique,
                "unique_section_count": len(unique),
            }

    plan = {
        "cluster": cluster_name,
        "action": "consolidate",
        "winner": {
            "id": winner.get("id"),
            "title": winner.get("title", ""),
            "url": winner.get("url", ""),
            "composite_score": round(scored[0][1], 4),
        },
        "losers": [
            {
                "id": l.get("id"),
                "title": l.get("title", ""),
                "url": l.get("url", ""),
            }
            for l in losers
        ],
        "unique_content_in_losers": unique_content,
        "steps": [
            f"1. Review unique content from losing blogs (see unique_content_in_losers).",
            f"2. Manually merge any valuable unique content into '{winner.get('title', '')}'.",
            f"3. Update the winner's publish date to the current date after merging.",
            f"4. Set up 301 redirects from losing blog URLs to {winner.get('url', '')}.",
            f"5. Run confirm_consolidation('{cluster_name}', {winner.get('id')}) to update the registry.",
            f"6. Verify redirects return 301 status codes.",
        ],
    }

    return plan


def confirm_consolidation(
    cluster_name: str,
    winner_id: int,
    loser_ids: Optional[list[int]] = None,
) -> bool:
    """Confirm that a consolidation has been completed.

    Updates the registry:
    - Sets losing blogs' status to "consolidated"
    - Sets their consolidated_into field to the winner's ID
    - Sets their redirect_target to the winner's URL
    - Logs the consolidation

    Args:
        cluster_name: Name of the cluster being consolidated.
        winner_id: ID of the blog that was kept.
        loser_ids: IDs of blogs that were consolidated. If None, inferred
                   from the known cluster.

    Returns True if the update succeeded.
    """
    registry = _load_registry()

    # Determine loser IDs
    if loser_ids is None:
        for c in KNOWN_CLUSTERS:
            if c["name"] == cluster_name:
                loser_ids = [bid for bid in c["blog_ids"] if bid != winner_id]
                break

    if loser_ids is None:
        logger.error("Could not determine loser IDs for cluster '%s'.", cluster_name)
        return False

    winner = _get_blog_by_id(registry, winner_id)
    if winner is None:
        logger.error("Winner blog #%d not found in registry.", winner_id)
        return False

    winner_url = winner.get("url", "")

    # Update losers
    for loser_id in loser_ids:
        loser = _get_blog_by_id(registry, loser_id)
        if loser is None:
            logger.warning("Loser blog #%d not found in registry — skipping.", loser_id)
            continue

        loser["status"] = "consolidated"
        loser["consolidated_into"] = winner_id
        loser["redirect_target"] = winner_url
        logger.info(
            "Marked blog #%d ('%s') as consolidated into #%d.",
            loser_id,
            loser.get("title", ""),
            winner_id,
        )

    # Log the consolidation
    log_entry = {
        "cluster": cluster_name,
        "winner_id": winner_id,
        "winner_title": winner.get("title", ""),
        "loser_ids": loser_ids,
        "completed_at": __import__("datetime").datetime.utcnow().isoformat(),
    }
    registry.setdefault("consolidation_log", []).append(log_entry)

    # Update known cluster status
    for c in KNOWN_CLUSTERS:
        if c["name"] == cluster_name:
            c["status"] = "completed"

    _save_registry(registry)
    logger.info(
        "Consolidation confirmed for '%s': winner=#%d, losers=%s.",
        cluster_name,
        winner_id,
        loser_ids,
    )
    return True


def detect_new_clusters(similarity_threshold: float = CLUSTER_THRESHOLD) -> list[dict]:
    """Scan the registry for new potential duplicate clusters.

    Uses embeddings from topic_dedup to find groups of published blogs
    with high semantic similarity.

    Returns list of detected clusters (excluding known/completed ones).
    """
    registry = _load_registry()
    published = [
        b for b in registry.get("blogs", [])
        if b.get("status") == "published" and b.get("embedding")
    ]

    if len(published) < 2:
        return []

    # Build similarity matrix
    known_ids = set()
    for c in KNOWN_CLUSTERS:
        known_ids.update(c["blog_ids"])

    clusters = []
    checked_pairs = set()

    for i, blog_a in enumerate(published):
        for j, blog_b in enumerate(published):
            if i >= j:
                continue

            pair_key = (blog_a.get("id"), blog_b.get("id"))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            # Skip if both are in a known cluster
            if blog_a.get("id") in known_ids and blog_b.get("id") in known_ids:
                continue

            emb_a = np.array(blog_a["embedding"])
            emb_b = np.array(blog_b["embedding"])

            norm_a = np.linalg.norm(emb_a)
            norm_b = np.linalg.norm(emb_b)
            if norm_a == 0 or norm_b == 0:
                continue

            sim = float(np.dot(emb_a, emb_b) / (norm_a * norm_b))

            if sim >= similarity_threshold:
                clusters.append({
                    "blog_a": {
                        "id": blog_a.get("id"),
                        "title": blog_a.get("title", ""),
                    },
                    "blog_b": {
                        "id": blog_b.get("id"),
                        "title": blog_b.get("title", ""),
                    },
                    "similarity": round(sim, 4),
                    "action_needed": sim > 0.85,
                })

    # Sort by similarity descending
    clusters.sort(key=lambda x: x["similarity"], reverse=True)

    if clusters:
        logger.info("Detected %d potential duplicate pair(s).", len(clusters))
    return clusters


def verify_redirect(url: str) -> Optional[dict]:
    """Verify that a 301 redirect is working correctly.

    Makes an HTTP request to the URL and checks the response status.

    Returns a dict with status_code, redirect_target, and verified flag.
    """
    try:
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        # Don't follow redirects
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirectHandler)
        try:
            response = opener.open(req)
            # If we get here, no redirect happened
            return {
                "url": url,
                "status_code": response.status,
                "redirect_target": None,
                "verified": False,
                "detail": "No redirect — page returned directly.",
            }
        except urllib.error.HTTPError as e:
            if e.code == 301:
                redirect_target = e.headers.get("Location", "")
                return {
                    "url": url,
                    "status_code": 301,
                    "redirect_target": redirect_target,
                    "verified": True,
                    "detail": f"301 redirect to {redirect_target}.",
                }
            return {
                "url": url,
                "status_code": e.code,
                "redirect_target": None,
                "verified": False,
                "detail": f"HTTP {e.code}: {e.reason}",
            }
    except Exception as e:
        logger.warning("Redirect verification failed for %s: %s", url, e)
        return {
            "url": url,
            "status_code": None,
            "redirect_target": None,
            "verified": False,
            "detail": f"Error: {e}",
        }


def pending_consolidations() -> list[dict]:
    """Return list of clusters that still need consolidation."""
    return [c for c in KNOWN_CLUSTERS if c["status"] == "pending"]


def status() -> dict:
    """Return overall consolidation status."""
    registry = _load_registry()
    log = registry.get("consolidation_log", [])

    return {
        "known_clusters": len(KNOWN_CLUSTERS),
        "pending": len([c for c in KNOWN_CLUSTERS if c["status"] == "pending"]),
        "completed": len([c for c in KNOWN_CLUSTERS if c["status"] == "completed"]),
        "no_action": len([c for c in KNOWN_CLUSTERS if c["status"] == "no_action"]),
        "consolidation_log_entries": len(log),
    }
