"""
Security gate — the single integration point between the ReAct loop and
all security systems (Phases 4 + 5).

Called before every browser action in the ReAct loop. The gate runs checks
in this order:

  1. Domain allowlist check (Phase 5) — blocks navigation to unlisted domains
  2. Action classification (Phase 4) — assigns risk tier (1/2/3)
  3. Output exfiltration filter (Phase 5) — blocks data-leaking URLs/forms
  4. Injection suspicion scoring (Phase 5) — flags poisoned page content
  5. Dual-model verification (Phase 5, optional) — second LLM consistency check
  6. Approval flow (Phase 4) — Telegram approval for Tier 2 actions
  7. Audit logging (Phase 5) — structured JSON log of every decision

Returns one of:
  - "proceed"  — execute the action
  - "skip"     — skip the action (rejected/blocked/timeout)
  - "cancel"   — terminate the entire task
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from telegram_bot.action_classifier import (
    ActionTier,
    classify_action,
    extract_domain,
)
from telegram_bot.approval_manager import (
    ApprovalManager,
    generate_action_id,
    send_approval_request,
    wait_for_approval,
)

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "300"))


async def security_gate(
    action_name: str,
    action_params: dict,
    task_state,
    elements: list,
    screenshot_b64: str | None,
    bot,
    approval_manager: ApprovalManager,
    allowlist=None,
    audit=None,
    sanitized_content: str = "",
) -> str:
    """Security gate called before every action in the ReAct loop.

    Args:
        action_name: The tool name (navigate, click, etc.)
        action_params: The tool input parameters.
        task_state: The TaskState object for the current task.
        elements: Current FlatElement list from the accessibility tree.
        screenshot_b64: Base64-encoded screenshot of the current page.
        bot: The Telegram bot instance for sending messages.
        approval_manager: The global ApprovalManager instance.
        allowlist: DomainAllowlist instance (Phase 5, optional).
        audit: AuditLogger instance (Phase 5, optional).
        sanitized_content: Sanitized page content for injection scoring (Phase 5).

    Returns:
        'proceed', 'skip', or 'cancel'.
    """
    step_num = task_state.step_count if task_state else 0
    task_id = task_state.task_id if task_state else "unknown"
    allowlist_result = ""
    suspicion_score = 0.0
    verifier_result_str = ""

    # ── 1. Domain allowlist check (Phase 5) ──────────────────────────
    if allowlist and action_name == "navigate":
        url = action_params.get("url", "")
        domain = extract_domain(url)
        if domain:
            allowlist_result = allowlist.check(domain)

            if allowlist_result == "blocked":
                if audit:
                    audit.log_action_blocked(
                        task_id=task_id, step_number=step_num,
                        action_name=action_name, action_params=action_params,
                        block_reason=f"Domain {domain} is on the blocked list",
                        block_source="allowlist",
                    )
                logger.warning("Allowlist BLOCKED domain %s for task %s", domain, task_id)
                return "skip"

            if allowlist_result == "unlisted":
                default_action = allowlist.default_action
                if default_action == "block":
                    if audit:
                        audit.log_action_blocked(
                            task_id=task_id, step_number=step_num,
                            action_name=action_name, action_params=action_params,
                            block_reason=f"Domain {domain} is not on the allowlist (default: block)",
                            block_source="allowlist",
                        )
                    logger.warning("Allowlist BLOCKED unlisted domain %s for task %s", domain, task_id)
                    return "skip"
                # default_action == "prompt" — fall through to Tier 2 approval

    # ── 2. Action classification (Phase 4) ───────────────────────────
    classification = classify_action(
        action_name=action_name,
        action_params=action_params,
        current_url=task_state.current_url,
        expected_domains=task_state.expected_domains,
        elements=elements,
        last_approved_domain=task_state.last_approved_domain,
        last_approval_time=task_state.last_approval_time,
    )

    # ── 3. Output exfiltration filter (Phase 5) ─────────────────────
    if not classification.blocked:
        from telegram_bot.output_filter import filter_outbound_action
        is_safe, filter_reason = await filter_outbound_action(
            action_name=action_name,
            action_params=action_params,
            allowlist=allowlist,
        )
        if not is_safe:
            if audit:
                audit.log_action_blocked(
                    task_id=task_id, step_number=step_num,
                    action_name=action_name, action_params=action_params,
                    block_reason=filter_reason,
                    block_source="output_filter",
                )
                audit.log_security_event(
                    task_id=task_id,
                    event_subtype="exfiltration_attempt",
                    details=filter_reason,
                )
            logger.warning("Output filter BLOCKED %s in task %s: %s", action_name, task_id, filter_reason)
            return "skip"

    # ── 4. Injection suspicion scoring (Phase 5) ─────────────────────
    if sanitized_content:
        from telegram_bot.injection_classifier import (
            calculate_suspicion_score,
            is_suspicious,
            is_very_likely_injection,
        )
        suspicion_score = calculate_suspicion_score(sanitized_content)

        if is_very_likely_injection(suspicion_score):
            if audit:
                audit.log_security_event(
                    task_id=task_id,
                    event_subtype="prompt_injection_detected",
                    details=f"Very high suspicion score: {suspicion_score:.1f}",
                    severity="critical",
                )
            # Force Tier 2 regardless of classification
            if not classification.requires_approval and not classification.blocked:
                classification = classification.__class__(
                    tier=ActionTier.REQUIRES_APPROVAL,
                    reason=f"Forced approval: high injection suspicion score ({suspicion_score:.1f})",
                    display_summary=classification.display_summary,
                    requires_approval=True,
                    blocked=False,
                )
        elif is_suspicious(suspicion_score):
            if audit:
                audit.log_security_event(
                    task_id=task_id,
                    event_subtype="suspicious_content_score",
                    details=f"Elevated suspicion score: {suspicion_score:.1f}",
                )

    # ── Log classification (Phase 5 audit) ───────────────────────────
    if audit:
        audit.log_action_classified(
            task_id=task_id,
            step_number=step_num,
            action_name=action_name,
            classification_tier=classification.tier.value,
            classification_reason=classification.reason,
            allowlist_result=allowlist_result,
            suspicion_score=suspicion_score,
        )

    # ── Tier 3: blocked ──────────────────────────────────────────────
    if classification.blocked:
        if audit:
            audit.log_action_blocked(
                task_id=task_id, step_number=step_num,
                action_name=action_name, action_params=action_params,
                block_reason=classification.reason,
                block_source="tier3_classification",
            )
        logger.warning(
            "BLOCKED action in task %s: %s — %s",
            task_id, action_name, classification.reason,
        )
        return "skip"

    # ── Tier 1: auto-execute ─────────────────────────────────────────
    if not classification.requires_approval:
        return "proceed"

    # ── 5. Dual-model verification (Phase 5, optional) ───────────────
    try:
        from telegram_bot.dual_model_verifier import should_verify, verify_action_consistency
        if should_verify(
            action_name=action_name,
            classification_tier=classification.tier.value,
            suspicion_score=suspicion_score,
            allowlist_result=allowlist_result,
        ):
            # Build compressed history from task_state
            compressed_history = [
                f"{a.get('action', '?')} on {a.get('url', '?')}"
                for a in (task_state.action_history or [])[-10:]
            ]
            proposed = {
                "name": action_name,
                "params": action_params,
                "reasoning": action_params.get("reasoning", ""),
            }
            is_consistent, verifier_reasoning = await verify_action_consistency(
                user_task=task_state.user_task,
                compressed_history=compressed_history,
                proposed_action=proposed,
                current_url=task_state.current_url,
            )
            verifier_result_str = f"{'consistent' if is_consistent else 'INCONSISTENT'}: {verifier_reasoning}"

            if not is_consistent:
                if audit:
                    audit.log_security_event(
                        task_id=task_id,
                        event_subtype="verifier_inconsistency",
                        details=verifier_reasoning,
                        severity="warning",
                    )
                # Auto-skip inconsistent actions without bothering the user
                if audit:
                    audit.log_action_blocked(
                        task_id=task_id, step_number=step_num,
                        action_name=action_name, action_params=action_params,
                        block_reason=f"Verifier flagged as inconsistent: {verifier_reasoning}",
                        block_source="verifier",
                    )
                return "skip"

            # Update audit with verifier result
            if audit:
                audit.log_action_classified(
                    task_id=task_id, step_number=step_num,
                    action_name=action_name,
                    classification_tier=classification.tier.value,
                    classification_reason=classification.reason,
                    allowlist_result=allowlist_result,
                    suspicion_score=suspicion_score,
                    verifier_result=verifier_result_str,
                )
    except ImportError:
        pass  # Dual-model verifier not available — skip

    # ── 6. Tier 2: requires approval (Phase 4) ──────────────────────
    action_id = generate_action_id()
    pending = approval_manager.create(task_state.task_id, action_id)
    approval_start = time.monotonic()

    msg_id = await send_approval_request(
        bot=bot,
        chat_id=task_state.chat_id,
        task_id=task_state.task_id,
        action_id=action_id,
        screenshot_b64=screenshot_b64,
        current_url=task_state.current_url,
        action_summary=classification.display_summary,
        reasoning=action_params.get("reasoning", "No reasoning provided"),
        action_name=action_name,
    )

    if audit:
        audit.log_approval_requested(
            task_id=task_id,
            action_summary=classification.display_summary,
            current_url=task_state.current_url,
            message_id=msg_id,
        )

    task_state.status = "waiting_approval"
    task_state.approval_message_id = msg_id

    result = await wait_for_approval(pending, timeout_seconds=APPROVAL_TIMEOUT_SECONDS)
    response_time = time.monotonic() - approval_start

    approval_manager.remove(task_state.task_id, action_id)
    task_state.status = "running"
    task_state.last_activity = datetime.now(timezone.utc)

    if audit:
        audit.log_approval_resolved(
            task_id=task_id,
            decision=result,
            response_time_seconds=response_time,
            message_id=msg_id,
        )

    if result == "approved":
        # Track credential approvals for follow-up auto-approve
        if action_name == "fill_credentials":
            task_state.last_approved_domain = extract_domain(task_state.current_url)
            task_state.last_approval_time = datetime.now(timezone.utc)

        # Add approved navigation domains to expected set
        if action_name == "navigate":
            new_domain = extract_domain(action_params.get("url", ""))
            if new_domain:
                task_state.expected_domains.add(new_domain)

        return "proceed"

    elif result == "rejected":
        return "skip"

    elif result == "cancelled":
        return "cancel"

    elif result == "timeout":
        # Auto-reject and update the Telegram message
        try:
            timeout_mins = APPROVAL_TIMEOUT_SECONDS // 60
            try:
                await bot.edit_message_caption(
                    chat_id=task_state.chat_id,
                    message_id=msg_id,
                    caption=(
                        f"Timed out -- auto-rejected after {timeout_mins} minutes.\n"
                        f"Reply /resume to continue."
                    ),
                    reply_markup=None,
                )
            except Exception:
                await bot.edit_message_text(
                    chat_id=task_state.chat_id,
                    message_id=msg_id,
                    text=(
                        f"Timed out -- auto-rejected after {timeout_mins} minutes.\n"
                        f"Reply /resume to continue."
                    ),
                    reply_markup=None,
                )
        except Exception:
            logger.warning("Failed to update timeout message", exc_info=True)

        task_state.status = "paused"
        return "skip"

    return "skip"
