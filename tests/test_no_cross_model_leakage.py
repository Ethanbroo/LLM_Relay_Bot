"""Test Phase 6 Invariant: No cross-model information leakage.

Phase 6 Critical Invariant: Models operate in complete isolation.
No model can see, reference, or be influenced by another model's output.
"""

import pytest
import numpy as np
from orchestration.orchestration_pipeline import OrchestrationPipeline
from orchestration.models import ModelRegistry, ChatGPTModel, ClaudeModel, GeminiModel, DeepSeekModel
from orchestration.response_parser import LLMProposal


class TestNoCrossModelLeakage:
    """Test that models cannot leak information to each other."""

    def setup_method(self):
        """Set up test registry and pipeline."""
        self.registry = ModelRegistry()
        self.registry.register(ChatGPTModel())
        self.registry.register(ClaudeModel())
        self.registry.register(GeminiModel())
        self.registry.register(DeepSeekModel())

        self.audit_events = []

        def audit_callback(event_type: str, metadata: dict):
            self.audit_events.append({"event_type": event_type, "metadata": metadata})

        self.pipeline = OrchestrationPipeline(
            model_registry=self.registry,
            consensus_threshold=0.80,
            similarity_rounding=3,
            escalation_model_id="chatgpt",
            audit_callback=audit_callback,
            run_id="test-run-isolation"
        )

    def test_models_cannot_see_each_others_proposals(self):
        """Test that models generate proposals independently without seeing others."""
        task = "Design security protocol"
        constraints = "Must be zero-trust"

        # Run orchestration
        decision = self.pipeline.orchestrate(
            ["chatgpt", "claude"],
            task,
            constraints
        )

        # Each model should have received only its own prompt
        # Check audit events for LLM_REQUEST_SENT
        request_events = [e for e in self.audit_events if e["event_type"] == "LLM_REQUEST_SENT"]
        assert len(request_events) == 2

        # Each request should have unique prompt_hash
        prompt_hashes = [e["metadata"]["prompt_hash"] for e in request_events]
        assert len(set(prompt_hashes)) == 2, "Each model must receive unique prompt"

    def test_escalation_receives_only_anonymized_data(self):
        """Test that escalation model receives only hashes, not original proposals."""
        task = "Analyze threat model"
        constraints = "Critical systems only"

        # Create scenario that triggers escalation (low similarity)
        # This will happen naturally with stub models if proposals differ enough

        decision = self.pipeline.orchestrate(
            ["chatgpt", "claude", "gemini"],
            task,
            constraints
        )

        # If escalation occurred, check audit events
        escalation_events = [e for e in self.audit_events if e["event_type"] == "ESCALATION_TRIGGERED"]

        if len(escalation_events) > 0:
            # Escalation happened - verify no raw proposal text in audit
            for event in escalation_events:
                metadata_str = str(event["metadata"])
                # Should not contain actual proposal content
                assert "Design" not in metadata_str  # Task words shouldn't appear
                assert "Analyze" not in metadata_str

    def test_consensus_algorithm_uses_only_embeddings(self):
        """Test that consensus uses only numeric embeddings, not text comparison."""
        from orchestration.consensus import ConsensusEngine

        engine = ConsensusEngine(consensus_threshold=0.80)

        # Create proposals with similar hashes but different text
        proposals = [
            LLMProposal("chatgpt", "Proposal text A", "Rationale A", 0.9, "hash_abc"),
            LLMProposal("claude", "Proposal text B", "Rationale B", 0.85, "hash_def")
        ]

        # Similarity matrix based on embeddings only
        similarity_matrix = np.array([
            [1.0, 0.85],
            [0.85, 1.0]
        ])

        # Consensus should be based purely on similarity_matrix, not proposal text
        consensus = engine.check_consensus(proposals, similarity_matrix)
        selected, score = engine.select_proposal(proposals, similarity_matrix)

        # If we manually set high similarity, consensus should be reached
        # regardless of actual text content
        assert consensus is True

    def test_proposal_selection_independent_of_generation_order(self):
        """Test that proposal selection is independent of generation order."""
        from orchestration.consensus import ConsensusEngine

        engine = ConsensusEngine(consensus_threshold=0.80)

        # Same proposals in different orders
        proposals_order1 = [
            LLMProposal("chatgpt", "A", "R", 0.9, "hash1"),
            LLMProposal("claude", "B", "R", 0.85, "hash2"),
            LLMProposal("gemini", "C", "R", 0.88, "hash3")
        ]

        proposals_order2 = [
            LLMProposal("gemini", "C", "R", 0.88, "hash3"),
            LLMProposal("chatgpt", "A", "R", 0.9, "hash1"),
            LLMProposal("claude", "B", "R", 0.85, "hash2")
        ]

        # Same similarity matrix (reordered correspondingly)
        # Order 1: chatgpt=0, claude=1, gemini=2
        similarity_matrix1 = np.array([
            [1.0, 0.80, 0.75],  # chatgpt
            [0.80, 1.0, 0.70],  # claude
            [0.75, 0.70, 1.0]   # gemini
        ])

        # Order 2: gemini=0, chatgpt=1, claude=2
        similarity_matrix2 = np.array([
            [1.0, 0.75, 0.70],  # gemini
            [0.75, 1.0, 0.80],  # chatgpt
            [0.70, 0.80, 1.0]   # claude
        ])

        selected1, score1 = engine.select_proposal(proposals_order1, similarity_matrix1)
        selected2, score2 = engine.select_proposal(proposals_order2, similarity_matrix2)

        # Same proposal should be selected regardless of order
        assert selected1.proposal_hash == selected2.proposal_hash
        assert abs(score1 - score2) < 1e-6

    def test_audit_events_contain_only_hashes(self):
        """Test that audit events contain hashes only, not raw text."""
        task = "Implement authentication"
        constraints = "OAuth2 required"

        decision = self.pipeline.orchestrate(
            ["chatgpt", "claude"],
            task,
            constraints
        )

        # Check all audit events - none should contain raw proposal text
        for event in self.audit_events:
            metadata_str = str(event["metadata"])

            # Should not contain task description words
            assert "authentication" not in metadata_str.lower()
            assert "oauth2" not in metadata_str.lower()

            # Should contain only hashes (if present)
            if "hash" in metadata_str.lower():
                # Hashes are hex strings
                pass  # OK to have hashes

    def test_models_use_deterministic_stubs_without_shared_state(self):
        """Test that model stubs are deterministic but don't share state."""
        model1 = ChatGPTModel()
        model2 = ChatGPTModel()  # Different instance

        prompt = "Test prompt"
        task_hash = "test_hash_123"

        # Same model type, same inputs = same output
        response1a = model1.generate_proposal(prompt, task_hash)
        response1b = model1.generate_proposal(prompt, task_hash)
        assert response1a == response1b, "Same instance must be deterministic"

        # Different instance, same inputs = same output (no instance state)
        response2 = model2.generate_proposal(prompt, task_hash)
        assert response1a == response2, "Different instances must produce same output"

    def test_orchestration_decision_contains_no_raw_prompts(self):
        """Test that OrchestrationDecision does not contain raw prompts."""
        task = "Review code for vulnerabilities"
        constraints = "Focus on OWASP Top 10"

        decision = self.pipeline.orchestrate(
            ["chatgpt", "claude"],
            task,
            constraints
        )

        # Convert decision to dict
        decision_dict = decision.to_dict()
        decision_str = str(decision_dict)

        # Should not contain raw task or constraints
        assert "vulnerabilities" not in decision_str.lower()
        assert "owasp" not in decision_str.lower()

        # Should contain only selected proposal text (if consensus)
        # or no text (if escalated)

    def test_similarity_computation_is_model_agnostic(self):
        """Test that similarity computation doesn't use model identity."""
        from orchestration.scoring import SimilarityScorer

        scorer = SimilarityScorer(similarity_rounding=3)

        # Same text from different models should produce same embedding
        text = "Implement rate limiting"

        embedding1 = scorer.compute_embedding(text)
        embedding2 = scorer.compute_embedding(text)

        assert np.array_equal(embedding1, embedding2)

        # Similarity should be purely based on text content
        similarity = scorer.compute_similarity(embedding1, embedding2)
        assert similarity == 1.0

    def test_pipeline_processes_models_in_deterministic_order(self):
        """Test that pipeline processes models in deterministic order."""
        task = "Design API schema"
        constraints = "RESTful conventions"

        # Run orchestration multiple times
        decision1 = self.pipeline.orchestrate(
            ["chatgpt", "claude", "gemini"],
            task,
            constraints
        )

        # Clear audit events
        self.audit_events.clear()

        decision2 = self.pipeline.orchestrate(
            ["chatgpt", "claude", "gemini"],
            task,
            constraints
        )

        # Should produce same decision (deterministic)
        assert decision1.decision == decision2.decision
        if decision1.decision == "consensus":
            assert decision1.selected_proposal_hash == decision2.selected_proposal_hash

    def test_no_model_can_reference_another_model(self):
        """Test that model outputs cannot reference other models."""
        # This is enforced by prompt construction - verify prompts don't leak model names
        from orchestration.request_builder import LLMRequestBuilder

        builder = LLMRequestBuilder()
        requests = builder.build_requests_for_models(
            ["chatgpt", "claude", "gemini", "deepseek"],
            "Test task",
            "Test constraints"
        )

        # Each request prompt should not contain other model names
        for request in requests:
            prompt_lower = request.prompt.lower()

            # Check that prompt doesn't mention other models
            model_names = ["chatgpt", "claude", "gemini", "deepseek", "gpt", "anthropic", "google"]
            other_models = [m for m in model_names if m != request.model.lower()]

            for other_model in other_models:
                assert other_model not in prompt_lower, \
                    f"Prompt for {request.model} should not mention {other_model}"

    def test_orchestration_decision_is_data_only(self):
        """Test that OrchestrationDecision contains no executable code."""
        task = "Implement logging"
        constraints = "Structured format"

        decision = self.pipeline.orchestrate(
            ["chatgpt", "claude"],
            task,
            constraints
        )

        # Decision should be pure data (dataclass)
        decision_dict = decision.to_dict()

        # Should not contain any code-like strings
        decision_str = str(decision_dict)
        assert "lambda" not in decision_str.lower()
        assert "exec" not in decision_str.lower()
        assert "eval" not in decision_str.lower()
        assert "import" not in decision_str.lower()
        assert "__" not in decision_str  # No dunder methods

        # Should only contain data fields
        assert "schema_id" in decision_dict
        assert "run_id" in decision_dict
        assert "decision" in decision_dict
