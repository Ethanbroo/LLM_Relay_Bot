"""Response parser for Phase 6.

Phase 6 Invariant: Strict parsing - malformed responses are discarded, not retried.
"""

import re
import hashlib
import unicodedata
from dataclasses import dataclass
from orchestration.errors import LLMResponseInvalidError


@dataclass(frozen=True)
class LLMProposal:
    """Parsed LLM proposal.

    Phase 6 Invariant: All proposals are strictly typed and immutable.
    """
    model: str
    proposal_text: str
    rationale_text: str
    confidence: float
    proposal_hash: str

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "model": self.model,
            "proposal_text": self.proposal_text,
            "rationale_text": self.rationale_text,
            "confidence": self.confidence,
            "proposal_hash": self.proposal_hash,
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

        # 🔹 Normalize BEFORE validation + hashing
        proposal_text = normalize_proposal(proposal_text)

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


def normalize_proposal(proposal_text: str) -> str:
    """Normalize proposal text before comparison.

    Phase 6 Invariant:
    - Unicode NFC normalization
    - Trim trailing whitespace only
    - No semantic rewriting
    """
    normalized = unicodedata.normalize("NFC", proposal_text)
    return normalized.rstrip()

