"""Pipeline Orchestrator — Main Workflow.

Ties all components together into the blog generation pipeline.

Workflow:
0. Startup checks (model loading, sync checks, phase status)
1. Topic deduplication
2. Title pre-scoring
3. Sub-context selection
4. FC integration angle
5. Assemble per-blog block
6. Assemble full API call
7. Call LLM API
8. Validate output
9. Output (save, update registry, present for review)
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
TEMPLATES_DIR = BASE_DIR / "templates"
EXAMPLES_DIR = BASE_DIR / "examples"
OUTPUTS_DIR = BASE_DIR / "outputs"
REGISTRY_PATH = CONFIG_DIR / "blog_registry.json"
SYSTEM_PROMPT_PATH = CONFIG_DIR / "system_prompt.txt"
PER_BLOG_TEMPLATE_PATH = TEMPLATES_DIR / "per_blog_template.txt"
MODEL_CALIBRATION_PATH = CONFIG_DIR / "model_calibration.json"
EXAMPLES_PATH = EXAMPLES_DIR / "few_shot_examples.md"

# Q-learner state file (read-only — we consume its learned signals)
PROJECT_ROOT = BASE_DIR.parent
Q_STATE_PATH = PROJECT_ROOT / "data" / "q_state.json"

# Import components
from . import topic_dedup
from . import title_scorer
from . import sub_context_rotator
from . import fc_angle_manager
from . import validator
from . import blog_list_manager
from . import few_shot_refresher
from . import analytics_sync
from . import duplicate_consolidator
from . import blog_generator
from . import demographic_targeting


class PipelineConfig:
    """Configuration for a single pipeline run."""

    def __init__(
        self,
        topic: str,
        word_count: int = 1500,
        fc_angle: Optional[str] = None,
        fc_angle_type: str = "other",
        override_dedup: bool = False,
        skip_title_scoring: bool = False,
        title_override: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.topic = topic
        self.word_count = word_count
        self.min_word_count = int(word_count * 0.95)
        self.max_word_count = int(word_count * 1.10)
        self.fc_angle = fc_angle
        self.fc_angle_type = fc_angle_type
        self.override_dedup = override_dedup
        self.skip_title_scoring = skip_title_scoring
        self.title_override = title_override
        self.dry_run = dry_run


class PipelineResult:
    """Result of a pipeline run."""

    def __init__(self):
        self.success: bool = False
        self.title: str = ""
        self.content: str = ""
        self.metadata_block: str = ""
        self.validation_report: Optional[dict] = None
        self.output_path: Optional[str] = None
        self.validation_path: Optional[str] = None
        self.model_used: str = ""
        self.attempt_count: int = 0
        self.all_attempts: list[dict] = []
        self.error: Optional[str] = None
        self.dedup_result: Optional[dict] = None
        self.title_scoring_result: Optional[dict] = None
        self.sub_context: Optional[dict] = None
        self.fc_angle_result: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "title": self.title,
            "model_used": self.model_used,
            "attempt_count": self.attempt_count,
            "output_path": self.output_path,
            "validation_path": self.validation_path,
            "validation_report": self.validation_report,
            "error": self.error,
        }


# Shared embedding model instance
_embedding_model = None


def _load_embedding_model():
    """Load the shared embedding model (used by topic_dedup and title_scorer)."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    try:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded shared embedding model: all-MiniLM-L6-v2")

        # Share with components
        topic_dedup.set_embedding_model(_embedding_model)
        title_scorer.set_embedding_model(_embedding_model)

        return _embedding_model
    except ImportError:
        logger.warning(
            "sentence-transformers not installed — topic dedup and title "
            "uniqueness scoring will be unavailable. "
            "Install with: pip install sentence-transformers"
        )
        return None


def _load_system_prompt() -> str:
    """Load the static system prompt."""
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"System prompt not found at {SYSTEM_PROMPT_PATH}. "
            f"Create it before running the pipeline."
        )
    return SYSTEM_PROMPT_PATH.read_text().strip()


def _load_per_blog_template() -> str:
    """Load the per-blog instruction template."""
    if not PER_BLOG_TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Per-blog template not found at {PER_BLOG_TEMPLATE_PATH}. "
            f"Create it before running the pipeline."
        )
    return PER_BLOG_TEMPLATE_PATH.read_text().strip()


