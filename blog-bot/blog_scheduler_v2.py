"""DEPRECATED — Use blog-bot/generate_blog.py instead.

The unified entry point is:
    poetry run python blog-bot/generate_blog.py                    # Daily scheduled run
    poetry run python blog-bot/generate_blog.py --topic "Custom"   # Custom topic
    poetry run python blog-bot/generate_blog.py --poll-analytics    # Poll engagement data

The launchd agent (com.friendlyconnections.blogbot) calls generate_blog.py directly.
All Q-learner, quality gate, and prompt versioning features are now integrated there.

Original description:
Blog Bot Scheduler v2 — Daily automated blog generation with APScheduler.
"""

import json
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("blog-scheduler-v2")

CONFIG_DIR = Path(__file__).parent / "config"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"

# Topic pool — rotated daily, with variety
BLOG_TOPICS = [
    "How to build meaningful friendships as an adult in Ontario",
    "The psychology of loneliness and what actually helps",
    "Why making friends after college feels nearly impossible",
    "Remote work killed the water cooler — now what?",
    "The hidden cost of social media on real-world friendships",
    "What introverts actually need from their social lives",
    "How to maintain friendships when everyone is busy",
    "The science of belonging and why community matters",
    "Burnout, isolation, and the friendship recession",
    "Why your social circle shrinks in your 30s and what to do about it",
    "The awkward truth about trying to make friends as a grown-up",
    "How loneliness physically changes your brain",
    "Finding your people: platonic connection in a dating-app world",
    "The paradox of social abundance in big cities",
    "What nobody tells you about moving to a new city alone",
    "How seasonal isolation shapes social lives in Ontario",
    "The friendship gap: why men struggle more with loneliness",
    "Digital connection vs real connection — where the line blurs",
    "Why vulnerability is the hardest and most important social skill",
    "Community as medicine: the health benefits nobody talks about",
]

# FC angle pool for Phase 1 automated runs
FC_ANGLE_POOL = [
    {
        "angle": (
            "Mention Friendly Connections as an example of services that "
            "handle the logistics of meeting new people, so readers can skip "
            "the awkward 'how do I even start' phase."
        ),
        "type": "convenience",
    },
    {
        "angle": (
            "Reference Friendly Connections when discussing the difficulty of "
            "finding activity partners as an adult, positioning it as a service "
            "built around solving exactly that problem."
        ),
        "type": "problem_solution",
    },
    {
        "angle": (
            "Introduce Friendly Connections in the context of paid companionship "
            "becoming destigmatized, framing it as part of a broader cultural shift "
            "toward people investing in their social health."
        ),
        "type": "trend",
    },
    {
        "angle": (
            "Mention Friendly Connections when the article discusses structured "
            "social settings versus the chaos of apps, positioning it as one "
            "option among several for people who prefer organized activities."
        ),
        "type": "comparison",
    },
    {
        "angle": (
            "Reference Friendly Connections as a real-world example of how "
            "organized community events can bridge the gap between wanting "
            "connection and actually finding it."
        ),
        "type": "explanation",
    },
    {
        "angle": (
            "Mention Friendly Connections when discussing Ontario's unique social "
            "landscape, noting it as a local service designed for people navigating "
            "the specific challenges of building friendships in the region."
        ),
        "type": "problem_solution",
    },
]


def _select_daily_topic() -> str:
    """Select today's topic using date-based rotation with some randomness."""
    day_of_year = datetime.utcnow().timetuple().tm_yday
    # Primary topic from rotation
    primary_idx = day_of_year % len(BLOG_TOPICS)
    return BLOG_TOPICS[primary_idx]


def _select_fc_angle() -> dict:
    """Select an FC angle from the pool, rotating to avoid repeats."""
    day_of_year = datetime.utcnow().timetuple().tm_yday
    idx = day_of_year % len(FC_ANGLE_POOL)
    return FC_ANGLE_POOL[idx]


