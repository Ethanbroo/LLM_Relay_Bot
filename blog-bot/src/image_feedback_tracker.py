"""Passive feedback loop for AI-generated blog cover images.

Checks WordPress drafts that have been published to detect whether the user
kept the AI-generated cover image or swapped it for a different one.

Decision logic:
  - Still "draft" → skip, check again later
  - "publish" + featured_media == stored_media_id → ACCEPTED
  - "publish" + featured_media != stored_media_id → REJECTED (changed)
  - "publish" + featured_media == 0 → REJECTED (removed)
  - "trash" or 404 → blog deleted, no image signal

Called during analytics sync (analytics_sync.py → sync_from_wordpress).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"
PREFERENCES_PATH = CONFIG_DIR / "image_preferences.json"

# Cold start threshold: stop adding random variance after this many feedbacks
COLD_START_THRESHOLD = 10

# Preference decay: months of inactivity before decay kicks in
DECAY_INACTIVITY_MONTHS = 6
DECAY_RATE_PER_MONTH = 0.05  # drift toward 0.5 per month of inactivity

WP_TIMEOUT = 15


def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _save_registry(registry: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _load_preferences() -> dict:
    if PREFERENCES_PATH.exists():
        return json.loads(PREFERENCES_PATH.read_text())
    return {"cold_start_active": True, "total_blogs_with_feedback": 0, "attributes": {}}


def _save_preferences(prefs: dict) -> None:
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(prefs, indent=2))


def _fetch_post_status(base_url: str, auth: tuple, post_id: int) -> Optional[dict]:
    """Fetch post status and featured_media from WordPress REST API.

    Returns dict with 'status' and 'featured_media' keys, or None on failure.
    """
    url = f"{base_url}/wp-json/wp/v2/posts/{post_id}"
    try:
        resp = requests.get(url, auth=auth, timeout=WP_TIMEOUT)
        if resp.status_code == 404:
            return {"status": "not_found", "featured_media": 0}
        resp.raise_for_status()
        data = resp.json()
        return {
            "status": data.get("status", "unknown"),
            "featured_media": data.get("featured_media", 0),
        }
    except Exception as e:
        logger.warning("Failed to fetch post %d status: %s", post_id, e)
        return None


def _update_preferences(prefs: dict, attributes: dict, accepted: bool) -> None:
    """Update acceptance rates for an image's attributes."""
    for category, value in attributes.items():
        if category not in prefs.get("attributes", {}):
            continue
        if value not in prefs["attributes"][category]:
            continue

        entry = prefs["attributes"][category][value]
        entry["total"] = entry.get("total", 0) + 1
        if accepted:
            entry["accepted"] = entry.get("accepted", 0) + 1

        total = entry["total"]
        acc = entry["accepted"]
        entry["rate"] = round(acc / total, 4) if total > 0 else 0.5
        entry["last_feedback"] = datetime.now(timezone.utc).isoformat()

    prefs["total_blogs_with_feedback"] = prefs.get("total_blogs_with_feedback", 0) + 1
    prefs["last_updated"] = datetime.now(timezone.utc).isoformat()


def _check_cold_start_status(prefs: dict) -> None:
    """Disable cold start mode once enough feedback has been collected."""
    if prefs.get("total_blogs_with_feedback", 0) >= COLD_START_THRESHOLD:
        if prefs.get("cold_start_active", True):
            prefs["cold_start_active"] = False
            logger.info(
                "Cold start disabled: %d blogs with feedback (threshold=%d)",
                prefs["total_blogs_with_feedback"],
                COLD_START_THRESHOLD,
            )