def _load_model_calibration() -> dict:
    """Load model calibration config."""
    if not MODEL_CALIBRATION_PATH.exists():
        logger.warning("Model calibration file not found — using defaults.")
        return {
            "models": {
                "claude-sonnet-4-20250514": {
                    "provider": "anthropic",
                    "role": "primary",
                    "append_to_system_prompt": "",
                    "recommended_temperature": 0.7,
                    "recommended_max_tokens": 4096,
                }
            },
            "fallback_order": ["claude-sonnet-4-20250514"],
        }
    return json.loads(MODEL_CALIBRATION_PATH.read_text())


def _load_bandit_signals() -> str:
    """Load learned signals from the q_learner's state file.

    Reads data/q_state.json (written by the scheduler's bandit) and extracts:
    - Top-performing keywords and tags (EMA scores)
    - Prompt knobs (sarcasm, painpoint intensity, audience specificity)

    Returns a text block to append to the system prompt, or empty string.
    """
    if not Q_STATE_PATH.exists():
        return ""

    try:
        state = json.loads(Q_STATE_PATH.read_text())
    except Exception as e:
        logger.debug("Could not read q_state.json: %s", e)
        return ""

    lines = []

    # Keyword and tag performance signals
    kw = state.get("keyword_scores", {})
    tag = state.get("tag_scores", {})
    if kw or tag:
        top_kw = sorted(kw, key=lambda k: kw[k], reverse=True)[:5]
        top_tag = sorted(tag, key=lambda t: tag[t], reverse=True)[:5]
        top_kw = [k for k in top_kw if kw[k] > 0]
        top_tag = [t for t in top_tag if tag[t] > 0]
        if top_kw or top_tag:
            lines.append("Performance insights from previous posts (use to improve SEO):")
            if top_kw:
                lines.append(f"- High-engagement keywords: {', '.join(top_kw)}")
            if top_tag:
                lines.append(f"- High-engagement tags: {', '.join(top_tag)}")
            lines.append("Incorporate these where naturally appropriate.")

    # Prompt knobs (learned writing style parameters)
    knobs = state.get("prompt_knobs", {})
    if knobs:
        sarcasm = knobs.get("sarcasm_level", 0.20)
        painpoint = knobs.get("painpoint_intensity", 0.75)
        audience = knobs.get("audience_specificity", 0.80)

        lines.append("")
        lines.append("Learned parameters (optimized from engagement data):")
        lines.append(f"- Sarcasm level: {int(sarcasm * 100)}% (dry, subtle tone)")

        if painpoint >= 0.8:
            lines.append("- Pain point depth: HIGH (specific, visceral psychological struggles)")
        elif painpoint >= 0.5:
            lines.append("- Pain point depth: MEDIUM (relatable emotional challenges)")
        else:
            lines.append("- Pain point depth: LOW (gentle, supportive framing)")

        if audience >= 0.8:
            lines.append("- Audience targeting: PRECISE (speak directly to lonely/burned-out adults)")
        elif audience >= 0.5:
            lines.append("- Audience targeting: MODERATE (relatable to broader audience)")
        else:
            lines.append("- Audience targeting: BROAD (general human connection themes)")

    if lines:
        logger.info("Loaded bandit signals: %d keyword(s), %d tag(s), knobs=%s",
                     len([k for k in (kw or {}) if kw.get(k, 0) > 0]),
                     len([t for t in (tag or {}) if tag.get(t, 0) > 0]),
                     bool(knobs))
        return "\n".join(lines)

    return ""


def _split_article_and_metadata(raw_output: str) -> tuple[str, str]:
    """Split raw LLM output into article body and METADATA block."""
    patterns = [
        r"\n#{1,4}\s*METADATA\s*\n",
        r"\nMETADATA\s*\n",
        r"\n\*\*METADATA\*\*\s*\n",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_output, re.IGNORECASE)
        if match:
            article = raw_output[:match.start()].strip()
            metadata = raw_output[match.end():].strip()
            return article, metadata
    return raw_output.strip(), ""