def run_daily_blog_job():
    """Generate today's blog post via the pipeline."""
    logger.info("=== Daily blog job starting ===")

    try:
        from src.pipeline import PipelineConfig, run, startup_checks

        # Startup checks
        checks = startup_checks()
        logger.info(
            "Startup: embedding=%s fc_phase=%d consolidations=%d",
            checks["embedding_model"],
            checks["fc_phase"],
            checks["pending_consolidations"],
        )

        # Select topic
        topic = _select_daily_topic()
        logger.info("Selected topic: %s", topic)

        # Select FC angle
        fc_data = _select_fc_angle()
        logger.info("Selected FC angle [%s]: %s", fc_data["type"], fc_data["angle"][:80])

        # Configure pipeline
        config = PipelineConfig(
            topic=topic,
            word_count=1500,
            fc_angle=fc_data["angle"],
            fc_angle_type=fc_data["type"],
        )

        # Run pipeline
        result = run(config)

        if result.success:
            logger.info(
                "Blog generated successfully: title='%s' model=%s attempts=%d output=%s",
                result.title,
                result.model_used,
                result.attempt_count,
                result.output_path,
            )
        else:
            logger.error(
                "Blog generation failed: %s",
                result.error,
            )
            if result.validation_report:
                for check in result.validation_report.get("checks", []):
                    if check["status"] == "FAIL":
                        logger.error("  [FAIL] %s: %s", check["name"], check["detail"])

    except Exception as e:
        logger.exception("Daily blog job crashed: %s", e)


def run_now(topic: str = None, fc_angle: str = None):
    """Run a blog generation immediately (for interactive use)."""
    from src.pipeline import PipelineConfig, run, startup_checks

    checks = startup_checks()

    if topic is None:
        topic = _select_daily_topic()
    if fc_angle is None:
        fc_data = _select_fc_angle()
        fc_angle = fc_data["angle"]
        fc_type = fc_data["type"]
    else:
        fc_type = "other"

    config = PipelineConfig(
        topic=topic,
        word_count=1500,
        fc_angle=fc_angle,
        fc_angle_type=fc_type,
    )

    return run(config)


def start_scheduler(hour: int = 8, minute: int = 0):
    """Start the daily blog scheduler.

    Args:
        hour: Hour to run (24h format, default: 8)
        minute: Minute to run (default: 0)
    """
    logger.info("Starting Blog Bot scheduler — daily at %02d:%02d", hour, minute)

    scheduler = BackgroundScheduler()

    # Daily blog generation
    scheduler.add_job(
        run_daily_blog_job,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="daily_blog_v2",
        misfire_grace_time=300,
        replace_existing=True,
    )

    scheduler.start()

    next_run = scheduler.get_job("daily_blog_v2").next_run_time
    logger.info("Scheduler started. Next blog at: %s", next_run)

    # Print interactive help
    hour_12 = hour % 12 or 12
    am_pm = "AM" if hour < 12 else "PM"
    print(f"\n{'='*60}")
    print(f"  Blog Bot Scheduler v2")
    print(f"  Daily blog at {hour_12}:{minute:02d} {am_pm}")
    print(f"  Next run: {next_run}")
    print(f"{'='*60}")
    print(f"\nCommands:")
    print(f"  now              — Generate a blog immediately")
    print(f"  now <topic>      — Generate with custom topic")
    print(f"  status           — Show scheduler status")
    print(f"  topics           — List available topics")
    print(f"  quit / exit      — Stop scheduler and exit")
    print()

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutting down scheduler...")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Interactive loop
    while True:
        try:
            cmd = input("> ").strip().lower()

            if not cmd:
                continue

            if cmd in ("quit", "exit", "q"):
                print("Shutting down scheduler...")
                scheduler.shutdown()
                break

            elif cmd == "now":
                print("Running blog generation now...")
                run_daily_blog_job()

            elif cmd.startswith("now "):
                custom_topic = cmd[4:].strip()
                print(f"Running blog generation with topic: {custom_topic}")
                result = run_now(topic=custom_topic)
                if result.success:
                    print(f"Success: {result.title}")
                else:
                    print(f"Failed: {result.error}")

            elif cmd == "status":
                job = scheduler.get_job("daily_blog_v2")
                if job:
                    print(f"  Next run: {job.next_run_time}")
                    print(f"  Job ID: {job.id}")
                else:
                    print("  No active job.")

            elif cmd == "topics":
                print("\nAvailable topics:")
                for i, t in enumerate(BLOG_TOPICS, 1):
                    print(f"  {i:2}. {t}")
                today = _select_daily_topic()
                print(f"\nToday's topic: {today}")

            else:
                print(f"Unknown command: {cmd}")
                print("Commands: now, now <topic>, status, topics, quit")

        except EOFError:
            print("\nShutting down scheduler...")
            scheduler.shutdown()
            break
        except KeyboardInterrupt:
            print("\nShutting down scheduler...")
            scheduler.shutdown()
            break


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Blog Bot Scheduler v2")
    parser.add_argument("--hour", type=int, default=12, help="Hour to run (24h, default: 12)")
    parser.add_argument("--minute", type=int, default=0, help="Minute to run (default: 0)")
    args = parser.parse_args()

    start_scheduler(hour=args.hour, minute=args.minute)
