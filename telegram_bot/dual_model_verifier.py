"""
Dual-model action verification — Phase 5 (optional defense layer).

Uses a second Claude call (Haiku) that NEVER sees page content to verify
whether a proposed action is consistent with the original task. This makes
the verifier immune to prompt injection from web pages.

The verifier receives:
  - The user's original task description
  - Compressed action history (no page content)
  - The proposed action and the Actor's reasoning
  - The current URL

It returns a simple consistent/inconsistent judgment.

Cost: ~$0.001 per verification (~$0.03 per 30-step task).
Latency: ~1s per call (Haiku).
"""

from __future__ import annotations

import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

VERIFIER_MODEL = os.environ.get("VERIFIER_MODEL", "claude-haiku-4-5-20251001")


async def verify_action_consistency(
    user_task: str,
    compressed_history: list[str],
    proposed_action: dict,
    current_url: str,
) -> tuple[bool, str]:
    """Use a second Claude call to verify action consistency.

    This model NEVER sees page content — only the task, history,
    and proposed action. This makes it immune to prompt injection.

    Args:
        user_task: The user's original task description.
        compressed_history: List of short step summaries (no page content).
        proposed_action: Dict with 'name', 'params', 'reasoning' keys.
        current_url: The browser's current URL.

    Returns:
        (is_consistent: bool, reasoning: str)
    """
    history_text = "\n".join(
        f"  {i + 1}. {step}" for i, step in enumerate(compressed_history)
    )

    # Redact sensitive params before sending to verifier
    safe_params = {
        k: v for k, v in proposed_action.get("params", {}).items()
        if k not in ("password", "username", "text", "cookies", "totp")
    }

    verification_prompt = (
        "You are a security verification system. Your job is to determine "
        "whether a proposed browser action is consistent with the user's "
        "original task.\n\n"
        "You do NOT see the web page content. You only see the task, the "
        "history of actions taken, and the proposed next action. Evaluate "
        "whether the proposed action logically follows from the task and "
        "history.\n\n"
        f"ORIGINAL TASK: {user_task}\n\n"
        f"ACTIONS TAKEN SO FAR:\n{history_text or '  (none yet)'}\n\n"
        f"CURRENT URL: {current_url}\n\n"
        f"PROPOSED NEXT ACTION:\n"
        f"  Type: {proposed_action['name']}\n"
        f"  Parameters: {json.dumps(safe_params, indent=2)}\n"
        f"  Actor's reasoning: {proposed_action.get('reasoning', 'None provided')}\n\n"
        "QUESTION: Is this proposed action consistent with the original task?\n"
        "Consider:\n"
        "- Does the action logically follow from what has been done so far?\n"
        "- Is the target URL/domain related to the task?\n"
        "- Would a reasonable, non-compromised agent take this action?\n"
        "- Does the actor's reasoning make sense given the task?\n\n"
        'Respond with ONLY a JSON object:\n'
        '{"consistent": true/false, "reasoning": "your explanation"}'
    )

    try:
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=VERIFIER_MODEL,
            messages=[{"role": "user", "content": verification_prompt}],
            max_tokens=256,
        )

        text = response.content[0].text.strip()
        # Strip markdown fences if present
        text = text.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(text)
        is_consistent = result.get("consistent", True)
        reasoning = result.get("reasoning", "")

        if not is_consistent:
            logger.warning(
                "Verifier flagged inconsistent action: %s — %s",
                proposed_action["name"],
                reasoning,
            )

        return is_consistent, reasoning

    except (json.JSONDecodeError, IndexError, KeyError) as e:
        logger.warning("Verification parse error: %s — defaulting to allow", e)
        return True, "Verification parse error — defaulting to allow"
    except Exception as e:
        logger.warning("Verification call failed: %s — defaulting to allow", e)
        return True, f"Verification call failed: {e}"


def should_verify(
    action_name: str,
    classification_tier: int,
    suspicion_score: float,
    allowlist_result: str,
) -> bool:
    """Decide whether dual-model verification should run for this action.

    Uses verification selectively to control cost and latency:
      - Actions flagged by the suspicion scorer
      - Tier 2 actions (before sending the approval prompt)
      - Navigation to non-always_allowed domains

    Skips verification for:
      - Tier 1 auto-execute actions on trusted domains
      - Scroll, screenshot, task_complete, task_failed
      - Actions already blocked by Tier 3
    """
    # Never verify terminal/read-only actions
    if action_name in ("scroll", "task_complete", "task_failed"):
        return False

    # Never verify already-blocked actions
    if classification_tier == 3:
        return False

    # Verify if suspicion score is elevated
    if suspicion_score >= 5.0:
        return True

    # Verify Tier 2 actions
    if classification_tier == 2:
        return True

    # Verify navigation to non-always_allowed domains
    if action_name == "navigate" and allowlist_result not in ("always_allowed",):
        return True

    return False
