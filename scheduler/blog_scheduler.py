"""DEPRECATED — Use blog-bot/generate_blog.py instead.

This file is retained for backward compatibility. The unified entry point is:

    poetry run python blog-bot/generate_blog.py                    # Daily scheduled run
    poetry run python blog-bot/generate_blog.py --topic "Custom"   # Custom topic
    poetry run python blog-bot/generate_blog.py --poll-analytics    # Poll engagement data

The launchd agent (com.friendlyconnections.blogbot) calls generate_blog.py directly.
All Q-learner, quality gate, and prompt versioning features are now integrated there.

Original description:
Blog scheduler — fires run_blog_workflow once daily at 12:00 PM local time.
"""

import os
import sys
import logging
import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from workflows.blog_workflow import BlogWorkflowInput, run_blog_workflow
from workflows.blog_evaluator import evaluate_draft
from workflows.narrative_examples import get_narrative_guidance
from learning.q_learner import QLearner
from learning.prompt_manager import PromptManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scheduler.log"),
    ]
)
logger = logging.getLogger("blog_scheduler")

# ── Blog topic queue ─────────────────────────────────────────────────────────
# Rotate through these topics one per day. Extend this list freely.
BLOG_TOPICS = [
    "How to build meaningful friendships as an adult",
    "The power of community support in mental wellness",
    "Simple ways to reconnect with your neighbors",
    "Why belonging matters for long-term happiness",
    "Overcoming loneliness through shared interests",
    "How social connections improve physical health",
    "Building a support network from scratch",
    "The science of genuine human connection",
    "Creating inclusive spaces in your community",
    "Small acts of kindness that strengthen communities",
]

CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
CLAUDE_SYSTEM_PROMPT = """You are a blog writer for Friendly Connections (human connection, community).
Write for engagement and retention. Return valid JSON only."""

# Clean Blog Writer prompt — separated from evaluation and image selection
BLOG_GENERATION_PROMPT = """Write a blog post using NARRATIVE STORYTELLING (NOT prescriptive advice).

Input: Topic: "{topic}"
Audience: Adults seeking genuine human connections (lonely, burned out, seeking community).
{prompt_knobs}
{learning_context}

CRITICAL: Use STORY-DRIVEN VOICE (like Vice, The Outline, personal essay):
- Open with a SPECIFIC MOMENT (person, place, feeling) — NOT a statistic
- Use narrative arc (tension → insight → path forward)
- Show through scenes/dialogue, don't just tell
- Avoid prescriptive tone: "you should", "it's important to", "simply", "just"
- Embrace: "Here's what happened", "Nobody talks about", "Turns out"

Examples of GOOD narrative hooks:
- "Sarah's therapist asked her to name three friends. She stared at the ceiling for 47 seconds."
- "The last time Mike had a genuine conversation was three weeks ago. He remembers because it made him uncomfortable."
- "Nobody warned Emma that making friends after 30 would feel like dating, except more awkward."

Sarcasm/Humor Rules (dry, not mean):
- ~20% increase: subtle, observational (NOT aggressive or cynical)
- Example: "Therapists call it 'social atrophy.' Your bank account calls it 'Friday night savings.'"
- Use dark humor that acknowledges pain (NOT toxic positivity)

Structure Rules:
1. Hook: Specific scene (50-100 words)
2. Tension: Show the cost/stakes through story
3. Insight: Psychological mechanism (HOW it works, not just WHAT)
4. Path: Actionable gesture (not bossy prescription)
5. Close: Question or observation (not "reach out" platitude)

Content Constraints:
- Dashes: Do NOT use em-dashes (—) except for numeric ranges
- NO listicles ("5 ways to...")
- NO generic pain points ("many people struggle")
- Cite mechanisms when making claims (avoid "studies show" without explanation)
- 800-1200 words, HTML: <p>, <h2>, <ul>, <li>

Category (REQUIRED): "Learning" or "Lifestyle"
Summary (REQUIRED): ONE sentence, clickbait-style, ending with "..."

Return ONLY valid JSON (properly escape all quotes/apostrophes inside strings):
{{
  "title": "Provocative/narrative title (50-100 chars, NOT how-to format)",
  "category": "Lifestyle",
  "summary": "One sentence ending with ...",
  "content": "Full HTML body (narrative structure, specific scenes) — use straight quotes only, avoid smart quotes/apostrophes",
  "tags": ["tag1", "tag2", "tag3"],
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}

CRITICAL: Use only straight ASCII quotes/apostrophes. Escape them in dialogue: She said \\"hello\\" not She said "hello"."""


