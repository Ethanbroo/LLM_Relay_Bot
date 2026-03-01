"""Blog Quality Gate — pre-posting evaluator.

Scores draft before posting. Rejects if below threshold.
Rejected posts = negative reward signal for bandit.
"""

import os
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EVALUATOR_THRESHOLD = 6.0  # overall_pass if avg of 3 scores >= this
EVALUATOR_MODEL = "claude-sonnet-4-5-20250929"

# Set BLOG_SKIP_QUALITY_GATE=1 to bypass (e.g. for testing)


@dataclass
class EvaluatorResult:
    """Result of blog draft evaluation."""
    title_score: float
    engagement_score: float
    audience_score: float
    image_score: float = 0.0  # Will be set separately after image selection
    specificity_score: float = 0.0  # Checks for generic platitudes
    credibility_score: float = 0.0  # Evidence-first reasoning
    overall_pass: bool = True
    feedback: str = ""
    platitudes_detected: list = None  # Specific generic phrases found
    unsubstantiated_claims: list = None  # Claims without mechanisms


def evaluate_draft(
    title: str,
    excerpt: str,
    content: str,
    image_tone: str = None,
    image_description: str = None
) -> EvaluatorResult:
    """Score draft on title strength, psychological engagement, audience relevance, and image match.

    Returns EvaluatorResult with scores 0-10 and overall_pass.

    Args:
        title: Blog post title
        excerpt: Blog post excerpt/summary
        content: Full blog content
        image_tone: Detected tone of selected image (optional)
        image_description: Image description for relevance check (optional)
    """
    if os.environ.get("BLOG_SKIP_QUALITY_GATE", "").strip() == "1":
        return EvaluatorResult(
            title_score=8.0, engagement_score=8.0, audience_score=8.0,
            overall_pass=True, feedback="Skipped (BLOG_SKIP_QUALITY_GATE=1)",
        )
    api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("No Anthropic key — skipping evaluation, defaulting to pass")
        return EvaluatorResult(
            title_score=7.0,
            engagement_score=7.0,
            audience_score=7.0,
            overall_pass=True,
            feedback="Evaluation skipped (no API key)",
        )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        content_preview = content[:4000] if content else ""

        # Build image context if provided
        image_context = ""
        if image_tone or image_description:
            image_context = f"\nImage: tone={image_tone or 'unknown'} description='{image_description or 'none'}'"

        prompt = f"""You are a skeptical editor at a mental health publication. Assume this draft is FLAWED until proven otherwise.

Your task: Find reasons to REJECT this draft. Use falsification-first reasoning.

Title: {title}
Excerpt: {excerpt}
Content preview: {content_preview}{image_context}

VALIDATION CHECKLIST (Score 0-10, be harsh):

1. TITLE STRENGTH (title_score)
   - Does it promise specific insight (NOT generic advice)?
   - Does it avoid clickbait without substance?
   - Does it speak to a defined pain point (NOT "everyone feels lonely")?
   REJECT if: Generic, vague, or overpromises

2. SPECIFICITY (specificity_score)
   - Are pain points VISCERAL and CONCRETE (NOT "feeling disconnected")?
   - Does it avoid platitudes: "just", "simply", "you're not alone", "reach out", "easy steps"?
   - Are examples SPECIFIC (NOT "many people struggle")?
   REJECT if: Contains 2+ generic phrases or abstract pain points

3. CREDIBILITY (credibility_score)
   - Do claims cite MECHANISMS (HOW it works, not just "studies show")?
   - Are psychological insights EXPLAINED (NOT name-dropped)?
   - Does advice have ACTIONABLE CONTEXT (NOT "build community" without how)?
   REJECT if: Unsubstantiated claims or research without explanation

4. ENGAGEMENT DEPTH (engagement_score)
   - Does it acknowledge UNCOMFORTABLE truths about loneliness/burnout?
   - Does it avoid toxic positivity ("just be grateful")?
   - Does tone match pain severity (NOT cheerful for grief)?
   REJECT if: Surface-level, dismissive, or tone-mismatched

5. AUDIENCE TARGETING (audience_score)
   - Does it speak to SPECIFIC struggles of lonely/burned-out adults?
   - Does it avoid corporate self-help clichés?
   - Does it position community as SOLUTION (not product)?
   - Does the TITLE work for readers aged 16-60? (A title like "Why Your Dad Has No Friends" alienates readers who ARE dads — penalize titles that exclude major age segments)
   - Would someone aged 50 reading this feel spoken TO or spoken ABOUT?
   REJECT if: Could apply to any audience, corporate tone, or title/framing excludes a major age segment of the target audience (16-60)

{"6. IMAGE MATCH (image_score)\n   - Does image emotionally align with blog tone?\n   - Does it avoid party/celebration imagery for isolation topics?\n   REJECT if: Tone mismatch or contradicts message" if image_context else ""}

REJECTION CRITERIA:
- Average score < 6.0
- ANY dimension < 3.5
- 3+ platitudes detected
- 5+ unsubstantiated claims (1-4 is acceptable if mechanisms are mostly explained)

Return ONLY valid JSON:
{{
  "title_score": N,
  "specificity_score": N,
  "credibility_score": N,
  "engagement_score": N,
  "audience_score": N{',\n  "image_score": N' if image_context else ''},
  "overall_pass": true/false,
  "feedback": "brief rejection reason",
  "platitudes_detected": ["phrase1", "phrase2"],
  "unsubstantiated_claims": ["claim without mechanism"]
}}"""

        message = client.messages.create(
            model=EVALUATOR_MODEL,
            max_tokens=1024,  # Room for platitudes/claims arrays + detailed feedback
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        ts = float(data.get("title_score", 5))
        spec_s = float(data.get("specificity_score", 5))
        cred_s = float(data.get("credibility_score", 5))
        es = float(data.get("engagement_score", 5))
        as_ = float(data.get("audience_score", 5))
        img_s = float(data.get("image_score", 0))  # Optional
        platitudes = data.get("platitudes_detected", []) or []
        claims = data.get("unsubstantiated_claims", []) or []
        feedback = data.get("feedback", "")

        # Calculate average across all dimensions (5 core + 1 optional image)
        if img_s > 0:
            avg = (ts + spec_s + cred_s + es + as_ + img_s) / 6.0
            min_score = min(ts, spec_s, cred_s, es, as_, img_s)
        else:
            avg = (ts + spec_s + cred_s + es + as_) / 5.0
            min_score = min(ts, spec_s, cred_s, es, as_)

        # Server-side rejection logic is authoritative (ignores Claude's overall_pass)
        # Reject if: avg < 6.0 OR any dimension < 3.5 OR 3+ platitudes OR 5+ unsubstantiated claims
        passed = True
        if avg < EVALUATOR_THRESHOLD or min_score < 3.5 or len(platitudes) >= 3 or len(claims) >= 5:
            passed = False

        logger.info(
            "Evaluator: title=%.1f spec=%.1f cred=%.1f engage=%.1f audience=%.1f image=%.1f platitudes=%d claims=%d pass=%s",
            ts, spec_s, cred_s, es, as_, img_s, len(platitudes), len(claims), passed,
        )
        return EvaluatorResult(
            title_score=ts,
            specificity_score=spec_s,
            credibility_score=cred_s,
            engagement_score=es,
            audience_score=as_,
            image_score=img_s,
            platitudes_detected=platitudes,
            unsubstantiated_claims=claims,
            overall_pass=passed,
            feedback=feedback or "",
        )
    except Exception as e:
        logger.error("Evaluator failed: %s — defaulting to FAIL (manual review required)", e)
        return EvaluatorResult(
            title_score=0.0,
            engagement_score=0.0,
            audience_score=0.0,
            overall_pass=False,
            feedback=f"Evaluation failed with error: {e}. Defaulting to FAIL — manual review required before posting.",
        )
