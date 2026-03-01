"""On-demand blog post generator.

Generates and posts a WordPress draft immediately without waiting for the scheduler.

Usage:
    poetry run python scheduler/blog_now.py                    # use today's Q-guided topic
    poetry run python scheduler/blog_now.py --topic "..."      # use a custom topic
    poetry run python scheduler/blog_now.py --list             # list available topics
    poetry run python scheduler/blog_now.py --dry-run          # generate content only, no WordPress post
"""

import os
import sys
import argparse
import logging
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler.blog_scheduler import (
    BLOG_TOPICS,
    _get_todays_topic,
    _generate_blog_content,
)
from workflows.blog_workflow import BlogWorkflowInput, run_blog_workflow
from workflows.blog_evaluator import evaluate_draft
from workflows.visual_intent import classify_tone_and_intent
from learning.q_learner import QLearner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("blog_now")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a blog post on demand.")
    parser.add_argument(
        "--topic", "-t",
        type=str,
        default=None,
        help="Custom topic to write about (overrides Q-learner selection)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available topics and exit",
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Generate content and print it, but do NOT post to WordPress",
    )
    args = parser.parse_args()

    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    if args.list:
        print("\nAvailable topics:")
        for i, t in enumerate(BLOG_TOPICS, 1):
            print(f"  {i:2}. {t}")
        print()
        return

    q_learner = QLearner()

    if args.topic:
        topic = args.topic.strip()
        print(f"\nCustom topic: {topic!r}")
    else:
        topic = _get_todays_topic(q_learner)
        print(f"\nSelected topic: {topic!r}")

    print("Calling Claude to generate content...")
    try:
        blog_data = _generate_blog_content(topic, q_learner)
    except Exception as e:
        logger.error("Content generation failed: %s", e)
        sys.exit(1)

    title = blog_data.get("title", topic)
    excerpt = blog_data.get("excerpt") or blog_data.get("summary", "")
    content = blog_data.get("content", "")
    print(f"\nGenerated title: {title}")
    print(f"Tags:            {', '.join(blog_data.get('tags', []))}")
    print(f"Keywords:        {', '.join(blog_data.get('keywords', []))}")
    print(f"Summary:        {excerpt[:120]}...")

    if args.dry_run:
        print("\n--- DRY RUN: content preview (first 500 chars of HTML body) ---")
        print(content[:500])
        print("--- END DRY RUN (no WordPress post created) ---\n")
        return

    # Quality gate
    print("\nEvaluating draft...")
    eval_result = evaluate_draft(title=title, excerpt=excerpt, content=content)
    if not eval_result.overall_pass:
        print(f"\nQuality gate REJECTED")
        print(f"  Scores: title={eval_result.title_score:.1f} spec={eval_result.specificity_score:.1f} cred={eval_result.credibility_score:.1f} engage={eval_result.engagement_score:.1f} audience={eval_result.audience_score:.1f}")
        if eval_result.platitudes_detected:
            print(f"  Platitudes: {eval_result.platitudes_detected}")
        if eval_result.unsubstantiated_claims:
            print(f"  Unsubstantiated claims: {eval_result.unsubstantiated_claims}")
        print(f"  Feedback: {eval_result.feedback}")
        tone_result = classify_tone_and_intent(title=title, excerpt=excerpt, content_preview=content[:500])
        q_learner.record_rejected(topic=topic, tone=tone_result.tone, keywords=blog_data.get("keywords", []), tags=blog_data.get("tags", []))
        sys.exit(1)

    print("\nPosting to WordPress...")
    logger.info("Starting blog workflow: title=%r", title)
    workflow_input = BlogWorkflowInput(
        title=title,
        content=content,
        excerpt=excerpt[:300],
        tags=blog_data.get("tags", []),
        keywords=blog_data.get("keywords", []),
    )

    result = run_blog_workflow(workflow_input)
    if result.success:
        print(f"\nDraft created successfully!")
        print(f"  Post ID : {result.post_id}")
        print(f"  Slug    : {result.slug}")
        print(f"  Link    : {result.post_link or '(set after publish)'}")
        print(f"  Image ID: {result.featured_media_id or '(no featured image)'}")
        logger.info(
            "Workflow success: post_id=%s slug=%s featured_media_id=%s",
            result.post_id,
            result.slug,
            result.featured_media_id,
        )
        q_learner.record_draft_created(
            topic=topic,
            post_id=result.post_id,
            slug=result.slug,
            keywords=blog_data.get("keywords", []),
            tags=blog_data.get("tags", []),
            tone=result.tone,
            title=title,
            image_id=result.image_id,
            image_description=result.image_description,
        )
        print("\nDraft is in WordPress — review and publish when ready.")
    else:
        print(f"\nWorkflow failed at stage: {result.error_stage}")
        print(f"Reason: {result.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