def _get_todays_topic(q_learner: "QLearner" = None) -> str:
    """Pick today's topic, biased by contextual bandit if data exists."""
    if q_learner is not None:
        result = q_learner.select_topic_and_tone(BLOG_TOPICS)
        if result:
            return result[0]
    day_of_year = datetime.date.today().timetuple().tm_yday
    return BLOG_TOPICS[day_of_year % len(BLOG_TOPICS)]


def _extract_json(raw: str) -> dict:
    """Extract JSON from Claude's response, handling markdown fencing and common issues."""
    import json
    import re

    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    # First attempt: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt: find the outermost JSON object with regex
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Third attempt: fix common issues — unescaped quotes inside string values
    # Replace smart quotes with straight quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u2014', '-')  # em-dash to hyphen
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise


def _generate_blog_content(topic: str, q_learner: "QLearner" = None) -> dict:
    """Call Claude API to generate structured blog content.

    Retries up to 2 times on JSON parse failure (with temperature bump).
    """
    api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    learning_context = q_learner.get_learning_context_hint() if q_learner else ""
    prompt_knobs = q_learner.get_prompt_knobs_hint() if q_learner else ""
    active_prompt_template = PromptManager().get_active_prompt()
    prompt = active_prompt_template.format(
        topic=topic,
        learning_context=learning_context,
        prompt_knobs=prompt_knobs or "",
    )

    max_attempts = 3
    last_error = None

    for attempt in range(max_attempts):
        temp = 0 if attempt == 0 else 0.3
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            temperature=temp,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        try:
            data = _extract_json(raw)
            # Normalize: summary → excerpt for workflow
            if "summary" in data and "excerpt" not in data:
                data["excerpt"] = data["summary"]
            return data
        except Exception as e:
            last_error = e
            logger.warning("JSON parse failed (attempt %d/%d): %s", attempt + 1, max_attempts, e)

    raise last_error


def run_daily_blog_job() -> None:
    """Main job: generate content → evaluate → post draft (or reject)."""
    logger.info("=== Daily blog job starting ===")
    q_learner = QLearner()
    topic = _get_todays_topic(q_learner)
    logger.info("Topic: %s", topic)

    try:
        blog_data = _generate_blog_content(topic, q_learner)
        logger.info("Claude generated title: %s", blog_data.get("title"))
    except Exception as e:
        logger.error("Content generation failed: %s", e)
        return

    # Quality gate: evaluate before posting
    title = blog_data.get("title", topic)
    excerpt = blog_data.get("excerpt") or blog_data.get("summary", "")
    content = blog_data.get("content", "")
    eval_result = evaluate_draft(title=title, excerpt=excerpt, content=content)
    if not eval_result.overall_pass:
        logger.warning(
            "Quality gate REJECTED: title=%.1f spec=%.1f cred=%.1f engage=%.1f audience=%.1f platitudes=%d claims=%d — %s",
            eval_result.title_score, eval_result.specificity_score, eval_result.credibility_score,
            eval_result.engagement_score, eval_result.audience_score,
            len(eval_result.platitudes_detected or []), len(eval_result.unsubstantiated_claims or []),
            eval_result.feedback,
        )
        from workflows.visual_intent import classify_tone_and_intent
        tone_result = classify_tone_and_intent(title=title, excerpt=excerpt, content_preview=content[:500])
        q_learner.record_rejected(
            topic=topic,
            tone=tone_result.tone,
            keywords=blog_data.get("keywords", []),
            tags=blog_data.get("tags", []),
        )
        logger.info("=== Daily blog job complete (rejected) ===")
        return

    workflow_input = BlogWorkflowInput(
        title=title,
        content=content,
        excerpt=excerpt[:300],
        tags=blog_data.get("tags", []),
        keywords=blog_data.get("keywords", []),
    )

    result = run_blog_workflow(workflow_input)
    if result.success:
        logger.info("Blog draft created: post_id=%s slug=%s tone=%s", result.post_id, result.slug, result.tone)
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
    else:
        logger.error("Blog workflow failed at %s: %s",
                     result.error_stage, result.error_message)

    logger.info("=== Daily blog job complete ===")


def run_analytics_poll_job() -> None:
    """Poll WordPress for all pending horizon windows (6h / 24h / 72h).

    Also runs prompt candidate evaluation and (every 7 days) proposes
    a new candidate prompt if enough reward history exists.
    """
    logger.info("=== Analytics poll starting ===")
    q_learner = QLearner()
    recorded = q_learner.poll_and_update_wp_stats()
    logger.info("Bandit: recorded %d horizon snapshot(s)", recorded)

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


