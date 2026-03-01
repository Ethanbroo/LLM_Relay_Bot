"""Analytics Sync Module.

Pulls performance data into blog_registry.json. Supports two data sources:

1. WordPress/Jetpack Stats (primary — always available via WP credentials)
2. Google Search Console + GA4 (optional — requires separate OAuth setup)

The WordPress fallback uses the same APIs as the q_learner, so it works
out of the box with existing credentials.

Schedule: Runs daily (lightweight — just pulls metrics and updates registry).
Can be triggered manually.

This module does NOT make decisions — it only collects and stores data.
Decision-making happens in few_shot_refresher.py, title_scorer.py, and
duplicate_consolidator.py.
"""

import json
import logging
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WP_TIMEOUT = 15

CONFIG_DIR = Path(__file__).parent.parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"

# Significant change thresholds for flagging
CTR_DROP_THRESHOLD = 0.30       # 30% drop in 30 days
VIEWS_INCREASE_THRESHOLD = 0.50  # 50% increase in 30 days
POSITION_IMPROVEMENT = 5         # 5+ spots improvement


def _load_registry() -> dict:
    """Load the blog registry from disk."""
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"blogs": [], "few_shot_history": [], "consolidation_log": []}


def _save_registry(registry: dict) -> None:
    """Save the blog registry to disk."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _get_last_sync_time(registry: dict) -> Optional[datetime]:
    """Get the most recent last_updated timestamp from any blog."""
    latest = None
    for blog in registry.get("blogs", []):
        perf = blog.get("performance", {})
        last_updated = perf.get("last_updated")
        if last_updated:
            try:
                dt = datetime.fromisoformat(last_updated)
                if latest is None or dt > latest:
                    latest = dt
            except ValueError:
                continue
    return latest


def is_sync_due(registry: Optional[dict] = None) -> bool:
    """Check if a sync is due (more than 24 hours since last sync)."""
    if registry is None:
        registry = _load_registry()
    last_sync = _get_last_sync_time(registry)
    if last_sync is None:
        return True
    return (datetime.utcnow() - last_sync).total_seconds() > 86400


def _fetch_search_console_data(
    site_url: str,
    blog_urls: list[str],
    credentials: dict,
) -> dict[str, dict]:
    """Fetch Search Console metrics for blog URLs.

    Returns dict mapping URL path to metrics:
    {
        "/blog/slug": {
            "impressions_30d": int,
            "clicks_30d": int,
            "ctr": float,
            "avg_position": float,
        }
    }
    """
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_info(credentials)
        service = build("searchconsole", "v1", credentials=creds)

        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=30)

        results = {}

        # Batch request — Search Console supports filtering by page
        response = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["page"],
                "rowLimit": 500,
            },
        ).execute()

        for row in response.get("rows", []):
            page_url = row["keys"][0]
            # Match against our blog URLs
            for blog_url in blog_urls:
                if blog_url in page_url:
                    results[blog_url] = {
                        "impressions_30d": int(row.get("impressions", 0)),
                        "clicks_30d": int(row.get("clicks", 0)),
                        "ctr": round(row.get("ctr", 0), 6),
                        "avg_position": round(row.get("position", 0), 1),
                    }
                    break

        return results

    except ImportError:
        logger.warning(
            "Google API client not installed. "
            "Install with: pip install google-api-python-client google-auth"
        )
        return {}
    except Exception as e:
        logger.error("Search Console fetch failed: %s", e)
        return {}


def _fetch_analytics_data(
    property_id: str,
    blog_urls: list[str],
    credentials: dict,
) -> dict[str, dict]:
    """Fetch Google Analytics metrics for blog URLs.

    Returns dict mapping URL path to metrics:
    {
        "/blog/slug": {
            "views_30d": int,
            "views_total": int,
            "avg_time_on_page": float,
            "bounce_rate": float,
        }
    }
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
            FilterExpression,
            Filter,
        )

        creds = Credentials.from_authorized_user_info(credentials)
        client = BetaAnalyticsDataClient(credentials=creds)

        end_date = datetime.utcnow().date()
        start_date_30d = end_date - timedelta(days=30)

        # 30-day metrics
        request_30d = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(
                start_date=start_date_30d.isoformat(),
                end_date=end_date.isoformat(),
            )],
            dimensions=[Dimension(name="pagePath")],
            metrics=[
                Metric(name="screenPageViews"),
                Metric(name="averageSessionDuration"),
                Metric(name="bounceRate"),
            ],
            limit=500,
        )

        response_30d = client.run_report(request_30d)

        results = {}
        for row in response_30d.rows:
            page_path = row.dimension_values[0].value
            for blog_url in blog_urls:
                if blog_url in page_path:
                    results[blog_url] = {
                        "views_30d": int(row.metric_values[0].value),
                        "avg_time_on_page": round(
                            float(row.metric_values[1].value), 1
                        ),
                        "bounce_rate": round(
                            float(row.metric_values[2].value), 4
                        ),
                    }
                    break

        # Total views (all time)
        request_total = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(
                start_date="2020-01-01",
                end_date=end_date.isoformat(),
            )],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            limit=500,
        )

        response_total = client.run_report(request_total)

        for row in response_total.rows:
            page_path = row.dimension_values[0].value
            for blog_url in blog_urls:
                if blog_url in page_path and blog_url in results:
                    results[blog_url]["views_total"] = int(
                        row.metric_values[0].value
                    )
                    break

        return results

    except ImportError:
        logger.warning(
            "Google Analytics client not installed. "
            "Install with: pip install google-analytics-data"
        )
        return {}
    except Exception as e:
        logger.error("Analytics fetch failed: %s", e)
        return {}


