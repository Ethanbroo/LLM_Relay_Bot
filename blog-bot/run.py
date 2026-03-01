"""Blog Bot CLI — On-demand blog generation and management.

Usage:
    poetry run python blog-bot/run.py generate --topic "..." --fc-angle "..."
    poetry run python blog-bot/run.py generate --topic "..." --fc-angle "..." --dry-run
    poetry run python blog-bot/run.py seed                    # Seed registry with existing blogs
    poetry run python blog-bot/run.py status                  # Show pipeline status
    poetry run python blog-bot/run.py poll                    # Poll analytics and update rewards

For scheduled daily generation, use generate_blog.py (called by launchd):
    poetry run python blog-bot/generate_blog.py               # Daily auto-run with Q-learner
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Add blog-bot to path for src imports
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("blog-bot")


def cmd_generate(args):
    """Generate a single blog post."""
    from src.pipeline import PipelineConfig, run, startup_checks

    print("\n=== Blog Bot — Generate ===\n")

    # Run startup checks
    print("Running startup checks...")
    checks = startup_checks()
    print(f"  Embedding model: {'loaded' if checks['embedding_model'] else 'UNAVAILABLE'}")
    print(f"  FC phase: {checks['fc_phase']}")
    print(f"  Pending consolidations: {checks['pending_consolidations']}")

    if not args.topic:
        print("\nERROR: --topic is required.")
        sys.exit(1)

    if not args.fc_angle and checks["fc_phase"] == 1:
        print("\nERROR: --fc-angle is required (Phase 1 active).")
        print("  Example: --fc-angle \"Mention Friendly Connections as a service that helps people find activity partners.\"")
        sys.exit(1)

    config = PipelineConfig(
        topic=args.topic,
        word_count=args.word_count,
        fc_angle=args.fc_angle,
        fc_angle_type=args.fc_angle_type,
        override_dedup=args.override_dedup,
        skip_title_scoring=args.skip_title_scoring,
        title_override=args.title,
        dry_run=args.dry_run,
    )

    print(f"\n  Topic: {config.topic}")
    print(f"  Word count: {config.word_count}")
    if config.fc_angle:
        print(f"  FC angle: {config.fc_angle[:80]}...")
    if config.title_override:
        print(f"  Title override: {config.title_override}")
    print(f"  Dry run: {config.dry_run}")
    print()

    result = run(config)

    if result.success:
        print(f"\n=== SUCCESS ===")
        print(f"  Title: {result.title}")
        print(f"  Model: {result.model_used}")
        print(f"  Attempts: {result.attempt_count}")
        if result.output_path:
            print(f"  Output: {result.output_path}")
        if result.validation_path:
            print(f"  Validation: {result.validation_path}")
        print(f"\n  Content preview (first 300 chars):")
        print(f"  {result.content[:300]}...")
        if result.metadata_block:
            print(f"\n  METADATA:\n  {result.metadata_block}")
    else:
        print(f"\n=== FAILED ===")
        print(f"  Error: {result.error}")
        if result.content:
            print(f"\n  Best attempt content preview (first 300 chars):")
            print(f"  {result.content[:300]}...")
        if result.validation_report:
            print(f"\n  Validation report:")
            for check in result.validation_report.get("checks", []):
                print(f"    [{check['status']}] {check['name']}: {check['detail']}")
        sys.exit(1)


def cmd_poll(args):
    """Poll analytics and update Q-learner rewards."""
    from generate_blog import run_analytics_poll

    print("\n=== Blog Bot — Analytics Poll ===\n")
    recorded = run_analytics_poll()
    print(f"\n  Recorded {recorded} horizon snapshot(s).")
    print()


def cmd_seed(args):
    """Seed the blog registry with existing WordPress blogs."""
    from src.topic_dedup import seed_registry

    print("\n=== Seeding Blog Registry ===\n")

    # Try to pull existing blogs from WordPress
    wp_base = os.environ.get("LLM_RELAY_SECRET_WP_BASE_URL", "")
    wp_user = os.environ.get("LLM_RELAY_SECRET_WP_USERNAME", "")
    wp_pass = os.environ.get("LLM_RELAY_SECRET_WP_APP_PASSWORD", "")

    if not all([wp_base, wp_user, wp_pass]):
        print("WordPress credentials not configured — cannot auto-seed.")
        print("Set LLM_RELAY_SECRET_WP_BASE_URL, LLM_RELAY_SECRET_WP_USERNAME, LLM_RELAY_SECRET_WP_APP_PASSWORD")
        sys.exit(1)

    import requests
    from requests.auth import HTTPBasicAuth

    print(f"Fetching posts from {wp_base}...")
    blogs = []
    page = 1
    per_page = 50

    while True:
        url = f"{wp_base}/wp-json/wp/v2/posts"
        resp = requests.get(
            url,
            params={"page": page, "per_page": per_page, "status": "publish,draft"},
            auth=HTTPBasicAuth(wp_user, wp_pass),
        )
        if resp.status_code != 200:
            if page == 1:
                print(f"Failed to fetch posts: {resp.status_code} {resp.text[:200]}")
                sys.exit(1)
            break

        posts = resp.json()
        if not posts:
            break

        for post in posts:
            # Determine category
            cat_ids = post.get("categories", [])
            category = "Uncategorized"
            if cat_ids:
                cat_resp = requests.get(
                    f"{wp_base}/wp-json/wp/v2/categories/{cat_ids[0]}",
                    auth=HTTPBasicAuth(wp_user, wp_pass),
                )
                if cat_resp.status_code == 200:
                    category = cat_resp.json().get("name", "Uncategorized")

            blogs.append({
                "id": post["id"],
                "title": post.get("title", {}).get("rendered", ""),
                "category": category,
                "url": f"/blog/{post.get('slug', '')}",
                "publish_date": post.get("date", "")[:10],
                "status": "published" if post.get("status") == "publish" else "draft",
                "content": post.get("content", {}).get("rendered", ""),
            })

        page += 1

    print(f"Found {len(blogs)} posts.")

    if blogs:
        count = seed_registry(blogs)
        print(f"Seeded {count} new blogs into registry.")
    else:
        print("No posts found to seed.")


def cmd_status(args):
    """Show pipeline status."""
    from src.pipeline import startup_checks
    from src import fc_angle_manager, duplicate_consolidator, blog_list_manager

    print("\n=== Blog Bot Status ===\n")

    checks = startup_checks()
    print(f"Embedding model: {'loaded' if checks['embedding_model'] else 'UNAVAILABLE'}")
    print(f"Analytics sync due: {checks['analytics_sync_due']}")
    print(f"Few-shot refresh due: {checks['few_shot_refresh_due']}")
    print(f"Pending consolidations: {checks['pending_consolidations']}")
    print(f"FC integration phase: {checks['fc_phase']}")

    print(f"\nFC Angle Manager:")
    fc_status = fc_angle_manager.status()
    print(f"  Phase: {fc_status['phase']}")
    print(f"  Total angles logged: {fc_status['total_angles']}")
    print(f"  Phase 2 eligible: {fc_status['phase_2_eligible']}")

    print(f"\nBlog Registry:")
    counts = blog_list_manager.get_blog_count()
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    print(f"\nDuplicate Consolidation:")
    consol_status = duplicate_consolidator.status()
    print(f"  Known clusters: {consol_status['known_clusters']}")
    print(f"  Pending: {consol_status['pending']}")
    print(f"  Completed: {consol_status['completed']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Blog Bot — Friendly Connections")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Generate command
    gen_parser = subparsers.add_parser("generate", help="Generate a single blog post")
    gen_parser.add_argument("--topic", "-t", type=str, required=True, help="Blog topic")
    gen_parser.add_argument("--title", type=str, default=None, help="Override generated title")
    gen_parser.add_argument("--word-count", "-w", type=int, default=1500, help="Target word count")
    gen_parser.add_argument("--fc-angle", type=str, default=None, help="FC integration angle (required in Phase 1)")
    gen_parser.add_argument("--fc-angle-type", type=str, default="other", help="FC angle type tag")
    gen_parser.add_argument("--override-dedup", action="store_true", help="Override topic dedup block")
    gen_parser.add_argument("--skip-title-scoring", action="store_true", help="Skip title pre-scoring")
    gen_parser.add_argument("--dry-run", "-d", action="store_true", help="Generate but don't save")

    # Poll command
    subparsers.add_parser("poll", help="Poll analytics and update Q-learner rewards")

    # Seed command
    subparsers.add_parser("seed", help="Seed registry with existing WordPress blogs")

    # Status command
    subparsers.add_parser("status", help="Show pipeline status")

    args = parser.parse_args()

    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "poll":
        cmd_poll(args)
    elif args.command == "seed":
        cmd_seed(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
