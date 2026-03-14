"""
Output filtering — Phase 5.

Prevents data exfiltration through the browser agent by checking outbound
actions for suspicious patterns before they reach the browser container.

Three exfiltration channels are addressed:
  1. Image/tracking URLs with embedded query data
  2. DNS-based data leaks (handled by domain allowlist)
  3. Form submissions to unauthorized domains

This filter runs after the security gate approves an action but before
the action is sent to the browser.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def check_url_for_exfiltration(url: str) -> tuple[bool, str]:
    """Check if a URL appears to be carrying exfiltrated data.

    Returns:
        (is_safe: bool, reason: str)
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Failed to parse URL"

    # Block data: and javascript: URLs entirely
    if parsed.scheme in ("data", "javascript", "blob"):
        return False, f"Blocked scheme: {parsed.scheme}"

    # Check query parameters for suspicious values
    params = parse_qs(parsed.query)
    for key, values in params.items():
        for value in values:
            # Base64-like strings (long alphanumeric with +/= padding)
            if len(value) > 20 and re.match(r"^[A-Za-z0-9+/=]+$", value):
                return False, f"Suspicious base64-like value in param '{key}'"

            # Long hex strings
            if len(value) > 20 and re.match(r"^[0-9a-fA-F]+$", value):
                return False, f"Suspicious hex value in param '{key}'"

            # Email-like patterns
            if "@" in value and "." in value.split("@")[-1]:
                return False, f"Email-like value in param '{key}'"

            # Very long values (legitimate query params are usually short)
            if len(value) > 200:
                return False, f"Unusually long value in param '{key}' ({len(value)} chars)"

    return True, "URL appears clean"


def check_form_submission(
    form_action_url: str | None,
    current_url: str,
    allowlist,
) -> tuple[bool, str]:
    """Check if a form submission targets an authorized domain.

    Args:
        form_action_url: The form's action URL (may be None if no form found).
        current_url: The page's current URL for context.
        allowlist: DomainAllowlist instance for domain checking.

    Returns:
        (is_safe: bool, reason: str)
    """
    if not form_action_url:
        return True, "No form action URL detected"

    try:
        parsed = urlparse(form_action_url)
        form_domain = (parsed.hostname or "").lower()
    except Exception:
        return False, "Failed to parse form action URL"

    if not form_domain:
        # Relative URL — submits to same domain, which is fine
        return True, "Form submits to same origin"

    result = allowlist.check(form_domain)
    if result in ("blocked", "unlisted"):
        status = "blocked" if result == "blocked" else "not on the allowlist"
        return False, f"Form submits to {form_domain} which is {status}"

    return True, f"Form target {form_domain} is {result}"


async def filter_outbound_action(
    action_name: str,
    action_params: dict,
    allowlist,
) -> tuple[bool, str]:
    """Filter outbound actions for data exfiltration attempts.

    This is the main entry point called by the security gate after
    classification but before browser execution.

    Args:
        action_name: The tool name (navigate, click, etc.)
        action_params: The tool input parameters.
        allowlist: DomainAllowlist instance.

    Returns:
        (is_safe: bool, reason: str)
    """
    # Check 1: URL exfiltration for navigation actions
    if action_name == "navigate":
        url = action_params.get("url", "")
        safe, reason = check_url_for_exfiltration(url)
        if not safe:
            logger.warning("Exfiltration filter blocked navigate to %s: %s", url[:100], reason)
            return False, f"Exfiltration filter: {reason}"

    # Check 2: Type actions that look like they contain encoded data
    if action_name == "type_text":
        text = action_params.get("text", "")
        if len(text) > 200:
            # Very long typed text might be exfiltrating data
            if re.match(r"^[A-Za-z0-9+/=]+$", text):
                logger.warning("Exfiltration filter blocked suspicious type_text (%d chars)", len(text))
                return False, "Suspicious base64-like content in type_text"

    return True, "Passed all output filters"