def main() -> None:
    import time
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    logger.info("Blog scheduler starting — daily blog at 12:00, analytics poll every 3h")
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_daily_blog_job,
        trigger=CronTrigger(hour=12, minute=0),
        id="daily_blog",
        name="Daily blog draft creation",
        misfire_grace_time=300,  # 5-minute window if machine was asleep
        replace_existing=True,
    )
    # Poll every 3 hours so 6h/24h/72h horizon windows are captured promptly.
    # The bandit learner is idempotent per horizon slot — safe to run frequently.
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(
        run_analytics_poll_job,
        trigger=IntervalTrigger(hours=3),
        id="analytics_poll",
        name="Multi-horizon analytics poll (every 3h)",
        misfire_grace_time=600,
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler running in background. Daily blog fires at 12:00.")

    print("\n" + "="*55)
    print("  LLM Relay Blog Scheduler")
    print("="*55)
    print("  Scheduled: daily blog at 12:00 PM, analytics every 3h")
    print()
    print("  Commands (type and press Enter):")
    print("    now          — generate & post blog right now")
    print("    now <topic>  — generate with a custom topic")
    print("    topics       — list all available topics")
    print("    status       — show next scheduled run times")
    print("    skip         — skip today's scheduled run")
    print("    quit / q     — stop scheduler and exit")
    print("="*55 + "\n")

    try:
        while True:
            try:
                cmd = input("scheduler> ").strip()
            except EOFError:
                # Non-interactive mode (e.g. tmux with no stdin)
                time.sleep(60)
                continue

            if not cmd:
                continue

            if cmd in ("quit", "q", "exit"):
                print("Stopping scheduler...")
                break

            elif cmd == "now":
                print("Running blog job now (today's topic)...")
                import threading
                threading.Thread(target=run_daily_blog_job, daemon=True).start()

            elif cmd.startswith("now "):
                custom_topic = cmd[4:].strip()
                if not custom_topic:
                    print("Usage: now <topic text>")
                    continue
                print(f"Running blog job now with topic: {custom_topic!r}")
                import threading

                def _run_custom(topic=custom_topic):
                    q_learner = QLearner()
                    try:
                        blog_data = _generate_blog_content(topic, q_learner)
                        logger.info("Claude generated title: %s", blog_data.get("title"))
                    except Exception as e:
                        logger.error("Content generation failed: %s", e)
                        return
                    title = blog_data.get("title", topic)
                    excerpt = blog_data.get("excerpt") or blog_data.get("summary", "")
                    content = blog_data.get("content", "")
                    eval_result = evaluate_draft(title=title, excerpt=excerpt, content=content)
                    if not eval_result.overall_pass:
                        logger.warning(
                            "Quality gate REJECTED: title=%.1f spec=%.1f cred=%.1f engage=%.1f audience=%.1f platitudes=%d claims=%d — %s",
                            eval_result.title_score, eval_result.specificity_score, eval_result.credibility_score,
                            eval_result.engagement_score, eval_result.audience_score,
                            len(eval_result.platitudes_detected or []), len(eval_result.unsubstantiated_claims or []),
                            eval_result.feedback,
                        )
                        from workflows.visual_intent import classify_tone_and_intent
                        tr = classify_tone_and_intent(title=title, excerpt=excerpt, content_preview=content[:500])
                        q_learner.record_rejected(topic=topic, tone=tr.tone, keywords=blog_data.get("keywords", []), tags=blog_data.get("tags", []))
                        return
                    workflow_input = BlogWorkflowInput(
                        title=title, content=content, excerpt=excerpt[:300],
                        tags=blog_data.get("tags", []), keywords=blog_data.get("keywords", []),
                    )
                    result = run_blog_workflow(workflow_input)
                    if result.success:
                        logger.info("Blog draft created: post_id=%s slug=%s tone=%s", result.post_id, result.slug, result.tone)
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
                    else:
                        logger.error("Blog workflow failed at %s: %s",
                                     result.error_stage, result.error_message)

                threading.Thread(target=_run_custom, daemon=True).start()

            elif cmd == "topics":
                print("\nAvailable topics:")
                for i, t in enumerate(BLOG_TOPICS, 1):
                    print(f"  {i:2}. {t}")
                print()

            elif cmd == "status":
                jobs = scheduler.get_jobs()
                print("\nScheduled jobs:")
                for job in jobs:
                    next_run = job.next_run_time
                    print(f"  {job.name}: next run at {next_run}")
                print()

            elif cmd == "skip":
                job = scheduler.get_job("daily_blog")
                if job:
                    job.pause()
                    print("Today's scheduled blog job paused. Run 'resume' to re-enable.")
                else:
                    print("No daily_blog job found.")

            elif cmd == "resume":
                job = scheduler.get_job("daily_blog")
                if job:
                    job.resume()
                    print("Daily blog job resumed.")
                else:
                    print("No daily_blog job found.")

            else:
                print(f"Unknown command: {cmd!r}. Type 'quit' to exit or 'now' to run immediately.")

    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown()
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
