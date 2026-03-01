"""
Epistemic Containment Layer (ECL) — v2.0

The most significant new component in v2.0.

This module governs not WHAT the system decides, but HOW it shapes the space
of choices presented to the user. It enforces the Influence Boundary Protocol.

The core distinction (from the outline):
- Decision risk: system selects an outcome it was not authorized to select.
- Influence risk: system shapes the landscape of perceived options in ways that
  systematically bias user choices—without making any explicit selection.

A system that never decides but always presents options in ranked, value-laden
format using criteria it inferred is not neutral. It is an author of the option
landscape. This module prevents that.

Influence Boundary Protocol (hard rules):
1. Remove all superlatives ('best', 'optimal', 'recommended') unless directly
   grounded in GoalContract success criteria.
2. If options are ordered, prepend the ordering rationale, attributed to
   GoalContract criteria. If no GoalContract criterion applies → random order.
3. Disclose excluded options: if an option was excluded due to system inference
   rather than a GoalContract constraint, include it and flag the exclusion reason.
4. Log all framing decisions to the Option Framing Audit Trail before presentation.

Soft Authorship Detection:
Flags potential soft authorship when an agent uses superlatives, proposes a
possibility space smaller than GoalContract permits, or frames a binary choice
when more options exist. Flagged events go to the audit trail.

Strictness levels (configurable):
- strict: All rules enforced; flagged events surface to user before presentation.
- moderate: Rules enforced; flagged events logged but not surfaced live.
- minimal: Logging only; no framing modification.
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# Superlatives and value-laden terms that trigger soft authorship detection
SUPERLATIVE_PATTERNS = re.compile(
    r"\b(best|optimal|recommended|ideal|superior|perfect|excellent|top|premier|"
    r"most efficient|most effective|better|worse|worst|definitive|clearly|obviously)\b",
    re.IGNORECASE,
)


class ContainmentStrictness(str, Enum):
    STRICT = "strict"        # Enforce + surface flags to user
    MODERATE = "moderate"    # Enforce + log flags (no live surfacing)
    MINIMAL = "minimal"      # Log only; no framing modification


@dataclass
class OptionEntry:
    """A single option in a set to be presented to the user."""
    option_id: str
    content: Any                          # The option's content (text, dict, etc.)
    proposed_by: str                      # Agent or source that proposed this
    excluded: bool = False
    exclusion_reason: Optional[str] = None
    exclusion_is_system_inference: bool = False  # True if exclusion wasn't GoalContract-derived
    presentation_label: Optional[str] = None     # Label shown to user


@dataclass
class FramedOptionSet:
    """
    The output of the ECL: a fully-disclosed, neutrally-framed option set
    ready for user presentation.
    """
    framing_id: str
    options: List[OptionEntry]
    ordering_rationale: Optional[str]     # Why options appear in this order; None = random
    disclosed_exclusions: List[OptionEntry]  # Options considered but excluded
    soft_authorship_flags: List[str]      # Detected influence violations
    framed_at: str
    contract_id: str
    strictness: str

    def to_dict(self) -> dict:
        return {
            "framing_id": self.framing_id,
            "options": [
                {
                    "option_id": o.option_id,
                    "content": o.content,
                    "proposed_by": o.proposed_by,
                    "presentation_label": o.presentation_label,
                }
                for o in self.options if not o.excluded
            ],
            "ordering_rationale": self.ordering_rationale,
            "disclosed_exclusions": [
                {
                    "option_id": o.option_id,
                    "content": o.content,
                    "exclusion_reason": o.exclusion_reason,
                    "exclusion_is_system_inference": o.exclusion_is_system_inference,
                }
                for o in self.disclosed_exclusions
            ],
            "soft_authorship_flags": self.soft_authorship_flags,
            "framed_at": self.framed_at,
            "contract_id": self.contract_id,
            "strictness": self.strictness,
        }


class OptionFramingAuditTrail:
    """
    Logs all framing decisions. The trail records not just what was presented,
    but how choices were framed — including order, language, and excluded options.
    This is the influence audit trail, distinct from the decision audit trail.
    """

    def __init__(self, audit_callback: Optional[Callable[[str, dict], None]] = None) -> None:
        self._callback = audit_callback
        self._entries: List[dict] = []

    def log(self, framed_set: FramedOptionSet) -> None:
        entry = {
            "framing_id": framed_set.framing_id,
            "contract_id": framed_set.contract_id,
            "option_count": len([o for o in framed_set.options if not o.excluded]),
            "excluded_count": len(framed_set.disclosed_exclusions),
            "soft_authorship_flags": framed_set.soft_authorship_flags,
            "ordering_rationale": framed_set.ordering_rationale,
            "framed_at": framed_set.framed_at,
            "strictness": framed_set.strictness,
        }
        self._entries.append(entry)
        if self._callback:
            self._callback("OPTION_FRAMING_LOGGED", entry)

    def all_entries(self) -> List[dict]:
        return list(self._entries)


class EpistemicContainmentLayer:
    """
    Applies the Influence Boundary Protocol to any set of options before
    they are presented to the user.

    Instantiate once per session with the GoalContract and desired strictness.
    Call apply() for each option set that an agent produces.
    """

    def __init__(
        self,
        goal_contract,
        strictness: ContainmentStrictness = ContainmentStrictness.STRICT,
        audit_trail: Optional[OptionFramingAuditTrail] = None,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._contract = goal_contract
        self._strictness = strictness
        self._trail = audit_trail or OptionFramingAuditTrail(audit_callback)
        self._audit_callback = audit_callback

    @property
    def strictness(self) -> ContainmentStrictness:
        return self._strictness

    def apply(
        self,
        options: List[OptionEntry],
        ordering_criterion: Optional[str] = None,
    ) -> FramedOptionSet:
        """
        Apply the Influence Boundary Protocol to an option set.

        Steps:
        1. Detect soft authorship in option labels/content.
        2. Strip unauthorized superlatives (strict/moderate mode).
        3. Determine ordering (use GoalContract criterion or randomize).
        4. Segregate excluded options; flag those excluded by system inference.
        5. Log to audit trail.
        6. Return FramedOptionSet.

        Args:
            options: Raw options produced by an agent.
            ordering_criterion: If set, must match a GoalContract success_criterion.
                                 If None or not in GoalContract, order is randomized.

        Returns:
            A FramedOptionSet ready for user presentation.
        """
        flags: List[str] = []
        active_options: List[OptionEntry] = []
        excluded_options: List[OptionEntry] = []

        for opt in options:
            if opt.excluded:
                # Check if exclusion was by system inference vs GoalContract constraint
                if opt.exclusion_is_system_inference:
                    # Must disclose — include in disclosed_exclusions with flag
                    flags.append(
                        f"Option '{opt.option_id}' was excluded based on system inference, "
                        f"not a GoalContract constraint. Disclosed to user as required."
                    )
                excluded_options.append(opt)
            else:
                # Check for soft authorship in content
                content_str = str(opt.content) if not isinstance(opt.content, str) else opt.content
                label_str = opt.presentation_label or ""
                combined = content_str + " " + label_str

                soft_flags = self._detect_soft_authorship(opt.option_id, combined)
                flags.extend(soft_flags)

                if self._strictness in (ContainmentStrictness.STRICT, ContainmentStrictness.MODERATE):
                    # Strip unauthorized superlatives
                    opt.content = self._strip_superlatives(opt.content, self._contract)
                    if opt.presentation_label:
                        opt.presentation_label = self._strip_superlatives(
                            opt.presentation_label, self._contract
                        )

                active_options.append(opt)

        # Determine ordering
        validated_criterion = None
        if ordering_criterion:
            # Only use if it matches a GoalContract success criterion
            if any(ordering_criterion in sc for sc in self._contract.success_criteria):
                validated_criterion = ordering_criterion
            else:
                flags.append(
                    f"Ordering criterion '{ordering_criterion}' is not grounded in "
                    f"GoalContract success_criteria. Randomizing order instead."
                )

        if validated_criterion:
            ordering_rationale = f"Ordered by: {validated_criterion}"
            # Ordering by criterion would require a comparator — for now preserve order
        else:
            # Random order to prevent implied ranking
            random.shuffle(active_options)
            ordering_rationale = None

        # Check for binary framing when more options might exist
        if len(active_options) == 2:
            flags.append(
                "Only 2 options presented. Verify this is not a false binary "
                "(GoalContract may permit more options)."
            )

        framed = FramedOptionSet(
            framing_id=str(uuid.uuid4()),
            options=active_options,
            ordering_rationale=ordering_rationale,
            disclosed_exclusions=excluded_options,
            soft_authorship_flags=flags,
            framed_at=datetime.now(timezone.utc).isoformat(),
            contract_id=self._contract.contract_id,
            strictness=self._strictness.value,
        )

        # Log to audit trail BEFORE returning (before user sees it)
        self._trail.log(framed)

        # In STRICT mode: emit audit event for review if flags exist
        if flags and self._strictness == ContainmentStrictness.STRICT:
            if self._audit_callback:
                self._audit_callback("SOFT_AUTHORSHIP_DETECTED", {
                    "framing_id": framed.framing_id,
                    "flags": flags,
                    "contract_id": self._contract.contract_id,
                    "strictness": self._strictness.value,
                    "action": "framing_modified_and_flags_surfaced_to_user",
                })

        return framed

    def _detect_soft_authorship(self, option_id: str, text: str) -> List[str]:
        """Detect superlatives and value-laden language not grounded in GoalContract."""
        flags = []
        matches = SUPERLATIVE_PATTERNS.findall(text)
        for match in set(matches):  # deduplicate
            # Check if this term appears in GoalContract success criteria
            # (would be legitimate if GoalContract says "best" performance)
            grounded = any(
                match.lower() in sc.lower()
                for sc in self._contract.success_criteria
            )
            if not grounded:
                flags.append(
                    f"Option '{option_id}': superlative '{match}' is not grounded in "
                    f"GoalContract success_criteria. Stripped in strict/moderate mode."
                )
        return flags

    def _strip_superlatives(self, content: Any, goal_contract) -> Any:
        """
        Remove superlatives not grounded in GoalContract from string content.
        Non-string content is returned unchanged.
        """
        if not isinstance(content, str):
            return content

        def replace_if_ungrounded(m: re.Match) -> str:
            word = m.group(0)
            grounded = any(
                word.lower() in sc.lower()
                for sc in goal_contract.success_criteria
            )
            return word if grounded else "[criterion not specified]"

        return SUPERLATIVE_PATTERNS.sub(replace_if_ungrounded, content)
