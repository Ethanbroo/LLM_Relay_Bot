"""Tier 4 Quality Gate: LLM Vision Model Review.

Final quality check using GPT-4o-mini's vision capabilities.
This provides human-level assessment of image quality, composition,
and adherence to brand guidelines.
"""

import logging
import time
import base64
from pathlib import Path
from typing import Optional

from .models import QualityGateResult, GateDecision, GateTier

logger = logging.getLogger(__name__)


# GPT-4o-mini vision pricing (as of early 2026)
GPT4O_MINI_COST_PER_IMAGE = 0.002  # Approximate cost per image review


VISION_REVIEW_SYSTEM_PROMPT = """You are a quality control specialist for AI-generated Instagram content.

Your job is to review generated images and identify issues that would make them unsuitable for posting:

REJECT if you see:
- Distorted or deformed faces, hands, or body parts
- Uncanny valley / obviously AI-generated appearance
- Artifacts, glitches, or visual corruption
- Text or watermarks in the image
- Multiple people when only one was expected
- Inappropriate or off-brand content
- Wrong identity (if this doesn't look like the same person as reference)

MARGINAL (flag for review) if you see:
- Minor composition issues that could be improved
- Slightly unnatural poses or expressions
- Small artifacts that might be acceptable
- Borderline quality on technical execution

APPROVE if:
- Image looks photorealistic and natural
- No obvious AI artifacts or distortions
- Composition is good and brand-appropriate
- Person's identity appears consistent
- Technical quality meets Instagram standards

Respond with a JSON object:
{
  "decision": "approve" | "reject" | "marginal",
  "reason": "brief explanation (1-2 sentences)",
  "issues": ["list", "of", "specific", "issues"],
  "quality_score": 0-100
}"""


class LLMVisionGate:
    """
    Tier 4: GPT-4o-mini vision model review.

    This is the most expensive gate (~$0.002 per image) but provides
    human-level quality assessment. Use selectively:
    - After all other gates pass (don't waste $ on obvious failures)
    - For hero shots / primary carousel images
    - When CLIP or identity scores are marginal
    """

    def __init__(self, openai_api_key: str, model: str = "gpt-4o-mini"):
        """
        Initialize LLM vision gate.

        Args:
            openai_api_key: OpenAI API key
            model: Model to use (default: gpt-4o-mini)
        """
        self.api_key = openai_api_key
        self.model = model

        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=openai_api_key)
        except ImportError:
            raise ImportError(
                "OpenAI SDK not installed. Install with: pip install openai"
            )

    def evaluate(
        self,
        image_path: str,
        context: Optional[dict] = None
    ) -> QualityGateResult:
        """
        Evaluate image using GPT-4o-mini vision.

        Args:
            image_path: Path to generated image
            context: Optional context dict with:
                - prompt: Generation prompt
                - character_id: Character identifier
                - is_hero_shot: Whether this is the primary image

        Returns:
            QualityGateResult with PASS/MARGINAL/FAIL decision
        """
        start_time = time.time()

        logger.info(
            "Running Tier 4 LLM vision gate on %s",
            Path(image_path).name
        )

        # Build user message with image and context
        user_message_parts = []

        # Add context if provided
        if context:
            context_text = "Context:\n"
            if "prompt" in context:
                context_text += f"- Generation prompt: {context['prompt']}\n"
            if "character_id" in context:
                context_text += f"- Character: {context['character_id']}\n"
            if context.get("is_hero_shot"):
                context_text += "- This is a hero shot (primary image) - higher standards apply\n"

            user_message_parts.append({
                "type": "text",
                "text": context_text
            })

        # Encode image as base64
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Add image
        user_message_parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_data}",
                "detail": "high"  # Use high detail for better quality assessment
            }
        })

        user_message_parts.append({
            "type": "text",
            "text": "Please review this generated image and provide your assessment."
        })

        # Call GPT-4o-mini
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": VISION_REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message_parts}
                ],
                max_tokens=500,
                temperature=0.3,  # Lower temperature for more consistent reviews
            )

            response_text = response.choices[0].message.content

            # Parse JSON response
            import json
            # Strip markdown code fences if present
            clean_response = response_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            review = json.loads(clean_response)

        except Exception as e:
            logger.error("LLM vision gate failed: %s", e)
            return QualityGateResult(
                tier=GateTier.TIER_4_LLM_VISION,
                decision=GateDecision.FAIL,
                score=0.0,
                reason=f"LLM vision evaluation failed: {e}",
                gate_cost_usd=GPT4O_MINI_COST_PER_IMAGE,
                execution_time_s=time.time() - start_time,
            )

        execution_time = time.time() - start_time

        # Map LLM decision to GateDecision
        llm_decision = review.get("decision", "reject").lower()
        if llm_decision == "approve":
            decision = GateDecision.PASS
        elif llm_decision == "marginal":
            decision = GateDecision.MARGINAL
        else:
            decision = GateDecision.FAIL

        reason = review.get("reason", "No reason provided")
        quality_score = review.get("quality_score", 0) / 100.0  # Normalize to [0, 1]
        issues = review.get("issues", [])

        logger.info(
            "Tier 4 LLM vision gate: %s (quality: %.2f, reason: %s)",
            decision.value.upper(),
            quality_score,
            reason[:50]
        )

        return QualityGateResult(
            tier=GateTier.TIER_4_LLM_VISION,
            decision=decision,
            score=quality_score,
            reason=reason,
            details={
                "issues": issues,
                "raw_response": review,
                "model": self.model,
            },
            gate_cost_usd=GPT4O_MINI_COST_PER_IMAGE,
            execution_time_s=execution_time,
        )

    def evaluate_batch(
        self,
        image_paths: list[str],
        contexts: Optional[list[dict]] = None
    ) -> list[QualityGateResult]:
        """
        Evaluate multiple images.

        Note: OpenAI API doesn't support batching vision requests,
        so this is just sequential evaluation.

        Args:
            image_paths: List of image paths
            contexts: Optional list of context dicts (same order as images)

        Returns:
            List of QualityGateResults (same order as input)
        """
        if contexts is None:
            contexts = [None] * len(image_paths)

        if len(contexts) != len(image_paths):
            raise ValueError("contexts must have same length as image_paths")

        results = []
        for image_path, context in zip(image_paths, contexts):
            result = self.evaluate(image_path, context)
            results.append(result)

        return results