def startup_checks() -> dict:
    """Run startup checks (once per session, not per blog).

    - Load embedding model
    - Check analytics sync
    - Check few-shot refresh
    - Check pending consolidations
    - Report FC angle phase
    """
    results = {
        "embedding_model": False,
        "analytics_sync_due": False,
        "few_shot_refresh_due": False,
        "pending_consolidations": 0,
        "fc_phase": 1,
    }

    # Load embedding model
    model = _load_embedding_model()
    results["embedding_model"] = model is not None

    # Run analytics sync if due (WordPress fallback, then Google if available)
    if analytics_sync.is_sync_due():
        results["analytics_sync_due"] = True
        try:
            sync_result = analytics_sync.sync_from_wordpress()
            if sync_result.get("synced"):
                logger.info(
                    "Analytics sync (WordPress): %d/%d blogs updated.",
                    sync_result.get("blogs_updated", 0),
                    sync_result.get("blogs_total", 0),
                )
                results["analytics_synced"] = True
            else:
                # Try Google-based sync as fallback
                google_result = analytics_sync.sync()
                if google_result.get("synced"):
                    logger.info("Analytics sync (Google): complete.")
                    results["analytics_synced"] = True
                else:
                    logger.info("Analytics sync skipped: %s", google_result.get("reason", "unknown"))
        except Exception as e:
            logger.warning("Analytics sync failed (non-blocking): %s", e)

    # Update sub-context performance rewards from analytics data
    try:
        ctx_result = sub_context_rotator.update_context_rewards()
        if ctx_result.get("updated", 0) > 0:
            logger.info("Sub-context rewards updated: %d context(s).", ctx_result["updated"])
    except Exception as e:
        logger.warning("Sub-context reward update failed (non-blocking): %s", e)

    # Sync demographic data from GA4 (if available)
    try:
        demo_result = analytics_sync.sync_demographics()
        if demo_result.get("synced"):
            logger.info(
                "Demographic sync: dominant=%s (%.1f%%), %d sessions.",
                demo_result.get("dominant_age_group", "?"),
                demo_result.get("dominant_share", 0) * 100,
                demo_result.get("total_sessions", 0),
            )
            results["demographic_synced"] = True
        else:
            logger.debug("Demographic sync skipped: %s", demo_result.get("reason", "unknown"))
    except Exception as e:
        logger.debug("Demographic sync failed (non-blocking): %s", e)

    # Run few-shot refresh if due and analytics data exists
    from . import few_shot_refresher as fsr
    registry_path = CONFIG_DIR / "blog_registry.json"
    if registry_path.exists():
        registry = json.loads(registry_path.read_text())
        if fsr._is_refresh_due(registry):
            results["few_shot_refresh_due"] = True
            try:
                refresh_result = fsr.refresh_few_shot_examples()
                if refresh_result.get("refreshed"):
                    logger.info(
                        "Few-shot examples refreshed: %d examples selected.",
                        refresh_result.get("examples_count", 0),
                    )
                    results["few_shot_refreshed"] = True
                else:
                    logger.info("Few-shot refresh skipped: %s", refresh_result.get("reason", "unknown"))
            except Exception as e:
                logger.warning("Few-shot refresh failed (non-blocking): %s", e)

    # Check pending consolidations
    pending = duplicate_consolidator.pending_consolidations()
    results["pending_consolidations"] = len(pending)
    if pending:
        logger.info(
            "%d pending consolidation(s): %s",
            len(pending),
            [c["name"] for c in pending],
        )

    # Update title format weights from q_learner data
    try:
        weight_result = title_scorer.update_format_weights_from_bandit()
        if weight_result.get("updated"):
            logger.info("Title format weights updated from bandit data.")
            results["title_weights_updated"] = True
        else:
            logger.info("Title format weights: %s", weight_result.get("reason", "no change"))
    except Exception as e:
        logger.warning("Title format weight update failed (non-blocking): %s", e)

    # FC angle phase — auto-confirm Phase 2 when eligible
    eligibility = fc_angle_manager.is_phase_2_eligible()
    if eligibility["eligible"] and not eligibility["already_confirmed"]:
        if fc_angle_manager.confirm_phase_2():
            logger.info("FC Angle Manager: auto-confirmed Phase 2 transition")
    results["fc_phase"] = fc_angle_manager.get_phase()

    return results


