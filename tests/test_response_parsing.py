"""Test Phase 6 response parsing and validation.

Phase 6 Invariant: All model outputs treated as untrusted.
Strict parsing with fail-closed behavior.
"""

import pytest
from orchestration.response_parser import ResponseParser, LLMProposal, normalize_proposal
from orchestration.errors import LLMResponseInvalidError


class TestResponseParsing:
    """Test strict parsing of LLM responses."""

    def test_valid_response_parsing(self):
        """Test parsing of valid response."""
        response_text = """PROPOSAL:
Implement input validation at API boundary.

RATIONALE:
This prevents injection attacks at the earliest point.

CONFIDENCE:
0.85"""

        parser = ResponseParser()
        proposal = parser.parse_response("chatgpt", response_text)

        assert proposal.model == "chatgpt"
        assert "input validation" in proposal.proposal_text.lower()
        assert "injection attacks" in proposal.rationale_text.lower()
        assert proposal.confidence == 0.85
        assert len(proposal.proposal_hash) == 64  # SHA-256

    def test_missing_proposal_section_fails(self):
        """Test that missing PROPOSAL section causes failure."""
        response_text = """RATIONALE:
Some rationale here.

CONFIDENCE:
0.75"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Missing section: PROPOSAL"):
            parser.parse_response("claude", response_text)

    def test_missing_rationale_section_fails(self):
        """Test that missing RATIONALE section causes failure."""
        response_text = """PROPOSAL:
Some proposal here.

CONFIDENCE:
0.75"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Missing section: RATIONALE"):
            parser.parse_response("gemini", response_text)

    def test_missing_confidence_section_fails(self):
        """Test that missing CONFIDENCE section causes failure."""
        response_text = """PROPOSAL:
Some proposal here.

RATIONALE:
Some rationale here."""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Missing section: CONFIDENCE"):
            parser.parse_response("deepseek", response_text)

    def test_invalid_confidence_format_fails(self):
        """Test that invalid confidence format causes failure."""
        response_text = """PROPOSAL:
Some proposal.

RATIONALE:
Some rationale.

CONFIDENCE:
high"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Confidence not a valid float"):
            parser.parse_response("chatgpt", response_text)

    def test_confidence_out_of_range_fails(self):
        """Test that confidence outside [0.0, 1.0] fails."""
        response_text = """PROPOSAL:
Some proposal.

RATIONALE:
Some rationale.

CONFIDENCE:
1.5"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Confidence out of range"):
            parser.parse_response("claude", response_text)

    def test_negative_confidence_fails(self):
        """Test that negative confidence fails."""
        response_text = """PROPOSAL:
Some proposal.

RATIONALE:
Some rationale.

CONFIDENCE:
-0.1"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Confidence out of range"):
            parser.parse_response("gemini", response_text)

    def test_proposal_too_long_fails(self):
        """Test that proposal exceeding max length fails."""
        long_proposal = "X" * 1001  # Max is 1000
        response_text = f"""PROPOSAL:
{long_proposal}

RATIONALE:
Valid rationale.

CONFIDENCE:
0.80"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Proposal too long"):
            parser.parse_response("deepseek", response_text)

    def test_rationale_too_long_fails(self):
        """Test that rationale exceeding max length fails."""
        long_rationale = "X" * 2001  # Max is 2000
        response_text = f"""PROPOSAL:
Valid proposal.

RATIONALE:
{long_rationale}

CONFIDENCE:
0.80"""

        parser = ResponseParser()
        with pytest.raises(LLMResponseInvalidError, match="Rationale too long"):
            parser.parse_response("chatgpt", response_text)

    def test_empty_proposal_fails(self):
        """Test that empty proposal fails."""
        response_text = """PROPOSAL:

RATIONALE:
Some rationale.

CONFIDENCE:
0.75"""

        parser = ResponseParser()
        # Empty section followed by newline + next section triggers Missing section error
        with pytest.raises(LLMResponseInvalidError):
            parser.parse_response("claude", response_text)

    def test_empty_rationale_fails(self):
        """Test that empty rationale fails."""
        response_text = """PROPOSAL:
Some proposal.

RATIONALE:

CONFIDENCE:
0.75"""

        parser = ResponseParser()
        # Empty section followed by newline + next section triggers Missing section error
        with pytest.raises(LLMResponseInvalidError):
            parser.parse_response("gemini", response_text)

    def test_normalize_proposal_unicode_nfc(self):
        """Test that proposals are normalized to Unicode NFC."""
        # U+00E9 (é) vs U+0065 U+0301 (e + combining acute)
        text1 = "café"  # Composed form
        text2 = "café"  # Decomposed form (if editor supports it)

        normalized1 = normalize_proposal(text1)
        normalized2 = normalize_proposal(text2)

        # Both should normalize to same form
        assert normalized1 == normalized2

    def test_normalize_proposal_strips_trailing_whitespace(self):
        """Test that trailing whitespace is stripped."""
        text = "Some proposal text   \n\n"
        normalized = normalize_proposal(text)

        assert normalized == "Some proposal text"
        assert not normalized.endswith(" ")
        assert not normalized.endswith("\n")

    def test_normalize_proposal_preserves_internal_whitespace(self):
        """Test that internal whitespace is preserved."""
        text = "Line 1\n\nLine 2\n\nLine 3"
        normalized = normalize_proposal(text)

        assert "Line 1" in normalized
        assert "Line 2" in normalized
        assert "Line 3" in normalized

    def test_proposal_hash_is_deterministic(self):
        """Test that proposal hash is deterministic."""
        response_text = """PROPOSAL:
Test proposal content.

RATIONALE:
Test rationale content.

CONFIDENCE:
0.90"""

        parser = ResponseParser()
        proposal1 = parser.parse_response("chatgpt", response_text)
        proposal2 = parser.parse_response("chatgpt", response_text)

        assert proposal1.proposal_hash == proposal2.proposal_hash

    def test_different_proposals_have_different_hashes(self):
        """Test that different proposals have different hashes."""
        response1 = """PROPOSAL:
Proposal A

RATIONALE:
Rationale A

CONFIDENCE:
0.80"""

        response2 = """PROPOSAL:
Proposal B

RATIONALE:
Rationale B

CONFIDENCE:
0.80"""

        parser = ResponseParser()
        proposal1 = parser.parse_response("claude", response1)
        proposal2 = parser.parse_response("claude", response2)

        assert proposal1.proposal_hash != proposal2.proposal_hash

    def test_case_sensitive_section_headers(self):
        """Test that section headers are case-sensitive."""
        response_text = """proposal:
Some proposal.

rationale:
Some rationale.

confidence:
0.75"""

        parser = ResponseParser()
        # Should fail because headers must be uppercase
        with pytest.raises(LLMResponseInvalidError):
            parser.parse_response("deepseek", response_text)

    def test_multiline_proposal_and_rationale(self):
        """Test that multiline proposals and rationales are supported."""
        response_text = """PROPOSAL:
Step 1: Validate input
Step 2: Sanitize data
Step 3: Process request

RATIONALE:
This approach ensures:
- Security at boundaries
- Clear error handling
- Maintainable code

CONFIDENCE:
0.92"""

        parser = ResponseParser()
        proposal = parser.parse_response("chatgpt", response_text)

        assert "Step 1" in proposal.proposal_text
        assert "Step 3" in proposal.proposal_text
        assert "Security at boundaries" in proposal.rationale_text
        assert "Maintainable code" in proposal.rationale_text
        assert proposal.confidence == 0.92