def _detect_significant_changes(
    blog: dict,
    old_perf: dict,
    new_perf: dict,
) -> list[dict]:
    """Detect significant performance changes for a blog.

    Returns list of change alerts.
    """
    alerts = []
    title = blog.get("title", f"Blog #{blog.get('id', '?')}")

    # CTR drop
    old_ctr = old_perf.get("ctr")
    new_ctr = new_perf.get("ctr")
    if old_ctr and new_ctr and old_ctr > 0:
        ctr_change = (new_ctr - old_ctr) / old_ctr
        if ctr_change < -CTR_DROP_THRESHOLD:
            alerts.append({
                "type": "ctr_drop",
                "severity": "warning",
                "blog_id": blog.get("id"),
                "title": title,
                "detail": (
                    f"CTR dropped {abs(ctr_change)*100:.1f}% "
                    f"({old_ctr:.4f} → {new_ctr:.4f})"
                ),
            })

    # Views increase
    old_views = old_perf.get("views_30d")
    new_views = new_perf.get("views_30d")
    if old_views and new_views and old_views > 0:
        views_change = (new_views - old_views) / old_views
        if views_change > VIEWS_INCREASE_THRESHOLD:
            alerts.append({
                "type": "views_increase",
                "severity": "positive",
                "blog_id": blog.get("id"),
                "title": title,
                "detail": (
                    f"Views increased {views_change*100:.1f}% "
                    f"({old_views} → {new_views})"
                ),
            })

    # Position improvement
    old_pos = old_perf.get("avg_position")
    new_pos = new_perf.get("avg_position")
    if old_pos and new_pos:
        pos_improvement = old_pos - new_pos  # Lower is better
        if pos_improvement >= POSITION_IMPROVEMENT:
            alerts.append({
                "type": "position_improvement",
                "severity": "positive",
                "blog_id": blog.get("id"),
                "title": title,
                "detail": (
                    f"Average position improved by {pos_improvement:.1f} spots "
                    f"({old_pos:.1f} → {new_pos:.1f})"
                ),
            })

    return alerts