def run(config: PipelineConfig) -> PipelineResult:
    """Run the full blog generation pipeline.

    Args:
        config: PipelineConfig with topic, word count, and options.

    Returns a PipelineResult with the generated blog or error details.
    """
    result = PipelineResult()
    api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")

    if not api_key:
        result.error = "No Anthropic API key (LLM_RELAY_SECRET_ANTHROPIC_API_KEY)."
        logger.error(result.error)
        return result

    # ── STEP 1: Topic Deduplication ──────────────────────────────────────

    logger.info("Step 1: Topic deduplication for '%s'", config.topic)
    dedup_result = topic_dedup.check_similarity(
        config.topic, override=config.override_dedup
    )
    result.dedup_result = dedup_result

    if dedup_result["status"] == "BLOCKED":
        result.error = (
            f"Topic BLOCKED by deduplication — too similar to: "
            f"{dedup_result['similar_blogs'][0]['title']} "
            f"(similarity: {dedup_result['max_similarity']}). "
            f"Use override_dedup=True to proceed."
        )
        logger.warning(result.error)
        return result

    if dedup_result["status"] == "CAUTION":
        logger.info(
            "Topic CAUTION — overlaps with existing content. Proceeding with "
            "differentiation note."
        )

    # ── STEP 2: Title Pre-Scoring ────────────────────────────────────────

    if config.title_override:
        title = config.title_override
        logger.info("Step 2: Using title override: '%s'", title)
        result.title_scoring_result = {"selected": {"title": title}, "override": True}
    elif config.skip_title_scoring:
        title = config.topic
        logger.info("Step 2: Skipping title scoring — using topic as title: '%s'", title)
        result.title_scoring_result = {"selected": {"title": title}, "skipped": True}
    else:
        logger.info("Step 2: Title pre-scoring for topic '%s'", config.topic)
        try:
            scoring_result = title_scorer.generate_and_score(config.topic, api_key)
            title = scoring_result["selected"]["title"]
            result.title_scoring_result = scoring_result
            logger.info("Selected title: '%s' (score: %.1f)", title, scoring_result["selected"]["total"])
        except Exception as e:
            logger.warning("Title scoring failed: %s — using topic as title.", e)
            title = config.topic
            result.title_scoring_result = {"error": str(e)}

    result.title = title

    # ── STEP 3: Sub-Context Selection ────────────────────────────────────

    logger.info("Step 3: Sub-context selection")

    # Get recent sub-context IDs for demographic exploration tracking
    registry_data = json.loads(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {"blogs": []}
    recent_ctx_ids = [
        b.get("sub_context_id")
        for b in sorted(
            registry_data.get("blogs", []),
            key=lambda b: b.get("publish_date", ""),
            reverse=True,
        )[:5]
        if b.get("sub_context_id")
    ]

    # Get demographic boosts from viewer data (if available)
    demo_boosts = demographic_targeting.get_subcontext_demographic_boosts(
        recent_demographic_ids=recent_ctx_ids,
    )
    if demo_boosts:
        logger.info("Demographic boosts active: %s", {k: v for k, v in demo_boosts.items() if v != 0})

    sub_context = sub_context_rotator.select(
        title,
        demographic_boosts=demo_boosts,
    )
    result.sub_context = sub_context
    logger.info("Selected sub-context: [%s] %s", sub_context["category"], sub_context["id"])

    # ── STEP 4: FC Integration Angle ─────────────────────────────────────

    logger.info("Step 4: FC integration angle")
    fc_result = fc_angle_manager.get_angle(
        topic=title,
        manual_angle=config.fc_angle,
        manual_angle_type=config.fc_angle_type,
        sub_context=sub_context["text"],
        api_key=api_key,
    )
    result.fc_angle_result = fc_result

    if fc_result.get("error"):
        result.error = fc_result["error"]
        logger.warning("FC angle error: %s", result.error)
        return result

    fc_angle_text = fc_result["angle_text"]

    # Phase 2 auto-selected angles require review
    if fc_result.get("requires_review"):
        logger.info(
            "Auto-selected FC angle (requires review): %s", fc_angle_text[:100]
        )

    # ── STEP 5: Assemble Per-Blog Block ──────────────────────────────────

    logger.info("Step 5: Assembling per-blog instruction block")

    template = _load_per_blog_template()
    blog_lists = blog_list_manager.format_all_lists()
    few_shot_text = few_shot_refresher.get_few_shot_examples()

    # Build differentiation note if CAUTION
    sub_context_text = sub_context["text"]
    if dedup_result["status"] in ("CAUTION", "BLOCKED_OVERRIDE"):
        similar_titles = [b["title"] for b in dedup_result.get("similar_blogs", [])[:3]]
        sub_context_text += (
            f"\n\nIMPORTANT: This topic overlaps with existing blogs: "
            f"{', '.join(similar_titles)}. Differentiate your angle significantly."
        )

    user_message = template.format(
        title=title,
        word_count=config.word_count,
        min_word_count=config.min_word_count,
        max_word_count=config.max_word_count,
        fc_integration_angle=fc_angle_text,
        sub_context=sub_context_text,
        blog_list_lifestyle=blog_lists["blog_list_lifestyle"],
        blog_list_learning=blog_lists["blog_list_learning"],
        blog_list_uncategorized=blog_lists["blog_list_uncategorized"],
        blog_list_news=blog_lists["blog_list_news"],
        few_shot_examples=few_shot_text,
    )

    # ── STEP 6: Assemble Full API Call ───────────────────────────────────

    logger.info("Step 6: Assembling API call")

    system_prompt = _load_system_prompt()

    # Inject learned signals from the q_learner bandit
    bandit_signals = _load_bandit_signals()
    if bandit_signals:
        system_prompt += "\n\n" + bandit_signals

    calibration = _load_model_calibration()
    fallback_order = calibration.get("fallback_order", ["claude-sonnet-4-20250514"])

    # ── STEP 7 & 8: Call LLM with Validation + Retries ───────────────────

    best_attempt = None
    best_fail_count = float("inf")
    total_attempts = 0

    for model_name in fallback_order:
        model_config = calibration.get("models", {}).get(model_name, {})
        provider = model_config.get("provider", "anthropic")
        temperature = model_config.get("recommended_temperature", 0.7)
        max_tokens = model_config.get("recommended_max_tokens", 4096)
        calibration_append = model_config.get("append_to_system_prompt", "")

        # Build system message with optional calibration override
        full_system_prompt = system_prompt
        if calibration_append:
            full_system_prompt += "\n\n" + calibration_append

        # Max attempts per model: 3 for primary, 2 for backups
        max_attempts = 3 if model_config.get("role") == "primary" else 2

        for attempt in range(1, max_attempts + 1):
            total_attempts += 1
            logger.info(
                "Step 7: LLM call — model=%s attempt=%d/%d",
                model_name, attempt, max_attempts,
            )

            try:
                raw_output = blog_generator.generate(
                    system_prompt=full_system_prompt,
                    user_message=user_message,
                    model_name=model_name,
                    provider=provider,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                logger.error(
                    "LLM call failed (model=%s, attempt=%d): %s",
                    model_name, attempt, e,
                )
                result.all_attempts.append({
                    "model": model_name,
                    "attempt": attempt,
                    "error": str(e),
                })
                continue

            # Step 8: Validate
            logger.info("Step 8: Validating output (attempt %d)", total_attempts)
            report = validator.validate(raw_output, config.word_count)

            attempt_record = {
                "model": model_name,
                "attempt": attempt,
                "validation": report.to_dict(),
                "output_length": len(raw_output),
            }
            result.all_attempts.append(attempt_record)

            # Track best attempt
            if report.fail_count < best_fail_count:
                best_fail_count = report.fail_count
                best_attempt = {
                    "raw_output": raw_output,
                    "report": report,
                    "model": model_name,
                    "attempt": attempt,
                }

            if report.overall_pass:
                logger.info(
                    "Validation PASSED on %s attempt %d.",
                    model_name, attempt,
                )
                result.success = True
                result.model_used = model_name
                result.attempt_count = total_attempts
                result.validation_report = report.to_dict()

                article, metadata = _split_article_and_metadata(raw_output)
                result.content = article
                result.metadata_block = metadata

                # Step 9: Output
                if not config.dry_run:
                    _save_output(result, config)

                return result

            logger.info(
                "Validation FAILED on %s attempt %d (%d failures). %s",
                model_name,
                attempt,
                report.fail_count,
                "Retrying..." if attempt < max_attempts else "Moving to next model.",
            )

        logger.info("Exhausted attempts on %s — trying next model.", model_name)

    # All models exhausted — return best attempt
    if best_attempt:
        logger.warning(
            "All models exhausted. Returning best attempt (%s attempt %d, %d failures).",
            best_attempt["model"],
            best_attempt["attempt"],
            best_attempt["report"].fail_count,
        )
        result.success = False
        result.model_used = best_attempt["model"]
        result.attempt_count = total_attempts
        result.validation_report = best_attempt["report"].to_dict()
        result.error = (
            f"All models exhausted — best attempt had "
            f"{best_attempt['report'].fail_count} validation failure(s). "
            f"Human review required."
        )

        article, metadata = _split_article_and_metadata(best_attempt["raw_output"])
        result.content = article
        result.metadata_block = metadata

        if not config.dry_run:
            _save_output(result, config)
    else:
        result.error = "All LLM calls failed — no output generated."
        result.attempt_count = total_attempts

    return result


def _save_output(result: PipelineResult, config: PipelineConfig) -> None:
    """Save blog output and validation report to the outputs directory."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    # Create slug from title
    slug = re.sub(r"[^a-z0-9]+", "-", result.title.lower()).strip("-")[:60]

    # Save blog content
    blog_filename = f"{date_str}_{slug}.md"
    blog_path = OUTPUTS_DIR / blog_filename
    blog_content = result.content
    if result.metadata_block:
        blog_content += f"\n\n---\n\nMETADATA\n\n{result.metadata_block}"
    blog_path.write_text(blog_content)
    result.output_path = str(blog_path)

    # Save validation report
    validation_filename = f"{date_str}_{slug}_validation.json"
    validation_path = OUTPUTS_DIR / validation_filename
    validation_data = {
        "title": result.title,
        "model_used": result.model_used,
        "attempt_count": result.attempt_count,
        "success": result.success,
        "validation": result.validation_report,
        "all_attempts": result.all_attempts,
        "config": {
            "topic": config.topic,
            "word_count": config.word_count,
            "fc_angle": config.fc_angle,
            "override_dedup": config.override_dedup,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }
    validation_path.write_text(json.dumps(validation_data, indent=2))
    result.validation_path = str(validation_path)

    logger.info("Saved output to %s", blog_path)
    logger.info("Saved validation report to %s", validation_path)

    # Update blog registry with new title and embedding
    try:
        _embedding_model_instance = _load_embedding_model()
        embedding = None
        if _embedding_model_instance:
            embedding = _embedding_model_instance.encode(result.title).tolist()

        blog_list_manager.add_blog(
            blog_id=0,  # Placeholder — updated after WordPress publish
            title=result.title,
            category="Uncategorized",  # Updated after human review
            url="",  # Set after WordPress publish
            content=result.content,
            embedding=embedding,
        )

        # Store sub_context_id on the registry entry for performance tracking
        if result.sub_context and result.sub_context.get("id"):
            registry = json.loads(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {"blogs": []}
            for blog in reversed(registry.get("blogs", [])):
                if blog.get("title") == result.title:
                    blog["sub_context_id"] = result.sub_context["id"]
                    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
                    break
    except Exception as e:
        logger.warning("Failed to update blog registry: %s", e)


def handle_review(
    blog_id: int,
    action: str,
    reject_reason: Optional[str] = None,
    reject_tag: Optional[str] = None,
    category: Optional[str] = None,
    url: Optional[str] = None,
) -> dict:
    """Handle human review of a generated blog.

    Args:
        blog_id: The blog ID (from WordPress or registry).
        action: "approve" or "reject".
        reject_reason: Free-text reason (if rejecting).
        reject_tag: One of: tone_off, too_generic, bad_structure,
                    fc_mention_forced, factual_concern, other.
        category: Blog category to set on approval.
        url: Blog URL to set on approval.

    Returns a dict with the action taken.
    """
    if action == "approve":
        if category:
            blog_list_manager.update_blog_status(blog_id, "published")
            # Update category and URL in registry
            registry = json.loads(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {"blogs": []}
            for blog in registry.get("blogs", []):
                if blog.get("id") == blog_id:
                    blog["category"] = category
                    if url:
                        blog["url"] = url
                    blog["publish_date"] = datetime.utcnow().strftime("%Y-%m-%d")
                    break
            REGISTRY_PATH.write_text(json.dumps(registry, indent=2))

        logger.info("Blog #%d APPROVED.", blog_id)
        return {"action": "approved", "blog_id": blog_id}

    elif action == "reject":
        valid_tags = [
            "tone_off", "too_generic", "bad_structure",
            "fc_mention_forced", "factual_concern", "other",
        ]
        if reject_tag and reject_tag not in valid_tags:
            logger.warning("Invalid reject tag '%s' — using 'other'.", reject_tag)
            reject_tag = "other"

        blog_list_manager.update_blog_status(blog_id, "rejected")

        # Log rejection for future learning
        registry = json.loads(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {"blogs": []}
        for blog in registry.get("blogs", []):
            if blog.get("id") == blog_id:
                blog["reject_reason"] = reject_reason
                blog["reject_tag"] = reject_tag
                blog["rejected_at"] = datetime.utcnow().isoformat()
                break
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2))

        logger.info(
            "Blog #%d REJECTED [%s]: %s",
            blog_id, reject_tag, reject_reason,
        )
        return {
            "action": "rejected",
            "blog_id": blog_id,
            "tag": reject_tag,
            "reason": reject_reason,
        }

    else:
        return {"action": "unknown", "error": f"Unknown action: {action}"}
