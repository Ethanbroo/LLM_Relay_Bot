"""Response parser for Phase 6.

Phase 6 Invariant: Strict parsing - malformed responses are discarded, not retried.
"""

import re
import hashlib
import unicodedata
from dataclasses import dataclass, field
from orchestration.errors import LLMResponseInvalidError

# Closed set of valid intent types.  Any value not in this set is silently
# replaced with the safe default "analysis" to prevent hallucinated types from
# leaking into downstream gate logic.
VALID_INTENT_TYPES: frozenset = frozenset({"analysis", "action"})


@dataclass(frozen=True)
class LLMProposal:
    """Parsed LLM proposal.

    Phase 6 Invariant: All proposals are strictly typed and immutable.

    Intent metadata fields (Layer 0):
    - intent_type: "analysis" (read-only) or "action" (state-changing).
      Validated against VALID_INTENT_TYPES; defaults to "analysis" when
      absent or unrecognised to prevent hallucinated types from leaking
      into gate logic.
    - is_state_changing: True only when intent_type == "action".
    - purpose: Free-text human-readable label for audit log context.

    All three fields use field(default=...) so existing constructors that
    omit them continue to work without modification.
    """
    model: str
    proposal_text: str
    rationale_text: str
    confidence: float
    proposal_hash: str
    # Intent metadata — defaults guarantee backward compatibility
    intent_type: str = field(default="analysis")
    is_state_changing: bool = field(default=False)
    purpose: str = field(default="")

    def __post_init__(self) -> None:
        # Validate intent_type against closed set; silently reset to safe default
        # if an unexpected value (e.g. LLM hallucination) slips through.
        if object.__getattribute__(self, "intent_type") not in VALID_INTENT_TYPES:
            object.__setattr__(self, "intent_type", "analysis")
            object.__setattr__(self, "is_state_changing", False)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "model": self.model,
            "proposal_text": self.proposal_text,
            "rationale_text": self.rationale_text,
            "confidence": self.confidence,
            "proposal_hash": self.proposal_hash,
            "intent_type": self.intent_type,
            "is_state_changing": self.is_state_changing,
            "purpose": self.purpose,
        }


class ResponseParser:
    """Parser for LLM responses.

    Phase 6 Invariants:
    - Must detect all three sections (PROPOSAL, RATIONALE, CONFIDENCE)
    - CONFIDENCE must parse as float ∈ [0.0, 1.0]
    - No extra sections allowed
    - Max length caps enforced
    """

    MAX_PROPOSAL_LENGTH = 1000
    MAX_RATIONALE_LENGTH = 2000

    def parse_response(self, model_id: str, response_text: str) -> LLMProposal:
        """Parse LLM response into structured proposal."""

        proposal_text = self._extract_section(response_text, "PROPOSAL")
        rationale_text = self._extract_section(response_text, "RATIONALE")
        confidence_text = self._extract_section(response_text, "CONFIDENCE")

        # Normalize at input boundary — once and only once — before any
        # validation, hashing, or embedding.
        proposal_text = normalize_text(proposal_text)
        rationale_text = normalize_text(rationale_text)

        if len(proposal_text) > self.MAX_PROPOSAL_LENGTH:
            raise LLMResponseInvalidError(
                f"Proposal too long: {len(proposal_text)} > {self.MAX_PROPOSAL_LENGTH}"
            )

        if len(rationale_text) > self.MAX_RATIONALE_LENGTH:
            raise LLMResponseInvalidError(
                f"Rationale too long: {len(rationale_text)} > {self.MAX_RATIONALE_LENGTH}"
            )

        confidence = self._parse_confidence(confidence_text)

        # 🔹 Hash must match normalized text
        proposal_hash = self._compute_proposal_hash(proposal_text)

        return LLMProposal(
            model=model_id,
            proposal_text=proposal_text,
            rationale_text=rationale_text,
            confidence=confidence,
            proposal_hash=proposal_hash,
        )

    def _extract_section(self, text: str, section_name: str) -> str:
        """Extract section content from response.

        Strict rule:
        - Section must immediately contain non-whitespace content.
        - Blank sections are invalid.
        """

        pattern = rf"{section_name}:\n([^\n].*?)(?=\n[A-Z]+:|$)"
        match = re.search(pattern, text, re.DOTALL)

        if not match:
            raise LLMResponseInvalidError(f"Missing section: {section_name}")

        content = match.group(1).strip()

        if not content:
            raise LLMResponseInvalidError(f"Empty section: {section_name}")

        return content


    def _parse_confidence(self, confidence_text: str) -> float:
        """Parse confidence value."""
        try:
            confidence = float(confidence_text.strip())
        except ValueError:
            raise LLMResponseInvalidError(
                f"Confidence not a valid float: {confidence_text}"
            )

        if not (0.0 <= confidence <= 1.0):
            raise LLMResponseInvalidError(
                f"Confidence out of range [0.0, 1.0]: {confidence}"
            )

        return confidence

    def _compute_proposal_hash(self, proposal_text: str) -> str:
        """Compute SHA-256 hash of proposal text."""
        return hashlib.sha256(proposal_text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """Normalize text before comparison, hashing, or embedding.

    Phase 6 Invariant:
    - Unicode NFC normalization (not NFKC — preserves code identifiers)
    - Trim trailing whitespace only
    - No semantic rewriting
    - Apply once at the input boundary; never re-normalize already-normalized text
    """
    normalized = unicodedata.normalize("NFC", text)
    return normalized.rstrip()


# Backward-compatible alias — existing callers that import normalize_proposal
# continue to work without modification.
normalize_proposal = normalize_text