def sync(
    site_url: Optional[str] = None,
    analytics_property_id: Optional[str] = None,
    credentials_path: Optional[str] = None,
    force: bool = False,
) -> dict:
    """Main entry point: sync analytics data into blog_registry.json.

    Args:
        site_url: Google Search Console site URL
                  (e.g., "https://friendlyconnections.services").
        analytics_property_id: Google Analytics 4 property ID.
        credentials_path: Path to Google OAuth credentials JSON file.
        force: If True, sync even if not due.

    Returns a dict with sync results.
    """
    registry = _load_registry()

    if not force and not is_sync_due(registry):
        logger.info("Analytics sync not due yet — skipping.")
        return {"synced": False, "reason": "not_due"}

    # Load credentials
    credentials = None
    creds_path = credentials_path or os.environ.get("GOOGLE_CREDENTIALS_PATH")
    if creds_path and Path(creds_path).exists():
        try:
            credentials = json.loads(Path(creds_path).read_text())
        except Exception as e:
            logger.error("Failed to load Google credentials: %s", e)

    if credentials is None:
        logger.warning(
            "No Google credentials available — cannot sync analytics. "
            "Set GOOGLE_CREDENTIALS_PATH or pass credentials_path."
        )
        return {"synced": False, "reason": "no_credentials"}

    # Validate: must be authorized user info (has refresh_token), not client config
    if "installed" in credentials or "web" in credentials:
        logger.warning(
            "GOOGLE_CREDENTIALS_PATH points to an OAuth client config, not "
            "authorized credentials. Run: poetry run python credentials/authorize_google.py"
        )
        return {"synced": False, "reason": "credentials_not_authorized"}
    if "refresh_token" not in credentials:
        logger.warning(
            "Google credentials missing refresh_token. "
            "Run: poetry run python credentials/authorize_google.py"
        )
        return {"synced": False, "reason": "credentials_missing_refresh_token"}

    # Get site URL and property ID from env if not provided
    site_url = site_url or os.environ.get(
        "GOOGLE_SITE_URL", "https://friendlyconnections.services"
    )
    analytics_property_id = analytics_property_id or os.environ.get(
        "GOOGLE_ANALYTICS_PROPERTY_ID", ""
    )

    # Collect blog URLs
    published_blogs = [
        b for b in registry.get("blogs", [])
        if b.get("status") == "published"
    ]
    blog_urls = [b.get("url", "") for b in published_blogs if b.get("url")]

    if not blog_urls:
        logger.warning("No published blog URLs in registry — nothing to sync.")
        return {"synced": False, "reason": "no_blogs"}

    # Fetch data
    search_data = _fetch_search_console_data(site_url, blog_urls, credentials)
    analytics_data = {}
    if analytics_property_id:
        analytics_data = _fetch_analytics_data(
            analytics_property_id, blog_urls, credentials
        )

    # Update registry
    now = datetime.utcnow().isoformat()
    updated_count = 0
    all_alerts = []

    for blog in published_blogs:
        blog_url = blog.get("url", "")
        if not blog_url:
            continue

        old_perf = dict(blog.get("performance", {}))
        new_perf = dict(old_perf)  # Start with existing data

        # Merge Search Console data
        sc_data = search_data.get(blog_url, {})
        if sc_data:
            new_perf["impressions_30d"] = sc_data.get("impressions_30d")
            new_perf["ctr"] = sc_data.get("ctr")
            new_perf["avg_position"] = sc_data.get("avg_position")

        # Merge Analytics data
        ga_data = analytics_data.get(blog_url, {})
        if ga_data:
            new_perf["views_30d"] = ga_data.get("views_30d")
            new_perf["views_total"] = ga_data.get("views_total", new_perf.get("views_total"))
            new_perf["avg_time_on_page"] = ga_data.get("avg_time_on_page")
            new_perf["bounce_rate"] = ga_data.get("bounce_rate")

        if sc_data or ga_data:
            new_perf["last_updated"] = now
            blog["performance"] = new_perf
            updated_count += 1

            # Check for significant changes
            alerts = _detect_significant_changes(blog, old_perf, new_perf)
            all_alerts.extend(alerts)

    _save_registry(registry)

    # Log alerts
    for alert in all_alerts:
        if alert["severity"] == "warning":
            logger.warning("ALERT: %s — %s", alert["title"], alert["detail"])
        else:
            logger.info("SIGNAL: %s — %s", alert["title"], alert["detail"])

    logger.info(
        "Analytics sync complete: %d/%d blogs updated, %d alert(s).",
        updated_count,
        len(published_blogs),
        len(all_alerts),
    )

    return {
        "synced": True,
        "blogs_updated": updated_count,
        "blogs_total": len(published_blogs),
        "alerts": all_alerts,
        "search_console_results": len(search_data),
        "analytics_results": len(analytics_data),
    }


