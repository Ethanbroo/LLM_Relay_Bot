"""Unified blog generation script — single entry point for all blog creation.

Called by macOS launchd (Launch Agent) on schedule, or manually.
Runs a single blog generation and exits — no persistent process needed.

Integrates:
  - Full pipeline (topic dedup, title scoring, sub-context, FC angle, validation)
  - Q-learner bandit (topic/tone selection, draft recording, rejection signals)
  - LLM quality gate (pre-posting evaluation)
  - Prompt versioning (A/B candidate testing)
  - AI image generation (fal.ai Flux with Unsplash fallback)
  - WordPress draft posting + email notification
  - Analytics polling (run with --poll-analytics flag)

Usage:
    poetry run python blog-bot/generate_blog.py                    # Daily scheduled run
    poetry run python blog-bot/generate_blog.py --topic "Custom"   # Custom topic
    poetry run python blog-bot/generate_blog.py --dry-run           # Generate without posting
    poetry run python blog-bot/generate_blog.py --poll-analytics    # Poll engagement data only
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root and blog-bot are on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import markdown

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "blog_generation.log"),
    ],
)
logger = logging.getLogger("blog-bot-generate")

# Topic pool — rotated daily, with Q-learner override when data exists
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

FC_ANGLE_POOL = [
    # ── convenience (3) ──────────────────────────────────────────────────
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
            "Mention Friendly Connections as an option for people who want "
            "ready-made social plans without the mental load of organizing "
            "everything themselves."
        ),
        "type": "convenience",
    },
    {
        "angle": (
            "Reference Friendly Connections when discussing how exhausting it is "
            "to coordinate adult hangouts, noting it as a service that removes "
            "the scheduling friction."
        ),
        "type": "convenience",
    },
    # ── problem_solution (3) ─────────────────────────────────────────────
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
            "Mention Friendly Connections when discussing Ontario's unique social "
            "landscape, noting it as a local service designed for people navigating "
            "the specific challenges of building friendships in the region."
        ),
        "type": "problem_solution",
    },
    {
        "angle": (
            "Reference Friendly Connections as a practical answer to the question "
            "'where do I even meet people?' that comes up when adults move to a "
            "new city or outgrow their college friend group."
        ),
        "type": "problem_solution",
    },
    # ── trend (2) ────────────────────────────────────────────────────────
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
            "Mention Friendly Connections when discussing how some people are "
            "turning to organized social services as a practical response to the "
            "friendship recession, framing it as part of a growing movement "
            "rather than a last resort."
        ),
        "type": "trend",
    },
    # ── comparison (2) ───────────────────────────────────────────────────
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
            "Reference Friendly Connections when comparing different ways adults "
            "try to make friends — apps, meetups, classes, coworking — noting it "
            "as the curated, low-pressure alternative."
        ),
        "type": "comparison",
    },
    # ── explanation (2) ──────────────────────────────────────────────────
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
            "When explaining the psychology of why structured social environments "
            "work better than unstructured ones, mention Friendly Connections "
            "as an example of this principle in practice."
        ),
        "type": "explanation",
    },
    # ── cultural_shift (2) ───────────────────────────────────────────────
    {
        "angle": (
            "Position Friendly Connections within the broader cultural shift "
            "of people treating friendship as something worth actively investing "
            "in, rather than expecting it to happen passively."
        ),
        "type": "cultural_shift",
    },
    {
        "angle": (
            "Mention Friendly Connections as part of a wider trend where "
            "services that used to feel niche — paying for social connection — "
            "are becoming normalized alongside therapy, coaching, and self-care."
        ),
        "type": "cultural_shift",
    },
    # ── explanation (bonus) ──────────────────────────────────────────────
    {
        "angle": (
            "Reference Friendly Connections when discussing how third places "
            "— cafes, gyms, community centres — are disappearing, noting it "
            "as a modern substitute for the spontaneous social encounters that "
            "spaces like these once provided."
        ),
        "type": "explanation",
    },
]


def select_topic(q_learner=None) -> tuple[str, str | None]:
    """Select today's topic. Uses Q-learner bandit if data exists, else day rotation.

    Returns (topic, tone) where tone may be None if bandit was not used.
    """
    if q_learner is not None:
        result = q_learner.select_topic_and_tone(BLOG_TOPICS)
        if result:
            logger.info("Q-learner selected topic=%r tone=%s", result[0][:50], result[1])
            return result[0], result[1]

    now = datetime.now(timezone.utc)
    day_of_year = now.timetuple().tm_yday
    return BLOG_TOPICS[day_of_year % len(BLOG_TOPICS)], None


def select_fc_angle() -> dict:
    """Select an FC angle, avoiding the same type as the last logged angle."""
    import json

    # Read last logged angle type
    fc_angles_path = Path(__file__).parent / "config" / "fc_angles.json"
    last_type = None
    if fc_angles_path.exists():
        try:
            data = json.loads(fc_angles_path.read_text())
            angles = data.get("angles", [])
            if angles:
                last_type = angles[-1].get("type")
        except Exception:
            pass

    # Filter pool to exclude last used type (for diversity)
    candidates = [a for a in FC_ANGLE_POOL if a["type"] != last_type]
    if not candidates:
        candidates = FC_ANGLE_POOL  # fallback if all same type

    # Rotate through filtered candidates by day
    now = datetime.now(timezone.utc)
    day_of_year = now.timetuple().tm_yday
    return candidates[day_of_year % len(candidates)]


def post_to_wordpress(
    title: str,
    content_md: str,
    image_data_b64: str | None = None,
    image_mime_type: str | None = None,
) -> "BlogDraftResult":
    """Convert markdown content to HTML and post to WordPress as a draft."""
    from workflows.blog_workflow import run_blog_workflow, BlogWorkflowInput

    # Convert markdown to HTML for WordPress
    html_content = markdown.markdown(
        content_md,
        extensions=["extra", "smarty"],
    )

    # Build a short plain-text excerpt (first ~280 chars)
    import re
    plain = re.sub(r"[#*_\[\]\(\)>]", "", content_md)
    plain = re.sub(r"\s+", " ", plain).strip()
    excerpt = plain[:280].rsplit(" ", 1)[0] + "..." if len(plain) > 280 else plain

    inp = BlogWorkflowInput(
        title=title,
        content=html_content,
        excerpt=excerpt,
        tags=["friendship", "loneliness", "social connection", "Ontario"],
        keywords=[],
        image_data_b64=image_data_b64,
        image_mime_type=image_mime_type,
    )

    logger.info("Posting to WordPress: title=%r", title[:60])
    wp_result = run_blog_workflow(inp)

    if wp_result.success:
        logger.info(
            "WordPress draft created: post_id=%s slug=%s link=%s",
            wp_result.post_id, wp_result.slug, wp_result.post_link,
        )
    else:
        logger.error(
            "WordPress posting failed: stage=%s error=%s",
            wp_result.error_stage, wp_result.error_message,
        )

    return wp_result


def _store_image_metadata(post_id: int, image_metadata: dict) -> None:
    """Store AI image metadata in blog_registry.json for feedback tracking."""
    import json

    registry_path = Path(__file__).parent / "config" / "blog_registry.json"
    if not registry_path.exists():
        logger.warning("blog_registry.json not found — cannot store image metadata")
        return

    registry = json.loads(registry_path.read_text())
    for blog in registry.get("blogs", []):
        if blog.get("id") == post_id:
            blog["image"] = image_metadata
            registry_path.write_text(json.dumps(registry, indent=2))
            logger.info("Stored image metadata for post %d", post_id)
            return

    logger.info("Post %d not yet in registry — image metadata will be attached later", post_id)


# ── Quality Gate (from blog_evaluator) ───────────────────────────────────

def _run_quality_gate(title: str, content: str, excerpt: str) -> bool:
    """Run the LLM quality gate. Returns True if the draft passes.

    On failure, logs details. On crash, defaults to PASS (let programmatic
    validation be the backstop).
    """
    try:
        from workflows.blog_evaluator import evaluate_draft
        eval_result = evaluate_draft(title=title, excerpt=excerpt, content=content)

        if not eval_result.overall_pass:
            logger.warning(
                "Quality gate REJECTED: title=%.1f spec=%.1f cred=%.1f "
                "engage=%.1f audience=%.1f platitudes=%d claims=%d — %s",
                eval_result.title_score, eval_result.specificity_score,
                eval_result.credibility_score, eval_result.engagement_score,
                eval_result.audience_score,
                len(eval_result.platitudes_detected or []),
                len(eval_result.unsubstantiated_claims or []),
                eval_result.feedback,
            )
            return False

        logger.info(
            "Quality gate PASSED: title=%.1f spec=%.1f cred=%.1f engage=%.1f audience=%.1f",
            eval_result.title_score, eval_result.specificity_score,
            eval_result.credibility_score, eval_result.engagement_score,
            eval_result.audience_score,
        )
        return True
    except Exception as e:
        logger.warning("Quality gate crashed: %s — defaulting to PASS", e)
        return True


# ── Analytics Polling ────────────────────────────────────────────────────

def run_analytics_poll():
    """Poll WordPress for engagement data and update Q-learner rewards.

    Also:
    - Syncs GA4 + Search Console data into blog_registry.json (if configured)
    - Syncs demographic data for sub-context targeting (if configured)
    - Evaluates prompt candidates and proposes new tweaks
    Can be called standalone via --poll-analytics flag.
    """
    logger.info("=== Analytics poll starting ===")

    try:
        from learning.q_learner import QLearner
        from learning.prompt_manager import PromptManager

        q_learner = QLearner()
        recorded = q_learner.poll_and_update_wp_stats()
        logger.info("Bandit: recorded %d horizon snapshot(s)", recorded)

        # ── GA4 + Search Console sync (enriches blog_registry.json) ──────
        try:
            from src.analytics_sync import sync, sync_demographics, sync_from_wordpress

            # Always sync WordPress stats into registry (baseline)
            wp_sync = sync_from_wordpress(force=False)
            if wp_sync.get("synced"):
                logger.info(
                    "Registry sync (WordPress): %d/%d blogs updated",
                    wp_sync.get("blogs_updated", 0),
                    wp_sync.get("blogs_total", 0),
                )

            # If GA4 is configured, also sync richer analytics
            ga4_property = os.environ.get("GOOGLE_ANALYTICS_PROPERTY_ID", "")
            if ga4_property:
                ga4_sync = sync(force=False)
                if ga4_sync.get("synced"):
                    logger.info(
                        "Registry sync (GA4): %d/%d blogs updated, %d alert(s)",
                        ga4_sync.get("blogs_updated", 0),
                        ga4_sync.get("blogs_total", 0),
                        len(ga4_sync.get("alerts", [])),
                    )

                # Sync demographics for sub-context targeting
                demo_sync = sync_demographics()
                if demo_sync.get("synced"):
                    logger.info("Demographic sync: updated audience profile")
            else:
                logger.info("GA4 not configured — using WordPress stats only")
        except Exception as sync_err:
            logger.warning("Analytics registry sync failed: %s — Q-learner unaffected", sync_err)

        # Prompt versioning: evaluate any running candidate, then maybe propose next
        reward_history = q_learner._state.get("reward_history", [])
        keyword_scores = q_learner._state.get("keyword_scores", {})
        pm = PromptManager()

        result = pm.maybe_evaluate_candidate(reward_history)
        if result is True:
            logger.info("PromptManager: candidate PROMOTED to new stable version")
        elif result is False:
            logger.info("PromptManager: candidate ROLLED BACK — stable prompt restored")

        proposed = pm.maybe_propose_tweak(reward_history, keyword_scores)
        if proposed:
            logger.info("PromptManager: new candidate prompt activated for 7-day trial")

        logger.info("=== Analytics poll complete ===")
        return recorded

    except Exception as e:
        logger.exception("Analytics poll crashed: %s", e)
        return 0


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a single blog post")
    parser.add_argument("--topic", "-t", type=str, default=None, help="Custom topic")
    parser.add_argument("--fc-angle", type=str, default=None, help="Custom FC angle")
    parser.add_argument("--fc-angle-type", type=str, default="other", help="FC angle type")
    parser.add_argument("--now", action="store_true", help="On-demand generation (for CLI clarity)")
    parser.add_argument("--dry-run", action="store_true", help="Generate blog but don't post to WordPress")
    parser.add_argument("--skip-quality-gate", action="store_true", help="Skip LLM quality evaluation")
    parser.add_argument("--poll-analytics", action="store_true", help="Only poll analytics (no blog generation)")
    args = parser.parse_args()

    # ── Analytics-only mode ──────────────────────────────────────────────
    if args.poll_analytics:
        run_analytics_poll()
        return

    logger.info("=== Blog generation starting ===")

    # ── Initialize Q-learner ─────────────────────────────────────────────
    q_learner = None
    try:
        from learning.q_learner import QLearner
        q_learner = QLearner()
        logger.info("Q-learner initialized (bandit learning active)")
    except Exception as e:
        logger.warning("Q-learner unavailable: %s — proceeding without bandit", e)

    try:
        from src.pipeline import PipelineConfig, run, startup_checks

        checks = startup_checks()
        logger.info(
            "Startup: embedding=%s fc_phase=%d consolidations=%d",
            checks["embedding_model"],
            checks["fc_phase"],
            checks["pending_consolidations"],
        )

        # ── Topic selection (Q-learner biased) ───────────────────────────
        if args.topic:
            topic = args.topic
            bandit_tone = None
        else:
            topic, bandit_tone = select_topic(q_learner)
        logger.info("Topic: %s", topic)

        # ── FC angle selection ───────────────────────────────────────────
        if args.fc_angle:
            fc_angle = args.fc_angle
            fc_type = args.fc_angle_type
        else:
            fc_data = select_fc_angle()
            fc_angle = fc_data["angle"]
            fc_type = fc_data["type"]

        logger.info("FC angle [%s]: %s", fc_type, fc_angle[:80])

        config = PipelineConfig(
            topic=topic,
            word_count=1500,
            fc_angle=fc_angle,
            fc_angle_type=fc_type,
            dry_run=args.dry_run,
        )

        # ── Run pipeline ─────────────────────────────────────────────────
        result = run(config)

        if result.success:
            logger.info(
                "Pipeline SUCCESS: title='%s' model=%s attempts=%d",
                result.title, result.model_used, result.attempt_count,
            )

            # ── Quality gate (LLM evaluation) ────────────────────────────
            if not args.skip_quality_gate and not args.dry_run:
                import re as _re
                plain_text = _re.sub(r"[#*_\[\]\(\)>]", "", result.content)
                plain_text = _re.sub(r"\s+", " ", plain_text).strip()
                excerpt_for_eval = plain_text[:280].rsplit(" ", 1)[0] + "..." if len(plain_text) > 280 else plain_text

                if not _run_quality_gate(result.title, result.content, excerpt_for_eval):
                    # Record rejection in Q-learner
                    if q_learner:
                        try:
                            from workflows.visual_intent import classify_tone_and_intent
                            tone_result = classify_tone_and_intent(
                                title=result.title, excerpt=excerpt_for_eval,
                                content_preview=result.content[:500],
                            )
                            q_learner.record_rejected(
                                topic=topic,
                                tone=tone_result.tone,
                                keywords=[], tags=[],
                            )
                            logger.info("Q-learner: recorded rejection (tone=%s)", tone_result.tone)
                        except Exception as ql_err:
                            logger.warning("Q-learner rejection recording failed: %s", ql_err)

                    print(f"\n{'='*60}")
                    print(f"  BLOG REJECTED BY QUALITY GATE")
                    print(f"  Title: {result.title}")
                    print(f"  See logs for details.")
                    print(f"{'='*60}\n")
                    sys.exit(1)

            print(f"\n{'='*60}")
            print(f"  BLOG GENERATED SUCCESSFULLY")
            print(f"  Title: {result.title}")
            print(f"  Model: {result.model_used}")
            print(f"  Output: {result.output_path}")
            print(f"{'='*60}\n")

            # Post to WordPress as a draft + cover image + email notification
            if args.dry_run:
                print(f"  DRY RUN: Skipping WordPress posting.")
                print(f"{'='*60}\n")
                return

            # Generate AI cover image (falls back to Unsplash if this fails)
            image_data_b64 = None
            image_mime_type = None
            image_metadata = None
            try:
                from src.image_generator import generate_blog_image

                import re as _re
                plain_text = _re.sub(r"[#*_\[\]\(\)>]", "", result.content)
                plain_text = _re.sub(r"\s+", " ", plain_text).strip()
                opening = plain_text[:500]
                excerpt_for_img = plain_text[:280].rsplit(" ", 1)[0] + "..." if len(plain_text) > 280 else plain_text

                image_result = generate_blog_image(result.title, excerpt_for_img, opening)
                if image_result.success:
                    image_data_b64 = image_result.image_bytes_b64
                    image_mime_type = image_result.mime_type
                    image_metadata = {
                        "generation_prompt": image_result.prompt,
                        "attributes": image_result.attributes,
                        "candidate_scores": image_result.candidate_scores,
                        "selected_candidate": image_result.selected_index,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "feedback": None,
                    }
                    logger.info(
                        "AI image generated: style=%s subject=%s",
                        image_result.attributes.get("style"),
                        image_result.attributes.get("subject"),
                    )
                    print(f"  AI cover image generated (style={image_result.attributes.get('style')})")
                else:
                    logger.warning("AI image generation failed: %s — falling back to Unsplash", image_result.error)
                    print(f"  AI image generation failed, using Unsplash fallback")
            except Exception as img_err:
                logger.warning("AI image generation crashed: %s — falling back to Unsplash", img_err)
                print(f"  AI image generation crashed, using Unsplash fallback")

            try:
                wp_result = post_to_wordpress(
                    result.title,
                    result.content,
                    image_data_b64=image_data_b64,
                    image_mime_type=image_mime_type,
                )
                if wp_result.success:
                    print(f"  WORDPRESS DRAFT CREATED")
                    print(f"  Post ID: {wp_result.post_id}")
                    print(f"  Link: {wp_result.post_link}")
                    if wp_result.featured_media_url:
                        print(f"  Cover image: {wp_result.featured_media_url}")
                    print(f"{'='*60}\n")

                    # ── Record draft in Q-learner ────────────────────────
                    if q_learner and wp_result.post_id:
                        try:
                            q_learner.record_draft_created(
                                topic=topic,
                                post_id=wp_result.post_id,
                                slug=wp_result.slug,
                                keywords=[],
                                tags=["friendship", "loneliness", "social connection", "Ontario"],
                                tone=wp_result.tone or bandit_tone,
                                title=result.title,
                                image_id=wp_result.image_id,
                                image_description=wp_result.image_description,
                            )
                            logger.info(
                                "Q-learner: recorded draft post_id=%s tone=%s",
                                wp_result.post_id, wp_result.tone or bandit_tone,
                            )
                        except Exception as ql_err:
                            logger.warning("Q-learner draft recording failed: %s", ql_err)

                    # Store image metadata in blog registry for feedback tracking
                    if image_metadata and wp_result.featured_media_id:
                        try:
                            image_metadata["draft_media_id"] = wp_result.featured_media_id
                            _store_image_metadata(
                                post_id=int(wp_result.post_id),
                                image_metadata=image_metadata,
                            )
                        except Exception as meta_err:
                            logger.warning("Failed to store image metadata: %s", meta_err)
                else:
                    logger.error(
                        "WordPress posting failed: %s — %s",
                        wp_result.error_stage, wp_result.error_message,
                    )
                    print(f"  WARNING: Blog generated but WordPress posting failed.")
                    print(f"  Stage: {wp_result.error_stage}")
                    print(f"  Error: {wp_result.error_message}")
                    print(f"{'='*60}\n")
            except Exception as wp_err:
                logger.exception("WordPress posting crashed: %s", wp_err)
                print(f"  WARNING: Blog generated but WordPress posting crashed: {wp_err}")
                print(f"{'='*60}\n")
        else:
            logger.error("FAILED: %s", result.error)
            print(f"\n{'='*60}")
            print(f"  BLOG GENERATION FAILED")
            print(f"  Error: {result.error}")
            print(f"{'='*60}\n")
            sys.exit(1)

    except Exception as e:
        logger.exception("Blog generation crashed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
