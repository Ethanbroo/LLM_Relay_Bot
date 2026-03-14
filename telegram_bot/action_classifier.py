"""
Action classification for the browser agent approval flow.

Every browser action is classified into one of three risk tiers:
  - Tier 1 (AUTO_EXECUTE): Safe, read-only, or expected-domain navigation
  - Tier 2 (REQUIRES_APPROVAL): Sensitive — credentials, form submits, unexpected domains
  - Tier 3 (BLOCKED): Dangerous — internal networks, known malicious, code execution

The default is Tier 2 (fail-safe): any action not explicitly classified as
Tier 1 or Tier 3 requires user approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class ActionTier(Enum):
    AUTO_EXECUTE = 1
    REQUIRES_APPROVAL = 2
    BLOCKED = 3


@dataclass
class ClassificationResult:
    tier: ActionTier
    reason: str
    display_summary: str
    requires_approval: bool
    blocked: bool


# Keywords that indicate a sensitive submit button
_SENSITIVE_BUTTON_KEYWORDS = re.compile(
    r"\b(submit|buy|purchase|order|pay|confirm|agree|subscribe|checkout|"
    r"donate|sign\s*up|register|enroll|apply|place\s+order|add\s+to\s+cart)\b",
    re.IGNORECASE,
)

# Keywords that indicate a login/submit button (auto-approved after credential fill)
_LOGIN_BUTTON_KEYWORDS = re.compile(
    r"\b(log\s*in|sign\s*in|continue|next|submit)\b",
    re.IGNORECASE,
)

# Keywords that indicate a sensitive input field
_SENSITIVE_FIELD_KEYWORDS = re.compile(
    r"\b(password|credit\s*card|card\s*number|cvv|cvc|ssn|social\s*security|"
    r"bank\s*account|routing\s*number|date\s*of\s*birth|passport|"
    r"driver.?s?\s*licen[cs]e|pin\s*code|security\s*code)\b",
    re.IGNORECASE,
)

# Keywords that suggest a file download
_DOWNLOAD_KEYWORDS = re.compile(
    r"\b(download)\b|\.(?:exe|zip|dmg|msi|pdf|apk|tar|gz|rar|deb|rpm)\b",
    re.IGNORECASE,
)

# RFC 1918 + loopback patterns for internal network detection
_INTERNAL_NETWORK_PATTERNS = [
    re.compile(r"^https?://localhost\b"),
    re.compile(r"^https?://127\.\d+\.\d+\.\d+"),
    re.compile(r"^https?://10\.\d+\.\d+\.\d+"),
    re.compile(r"^https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"),
    re.compile(r"^https?://192\.168\.\d+\.\d+"),
    re.compile(r"^https?://\[::1\]"),
    re.compile(r"^https?://0\.0\.0\.0"),
]

# Blocked URL schemes
_BLOCKED_SCHEMES = {"data", "javascript", "blob", "vbscript"}

# Common typosquat patterns (extend as needed)
_TYPOSQUAT_PATTERNS = [
    re.compile(r"g00gle|go0gle|googl\.com", re.IGNORECASE),
    re.compile(r"facebo0k|faceb00k|facebok", re.IGNORECASE),
    re.compile(r"amaz0n|amazn\.com", re.IGNORECASE),
    re.compile(r"paypai|paypa1\.com", re.IGNORECASE),
    re.compile(r"micros0ft|mircosoft|microsft", re.IGNORECASE),
    re.compile(r"app[l1]e\.com\.[\w]+", re.IGNORECASE),
]


def extract_domain(url: str) -> str:
    """Extract the domain (host) from a URL, lowercased."""
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def extract_domains_from_text(text: str) -> set[str]:
    """Extract plausible domain names from a task description.

    Recognises explicit URLs and common service names.
    """
    domains: set[str] = set()

    # Explicit URLs
    for match in re.finditer(r"https?://([a-zA-Z0-9._-]+)", text):
        domains.add(match.group(1).lower())

    # Common service name -> domain mapping
    _SERVICE_MAP = {
        "google": {"google.com", "www.google.com"},
        "gmail": {"gmail.com", "mail.google.com", "accounts.google.com"},
        "youtube": {"youtube.com", "www.youtube.com", "accounts.google.com"},
        "spotify": {"spotify.com", "open.spotify.com", "accounts.spotify.com"},
        "amazon": {"amazon.com", "www.amazon.com"},
        "twitter": {"twitter.com", "x.com"},
        "facebook": {"facebook.com", "www.facebook.com"},
        "instagram": {"instagram.com", "www.instagram.com"},
        "linkedin": {"linkedin.com", "www.linkedin.com"},
        "github": {"github.com", "www.github.com"},
        "reddit": {"reddit.com", "www.reddit.com", "old.reddit.com"},
        "netflix": {"netflix.com", "www.netflix.com"},
        "ebay": {"ebay.com", "www.ebay.com"},
    }

    text_lower = text.lower()
    for service, service_domains in _SERVICE_MAP.items():
        if service in text_lower:
            domains.update(service_domains)

    return domains


def is_internal_network(url: str) -> bool:
    """Check if a URL targets an internal/private network address."""
    for pattern in _INTERNAL_NETWORK_PATTERNS:
        if pattern.search(url):
            return True
    return False


def is_known_malicious(url: str) -> bool:
    """Check if a URL matches known malicious patterns."""
    parsed = urlparse(url)

    # Block dangerous schemes
    if parsed.scheme.lower() in _BLOCKED_SCHEMES:
        return True

    # Block IP-address URLs (not domain names)
    hostname = parsed.hostname or ""
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", hostname):
        return True

    # Check typosquat patterns
    for pattern in _TYPOSQUAT_PATTERNS:
        if pattern.search(url):
            return True

    return False


def _is_sensitive_button(element_name: str) -> bool:
    """Check if a button/link name suggests a sensitive action."""
    return bool(_SENSITIVE_BUTTON_KEYWORDS.search(element_name))


def _is_login_submit_button(element_name: str) -> bool:
    """Check if a button name suggests a login submit action."""
    return bool(_LOGIN_BUTTON_KEYWORDS.search(element_name))


def _is_sensitive_field(element_name: str) -> bool:
    """Check if a field name suggests sensitive data entry."""
    return bool(_SENSITIVE_FIELD_KEYWORDS.search(element_name))


def _is_download_element(element_name: str) -> bool:
    """Check if an element suggests a file download."""
    return bool(_DOWNLOAD_KEYWORDS.search(element_name))


def _get_element_name(action_params: dict, elements: list) -> str:
    """Look up the accessible name of an element by reference number."""
    ref = action_params.get("element")
    if ref is None:
        return ""
    for elem in elements:
        if elem.ref == ref:
            return elem.name or ""
    return ""


def _get_element_role(action_params: dict, elements: list) -> str:
    """Look up the role of an element by reference number."""
    ref = action_params.get("element")
    if ref is None:
        return ""
    for elem in elements:
        if elem.ref == ref:
            return elem.role or ""
    return ""


def classify_action(
    action_name: str,
    action_params: dict,
    current_url: str,
    expected_domains: set[str],
    elements: list,
    last_approved_domain: str | None = None,
    last_approval_time=None,
) -> ClassificationResult:
    """Classify a browser action into a risk tier.

    Args:
        action_name: The tool name (navigate, click, type_text, etc.)
        action_params: The tool input parameters.
        current_url: The browser's current URL.
        expected_domains: Domains considered safe for this task.
        elements: The current FlatElement list from the accessibility tree.
        last_approved_domain: Domain of the most recently approved credential fill.
        last_approval_time: datetime of the most recent credential approval.

    Returns:
        ClassificationResult with tier, reason, and display summary.
    """
    from datetime import datetime, timezone

    # ── Tier 3 checks (blocked) ──────────────────────────
    if action_name == "navigate":
        url = action_params.get("url", "")
        if is_internal_network(url):
            return ClassificationResult(
                tier=ActionTier.BLOCKED,
                reason="Navigation to internal network address",
                display_summary=f"Navigate to {url}",
                requires_approval=False,
                blocked=True,
            )
        if is_known_malicious(url):
            return ClassificationResult(
                tier=ActionTier.BLOCKED,
                reason="Navigation to known malicious or suspicious URL",
                display_summary=f"Navigate to {url}",
                requires_approval=False,
                blocked=True,
            )

    # Block unknown action types
    known_actions = {
        "navigate", "click", "type_text", "select_option", "scroll",
        "fill_credentials", "task_complete", "task_failed",
    }
    if action_name not in known_actions:
        return ClassificationResult(
            tier=ActionTier.BLOCKED,
            reason=f"Unknown action type: {action_name}",
            display_summary=f"Unknown: {action_name}",
            requires_approval=False,
            blocked=True,
        )

    # ── Tier 1 checks (auto-execute) ─────────────────────
    if action_name in ("scroll", "task_complete", "task_failed"):
        return ClassificationResult(
            tier=ActionTier.AUTO_EXECUTE,
            reason="Read-only or terminal action",
            display_summary=action_name,
            requires_approval=False,
            blocked=False,
        )

    if action_name == "navigate":
        url = action_params.get("url", "")
        domain = extract_domain(url)
        if domain and _domain_matches(domain, expected_domains):
            return ClassificationResult(
                tier=ActionTier.AUTO_EXECUTE,
                reason=f"Navigation to expected domain: {domain}",
                display_summary=f"Navigate to {url}",
                requires_approval=False,
                blocked=False,
            )

    if action_name == "click":
        element_name = _get_element_name(action_params, elements)
        if not _is_sensitive_button(element_name) and not _is_download_element(element_name):
            # Check if this is a login button right after credential approval
            if (
                _is_login_submit_button(element_name)
                and last_approved_domain
                and last_approval_time
            ):
                now = datetime.now(timezone.utc)
                elapsed = (now - last_approval_time).total_seconds()
                current_domain = extract_domain(current_url)
                if current_domain == last_approved_domain and elapsed < 30:
                    return ClassificationResult(
                        tier=ActionTier.AUTO_EXECUTE,
                        reason="Follow-up to recently approved credential fill",
                        display_summary=f"Click '{element_name}'",
                        requires_approval=False,
                        blocked=False,
                    )

            # Regular non-sensitive click within expected domain
            current_domain = extract_domain(current_url)
            if _domain_matches(current_domain, expected_domains):
                return ClassificationResult(
                    tier=ActionTier.AUTO_EXECUTE,
                    reason="Click on non-sensitive element within expected domain",
                    display_summary=f"Click '{element_name}'",
                    requires_approval=False,
                    blocked=False,
                )

    if action_name == "type_text":
        element_name = _get_element_name(action_params, elements)
        if not _is_sensitive_field(element_name):
            current_domain = extract_domain(current_url)
            if _domain_matches(current_domain, expected_domains):
                return ClassificationResult(
                    tier=ActionTier.AUTO_EXECUTE,
                    reason="Typing into non-sensitive field within expected domain",
                    display_summary=f"Type into '{element_name}'",
                    requires_approval=False,
                    blocked=False,
                )

    if action_name == "select_option":
        current_domain = extract_domain(current_url)
        if _domain_matches(current_domain, expected_domains):
            element_name = _get_element_name(action_params, elements)
            return ClassificationResult(
                tier=ActionTier.AUTO_EXECUTE,
                reason="Select option within expected domain",
                display_summary=f"Select '{action_params.get('value', '?')}' in '{element_name}'",
                requires_approval=False,
                blocked=False,
            )

    # ── Tier 2 (everything else requires approval) ───────
    display = _build_display_summary(action_name, action_params, elements)
    reason = _build_tier2_reason(action_name, action_params, current_url, elements)

    return ClassificationResult(
        tier=ActionTier.REQUIRES_APPROVAL,
        reason=reason,
        display_summary=display,
        requires_approval=True,
        blocked=False,
    )


def _domain_matches(domain: str, expected_domains: set[str]) -> bool:
    """Check if domain matches any expected domain (exact or subdomain)."""
    if not domain:
        return False
    for expected in expected_domains:
        if domain == expected or domain.endswith("." + expected):
            return True
    return False


def _build_display_summary(action_name: str, action_params: dict, elements: list) -> str:
    """Build a human-readable summary of the action for Telegram."""
    if action_name == "navigate":
        return f"Navigate to {action_params.get('url', '?')}"
    elif action_name == "click":
        name = _get_element_name(action_params, elements)
        return f"Click '{name}'" if name else f"Click element [{action_params.get('element', '?')}]"
    elif action_name == "type_text":
        name = _get_element_name(action_params, elements)
        text = action_params.get("text", "")
        preview = text[:30] + "..." if len(text) > 30 else text
        return f"Type '{preview}' into '{name}'"
    elif action_name == "fill_credentials":
        return f"Fill login credentials for {action_params.get('domain', '?')}"
    elif action_name == "select_option":
        name = _get_element_name(action_params, elements)
        return f"Select '{action_params.get('value', '?')}' in '{name}'"
    return f"{action_name}"


def _build_tier2_reason(action_name: str, action_params: dict, current_url: str, elements: list) -> str:
    """Build a human-readable reason for why the action requires approval."""
    if action_name == "fill_credentials":
        return f"Credential injection for {action_params.get('domain', '?')}"
    elif action_name == "navigate":
        domain = extract_domain(action_params.get("url", ""))
        return f"Navigation to unexpected domain: {domain}"
    elif action_name == "click":
        name = _get_element_name(action_params, elements)
        if _is_sensitive_button(name):
            return f"Click on sensitive button: '{name}'"
        if _is_download_element(name):
            return f"Click on download element: '{name}'"
        return f"Click on element outside expected domain: '{name}'"
    elif action_name == "type_text":
        name = _get_element_name(action_params, elements)
        if _is_sensitive_field(name):
            return f"Typing into sensitive field: '{name}'"
        return f"Typing into field outside expected domain: '{name}'"
    elif action_name == "select_option":
        return "Selection outside expected domain"
    return f"Action requires approval: {action_name}"
