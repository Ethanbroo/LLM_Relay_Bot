"""Test Phase 6 escalation logic and paths.

Phase 6 Invariant: Escalation paths are closed and finite.
Only three possible escalation decisions: ABORT, REQUEST_HUMAN, REPROMPT_LATER.
"""

import pytest
import numpy as np
from orchestration.escalation import (
    EscalationEngine,
    EscalationReason,
    EscalationDecision,
    should_escalate
)
from orchestration.response_parser import LLMProposal
from orchestration.models import ChatGPTModel, ModelRegistry


class TestEscalationPaths:
    """Test escalation decision logic."""

    def test_escalation_required_for_no_consensus(self):
        """Test that escalation is required when no consensus exists."""
        proposals = [
            LLMProposal("chatgpt", "Proposal A", "Rationale A", 0.9, "hash1"),
            LLMProposal("claude", "Proposal B", "Rationale B", 0.85, "hash2")
        ]
        consensus_exists = False
        max_similarity = 0.65
        threshold = 0.80

        needs_escalation, reason = should_escalate(
            proposals, consensus_exists, max_similarity, threshold
        )

        assert needs_escalation is True
        assert reason == EscalationReason.LOW_SIMILARITY

    def test_no_escalation_when_consensus_exists(self):
        """Test that no escalation when consensus exists."""
        proposals = [
            LLMProposal("chatgpt", "Proposal X", "Rationale X", 0.9, "hash_x"),
            LLMProposal("claude", "Proposal X similar", "Rationale Y", 0.88, "hash_y")
        ]
        consensus_exists = True
        max_similarity = 0.95
        threshold = 0.80

        needs_escalation, reason = should_escalate(
            proposals, consensus_exists, max_similarity, threshold
        )

        assert needs_escalation is False
        assert reason is None

    def test_escalation_for_all_invalid_proposals(self):
        """Test escalation when all proposals are invalid (empty list)."""
        proposals = []
        consensus_exists = False
        max_similarity = 0.0
        threshold = 0.80

        needs_escalation, reason = should_escalate(
            proposals, consensus_exists, max_similarity, threshold
        )

        assert needs_escalation is True
        assert reason == EscalationReason.ALL_INVALID

    def test_escalation_reason_low_similarity(self):
        """Test LOW_SIMILARITY reason when similarity below threshold."""
        proposals = [
            LLMProposal("gemini", "Proposal A", "Rationale A", 0.9, "hash_a"),
            LLMProposal("deepseek", "Proposal B", "Rationale B", 0.85, "hash_b")
        ]
        consensus_exists = False
        max_similarity = 0.70  # Below threshold
        threshold = 0.80

        needs_escalation, reason = should_escalate(
            proposals, consensus_exists, max_similarity, threshold
        )

        assert needs_escalation is True
        assert reason == EscalationReason.LOW_SIMILARITY

    def test_escalation_reason_no_consensus(self):
        """Test NO_CONSENSUS reason when similarity above threshold but no consensus."""
        proposals = [
            LLMProposal("chatgpt", "A", "R", 0.9, "h1"),
            LLMProposal("claude", "B", "R", 0.85, "h2"),
            LLMProposal("gemini", "C", "R", 0.88, "h3")
        ]
        consensus_exists = False
        max_similarity = 0.85  # Above threshold
        threshold = 0.80

        needs_escalation, reason = should_escalate(
            proposals, consensus_exists, max_similarity, threshold
        )

        assert needs_escalation is True
        assert reason == EscalationReason.NO_CONSENSUS

    def test_escalation_engine_requires_chatgpt(self):
        """Test that escalation engine only accepts chatgpt model."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        chatgpt_model = registry.get_model("chatgpt")

        # Should succeed with chatgpt
        engine = EscalationEngine(chatgpt_model)
        assert engine.model.model_id == "chatgpt"

    def test_escalation_engine_rejects_non_chatgpt(self):
        """Test that escalation engine rejects non-chatgpt models."""
        from orchestration.models import ClaudeModel

        registry = ModelRegistry()
        registry.register(ClaudeModel())
        claude_model = registry.get_model("claude")

        # Should fail with non-chatgpt model
        with pytest.raises(ValueError, match="Escalation model must be chatgpt"):
            EscalationEngine(claude_model)

    def test_escalation_produces_valid_decision(self):
        """Test that escalation produces one of three valid decisions."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        proposals = [
            LLMProposal("claude", "Prop A", "Rat A", 0.8, "hash_a")
        ]
        similarity_matrix = [[1.0]]

        decision = engine.escalate(
            EscalationReason.NO_CONSENSUS,
            proposals,
            similarity_matrix
        )

        # Must be one of three valid decisions
        assert decision in [
            EscalationDecision.ABORT,
            EscalationDecision.REQUEST_HUMAN,
            EscalationDecision.REPROMPT_LATER
        ]

    def test_escalation_prompt_is_anonymized(self):
        """Test that escalation prompt does not contain model names."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        # Create proposals with distinct model names
        proposals = [
            LLMProposal("claude", "Proposal from Claude", "Rationale", 0.9, "claude_hash_123"),
            LLMProposal("gemini", "Proposal from Gemini", "Rationale", 0.85, "gemini_hash_456")
        ]

        # Build anonymized prompt (internal method test)
        prompt = engine._build_escalation_prompt(
            EscalationReason.NO_CONSENSUS,
            proposals,
            [[1.0, 0.75], [0.75, 1.0]]
        )

        # Prompt should NOT contain original model names
        assert "claude" not in prompt.lower()
        assert "gemini" not in prompt.lower()

        # Prompt should contain anonymized identifiers
        assert "proposal_0" in prompt
        assert "proposal_1" in prompt

        # Prompt should contain hash prefixes (first 8 chars only)
        import re
        
        hashes = re.findall(r"'hash': '[0-9a-f]{8}'", prompt)
        assert len(hashes) == 2

    def test_escalation_decision_parsing_abort(self):
        """Test parsing of ESCALATE_ABORT decision."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        # Test responses that should parse to ABORT
        responses = [
            "ESCALATE_ABORT",
            "escalate_abort",
            "I recommend: ESCALATE_ABORT",
            "The best option is ESCALATE_ABORT here."
        ]

        for response in responses:
            decision = engine._parse_escalation_decision(response)
            assert decision == EscalationDecision.ABORT, f"Failed to parse: {response}"

    def test_escalation_decision_parsing_request_human(self):
        """Test parsing of ESCALATE_REQUEST_HUMAN decision."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        responses = [
            "ESCALATE_REQUEST_HUMAN",
            "escalate_request_human",
            "Response: ESCALATE_REQUEST_HUMAN is needed"
        ]

        for response in responses:
            decision = engine._parse_escalation_decision(response)
            assert decision == EscalationDecision.REQUEST_HUMAN

    def test_escalation_decision_parsing_reprompt_later(self):
        """Test parsing of ESCALATE_REPROMPT_LATER decision."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        responses = [
            "ESCALATE_REPROMPT_LATER",
            "escalate_reprompt_later",
            "Suggest: ESCALATE_REPROMPT_LATER"
        ]

        for response in responses:
            decision = engine._parse_escalation_decision(response)
            assert decision == EscalationDecision.REPROMPT_LATER

    def test_escalation_decision_default_to_request_human(self):
        """Test that unclear responses default to REQUEST_HUMAN."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        # Ambiguous or invalid responses should default to REQUEST_HUMAN
        unclear_responses = [
            "I'm not sure what to do",
            "Maybe we should wait",
            "This is unclear",
            "",
            "INVALID_DECISION"
        ]

        for response in unclear_responses:
            decision = engine._parse_escalation_decision(response)
            assert decision == EscalationDecision.REQUEST_HUMAN, \
                f"Should default to REQUEST_HUMAN for: {response}"

    def test_escalation_with_empty_proposals(self):
        """Test escalation when proposals list is empty."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        # Empty proposals
        decision = engine.escalate(
            EscalationReason.ALL_INVALID,
            [],
            None
        )

        # Should return a valid decision
        assert decision in [
            EscalationDecision.ABORT,
            EscalationDecision.REQUEST_HUMAN,
            EscalationDecision.REPROMPT_LATER
        ]

    def test_escalation_deterministic_for_same_input(self):
        """Test that escalation is deterministic for same input."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        proposals = [
            LLMProposal("claude", "Test proposal", "Test rationale", 0.85, "test_hash")
        ]
        similarity_matrix = [[1.0]]

        # Run escalation twice with same inputs
        decision1 = engine.escalate(
            EscalationReason.LOW_SIMILARITY,
            proposals,
            similarity_matrix
        )

        decision2 = engine.escalate(
            EscalationReason.LOW_SIMILARITY,
            proposals,
            similarity_matrix
        )

        assert decision1 == decision2, "Escalation must be deterministic"

    def test_escalation_prompt_contains_required_options(self):
        """Test that escalation prompt lists all three decision options."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        escalation_model = registry.get_model("chatgpt")

        engine = EscalationEngine(escalation_model)

        prompt = engine._build_escalation_prompt(
            EscalationReason.NO_CONSENSUS,
            [],
            None
        )

        # Prompt must list all three options
        assert "ESCALATE_ABORT" in prompt
        assert "ESCALATE_REQUEST_HUMAN" in prompt
        assert "ESCALATE_REPROMPT_LATER" in prompt

        # Prompt must specify "EXACTLY ONE"
        assert "EXACTLY ONE" in prompt or "exactly one" in prompt.lower()