def sync_from_wordpress(force: bool = False) -> dict:
    """Sync analytics from WordPress/Jetpack Stats (no Google credentials needed).

    Uses the same WP REST API + Jetpack Stats endpoints as the q_learner.
    Populates views_30d, ctr (view-based proxy), and avg_time_on_page
    into blog_registry.json so downstream modules (few_shot_refresher,
    duplicate_consolidator) can function.

    Returns a dict with sync results.
    """
    registry = _load_registry()

    if not force and not is_sync_due(registry):
        logger.info("Analytics sync (WP) not due yet — skipping.")
        return {"synced": False, "reason": "not_due"}

    base_url = os.environ.get("LLM_RELAY_SECRET_WP_BASE_URL", "").rstrip("/")
    username = os.environ.get("LLM_RELAY_SECRET_WP_USERNAME", "")
    app_password = os.environ.get("LLM_RELAY_SECRET_WP_APP_PASSWORD", "")

    if not base_url or not username or not app_password:
        logger.warning("WordPress credentials not set — cannot sync analytics.")
        return {"synced": False, "reason": "no_wp_credentials"}

    auth = (username, app_password)
    published_blogs = [
        b for b in registry.get("blogs", [])
        if b.get("status") == "published" and b.get("id")
    ]

    if not published_blogs:
        logger.warning("No published blogs in registry — nothing to sync.")
        return {"synced": False, "reason": "no_blogs"}

    now = datetime.utcnow().isoformat()
    updated_count = 0
    all_alerts = []

    # Fetch total views across all blogs for CTR normalization
    all_views = []
    blog_stats = {}

    for blog in published_blogs:
        post_id = blog["id"]
        views = _fetch_jetpack_views(base_url, auth, post_id)
        comments = _fetch_wp_comment_count(base_url, auth, post_id)
        if views is not None:
            blog_stats[post_id] = {"views": views, "comments": comments or 0}
            all_views.append(views)

    # Compute normalization factors
    max_views = max(all_views) if all_views else 1
    total_views = sum(all_views) if all_views else 1

    for blog in published_blogs:
        post_id = blog["id"]
        stats = blog_stats.get(post_id)
        if stats is None:
            continue

        old_perf = dict(blog.get("performance", {}))
        new_perf = dict(old_perf)

        views = stats["views"]
        comments = stats["comments"]

        new_perf["views_30d"] = views
        # CTR proxy: view share relative to total (normalized 0-1)
        new_perf["ctr"] = round(views / total_views, 6) if total_views > 0 else 0.0
        # Engagement proxy: comments-per-view as avg_time_on_page stand-in (seconds)
        # Scale: 1 comment ≈ 120s engagement, plus base of 60s per view
        comment_engagement = (comments / max(views, 1)) * 120.0
        new_perf["avg_time_on_page"] = round(60.0 + comment_engagement + (views * 0.1), 1)
        new_perf["last_updated"] = now

        blog["performance"] = new_perf
        updated_count += 1

        alerts = _detect_significant_changes(blog, old_perf, new_perf)
        all_alerts.extend(alerts)

    _save_registry(registry)

    for alert in all_alerts:
        if alert["severity"] == "warning":
            logger.warning("ALERT: %s — %s", alert["title"], alert["detail"])
        else:
            logger.info("SIGNAL: %s — %s", alert["title"], alert["detail"])

    logger.info(
        "Analytics sync (WordPress): %d/%d blogs updated, %d alert(s).",
        updated_count,
        len(published_blogs),
        len(all_alerts),
    )

    # Check pending image feedback (AI-generated cover images)
    image_feedback_summary = {}
    try:
        from src.image_feedback_tracker import check_all_pending
        image_feedback_summary = check_all_pending()
        if image_feedback_summary.get("checked", 0) > 0:
            logger.info(
                "Image feedback: checked=%d accepted=%d rejected=%d",
                image_feedback_summary.get("checked", 0),
                image_feedback_summary.get("accepted", 0),
                image_feedback_summary.get("rejected", 0),
            )
    except Exception as e:
        logger.warning("Image feedback check failed: %s", e)

    return {
        "synced": True,
        "source": "wordpress",
        "blogs_updated": updated_count,
        "blogs_total": len(published_blogs),
        "alerts": all_alerts,
        "image_feedback": image_feedback_summary,
    }