def _apply_preference_decay(prefs: dict) -> None:
    """Decay stale preference rates toward 0.5 if no feedback in >6 months."""
    now = datetime.now(timezone.utc)

    for category in prefs.get("attributes", {}).values():
        for value, entry in category.items():
            last_fb = entry.get("last_feedback")
            if not last_fb or entry.get("total", 0) == 0:
                continue

            try:
                last_dt = datetime.fromisoformat(last_fb)
            except (ValueError, TypeError):
                continue

            months_inactive = (now - last_dt).days / 30.0
            if months_inactive <= DECAY_INACTIVITY_MONTHS:
                continue

            excess_months = months_inactive - DECAY_INACTIVITY_MONTHS
            decay = excess_months * DECAY_RATE_PER_MONTH
            current_rate = entry.get("rate", 0.5)

            # Drift toward 0.5
            if current_rate > 0.5:
                entry["rate"] = round(max(0.5, current_rate - decay), 4)
            elif current_rate < 0.5:
                entry["rate"] = round(min(0.5, current_rate + decay), 4)


def check_all_pending() -> dict:
    """Check all blogs with pending image feedback.

    Iterates blog_registry.json entries where image.feedback is null and
    image.draft_media_id exists. For each, checks WordPress post status
    and determines if the AI image was accepted or rejected.

    Returns summary dict: {checked, accepted, rejected, skipped, deleted}.
    """
    base_url = os.environ.get("LLM_RELAY_SECRET_WP_BASE_URL", "").rstrip("/")
    username = os.environ.get("LLM_RELAY_SECRET_WP_USERNAME", "")
    app_password = os.environ.get("LLM_RELAY_SECRET_WP_APP_PASSWORD", "")

    if not base_url or not username or not app_password:
        logger.debug("WordPress credentials not set — skipping image feedback check.")
        return {"checked": 0, "skipped_reason": "no_wp_credentials"}

    auth = (username, app_password)
    registry = _load_registry()
    prefs = _load_preferences()

    summary = {"checked": 0, "accepted": 0, "rejected": 0, "skipped": 0, "deleted": 0}

    for blog in registry.get("blogs", []):
        image_data = blog.get("image")
        if not image_data:
            continue

        # Only check entries with pending feedback
        if image_data.get("feedback") is not None:
            continue

        draft_media_id = image_data.get("draft_media_id")
        if not draft_media_id:
            continue

        post_id = blog.get("id")
        if not post_id:
            continue

        summary["checked"] += 1

        post_info = _fetch_post_status(base_url, auth, post_id)
        if post_info is None:
            summary["skipped"] += 1
            continue

        status = post_info["status"]
        featured_media = post_info["featured_media"]

        if status == "draft":
            # Still a draft — check again later
            summary["skipped"] += 1
            continue

        if status in ("trash", "not_found"):
            # Blog deleted — no image signal
            image_data["feedback"] = "blog_deleted"
            image_data["feedback_at"] = datetime.now(timezone.utc).isoformat()
            summary["deleted"] += 1
            logger.info("Post %d: blog deleted, no image feedback signal.", post_id)
            continue

        if status == "publish":
            accepted = featured_media == int(draft_media_id)

            if accepted:
                image_data["feedback"] = "accepted"
                summary["accepted"] += 1
                logger.info("Post %d: image ACCEPTED (media_id=%s kept).", post_id, draft_media_id)
            else:
                image_data["feedback"] = "rejected"
                summary["rejected"] += 1
                logger.info(
                    "Post %d: image REJECTED (media_id changed: %s → %s).",
                    post_id, draft_media_id, featured_media,
                )

            image_data["feedback_at"] = datetime.now(timezone.utc).isoformat()

            # Update preference rates
            attributes = image_data.get("attributes", {})
            if attributes:
                _update_preferences(prefs, attributes, accepted)

            continue

        # Unknown status — skip
        summary["skipped"] += 1
        logger.debug("Post %d: unknown status '%s', skipping.", post_id, status)

    # Post-processing
    _check_cold_start_status(prefs)
    _apply_preference_decay(prefs)

    _save_preferences(prefs)
    _save_registry(registry)

    logger.info(
        "Image feedback check: checked=%d accepted=%d rejected=%d skipped=%d deleted=%d",
        summary["checked"],
        summary["accepted"],
        summary["rejected"],
        summary["skipped"],
        summary["deleted"],
    )

    return summary