def _fetch_jetpack_views(base_url: str, auth: tuple, post_id: int) -> Optional[int]:
    """Fetch view count from Jetpack Stats API. Returns None on failure."""
    try:
        url = f"{base_url}/wp-json/jetpack/v4/stats/post/{post_id}"
        resp = requests.get(url, auth=auth, timeout=WP_TIMEOUT)
        if resp.ok:
            return int(resp.json().get("views", 0))
    except Exception as e:
        logger.debug("Jetpack views fetch failed for post %s: %s", post_id, e)
    # Fallback: try WP REST API for basic info
    try:
        url = f"{base_url}/wp-json/wp/v2/posts/{post_id}"
        resp = requests.get(url, auth=auth, timeout=WP_TIMEOUT)
        if resp.ok:
            return 0  # Post exists but no Jetpack — return 0 views
    except Exception:
        pass
    return None


def _fetch_wp_comment_count(base_url: str, auth: tuple, post_id: int) -> Optional[int]:
    """Fetch comment count from WP REST API."""
    try:
        url = f"{base_url}/wp-json/wp/v2/posts/{post_id}"
        resp = requests.get(url, auth=auth, timeout=WP_TIMEOUT)
        if resp.ok:
            return int(resp.json().get("comment_count", 0))
    except Exception as e:
        logger.debug("Comment count fetch failed for post %s: %s", post_id, e)
    return None


def _fetch_demographic_data(
    property_id: str,
    credentials: dict,
) -> dict:
    """Fetch demographic breakdown from Google Analytics 4.

    Returns dict with age_data and gender_data:
    {
        "age_data": {"18-24": 120, "25-34": 450, ...},
        "gender_data": {"male": 400, "female": 350, ...}
    }
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
        )

        creds = Credentials.from_authorized_user_info(credentials)
        client = BetaAnalyticsDataClient(credentials=creds)

        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=30)

        # Age breakdown
        age_request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )],
            dimensions=[Dimension(name="userAgeBracket")],
            metrics=[Metric(name="sessions")],
        )
        age_response = client.run_report(age_request)

        age_data = {}
        for row in age_response.rows:
            age_bracket = row.dimension_values[0].value
            sessions = int(row.metric_values[0].value)
            if age_bracket and age_bracket != "(not set)":
                age_data[age_bracket] = sessions

        # Gender breakdown
        gender_request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )],
            dimensions=[Dimension(name="userGender")],
            metrics=[Metric(name="sessions")],
        )
        gender_response = client.run_report(gender_request)

        gender_data = {}
        for row in gender_response.rows:
            gender = row.dimension_values[0].value
            sessions = int(row.metric_values[0].value)
            if gender and gender != "(not set)":
                gender_data[gender] = sessions

        return {"age_data": age_data, "gender_data": gender_data}

    except ImportError:
        logger.debug("Google Analytics client not installed — skipping demographic fetch.")
        return {}
    except Exception as e:
        logger.warning("Demographic data fetch failed: %s", e)
        return {}


def sync_demographics(
    analytics_property_id: Optional[str] = None,
    credentials_path: Optional[str] = None,
) -> dict:
    """Fetch and store demographic data from GA4 into demographic_profile.json.

    Called during analytics sync. Updates the demographic targeting module
    with fresh audience data.

    Returns dict with sync status.
    """
    from . import demographic_targeting

    property_id = analytics_property_id or os.environ.get("GOOGLE_ANALYTICS_PROPERTY_ID", "")
    if not property_id:
        return {"synced": False, "reason": "no_property_id"}

    creds_path = credentials_path or os.environ.get("GOOGLE_CREDENTIALS_PATH")
    if not creds_path or not Path(creds_path).exists():
        return {"synced": False, "reason": "no_credentials"}

    try:
        credentials = json.loads(Path(creds_path).read_text())
    except Exception as e:
        return {"synced": False, "reason": f"credentials_error: {e}"}

    # Validate: must be authorized user info, not client config
    if "installed" in credentials or "web" in credentials:
        return {"synced": False, "reason": "credentials_not_authorized"}
    if "refresh_token" not in credentials:
        return {"synced": False, "reason": "credentials_missing_refresh_token"}

    demo_data = _fetch_demographic_data(property_id, credentials)
    if not demo_data or not demo_data.get("age_data"):
        return {"synced": False, "reason": "no_demographic_data_returned"}

    result = demographic_targeting.update_demographic_profile(
        age_data=demo_data["age_data"],
        gender_data=demo_data.get("gender_data"),
    )

    return {"synced": True, **result}


def get_top_performers(n: int = 5) -> list[dict]:
    """Return the top N performing blogs by composite score.

    Composite score: CTR * 0.6 + normalized_time_on_page * 0.4
    """
    registry = _load_registry()
    published = [
        b for b in registry.get("blogs", [])
        if b.get("status") == "published"
        and b.get("performance", {}).get("ctr") is not None
        and b.get("performance", {}).get("avg_time_on_page") is not None
    ]

    if not published:
        return []

    # Normalize time on page for scoring
    times = [b["performance"]["avg_time_on_page"] for b in published]
    max_time = max(times) if times else 1.0

    scored = []
    for blog in published:
        perf = blog["performance"]
        norm_time = perf["avg_time_on_page"] / max_time if max_time > 0 else 0
        score = (perf["ctr"] * 0.6) + (norm_time * 0.4)
        scored.append({
            "id": blog.get("id"),
            "title": blog.get("title", ""),
            "composite_score": round(score, 4),
            "ctr": perf["ctr"],
            "avg_time_on_page": perf["avg_time_on_page"],
            "views_30d": perf.get("views_30d"),
        })

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored[:n]


def get_underperformers(n: int = 5) -> list[dict]:
    """Return the bottom N performing blogs by composite score."""
    registry = _load_registry()
    published = [
        b for b in registry.get("blogs", [])
        if b.get("status") == "published"
        and b.get("performance", {}).get("ctr") is not None
        and b.get("performance", {}).get("avg_time_on_page") is not None
    ]

    if not published:
        return []

    times = [b["performance"]["avg_time_on_page"] for b in published]
    max_time = max(times) if times else 1.0

    scored = []
    for blog in published:
        perf = blog["performance"]
        norm_time = perf["avg_time_on_page"] / max_time if max_time > 0 else 0
        score = (perf["ctr"] * 0.6) + (norm_time * 0.4)
        scored.append({
            "id": blog.get("id"),
            "title": blog.get("title", ""),
            "composite_score": round(score, 4),
            "ctr": perf["ctr"],
            "avg_time_on_page": perf["avg_time_on_page"],
            "views_30d": perf.get("views_30d"),
        })

    scored.sort(key=lambda x: x["composite_score"])
    return scored[:n]
