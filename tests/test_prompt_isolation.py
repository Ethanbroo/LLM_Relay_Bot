"""Test Phase 6 Invariant: No model can see another model's output.

Phase 6 Critical Invariant: Each LLM receives only its own prompt.
Models operate in complete isolation with no shared state.
"""

import pytest
from orchestration.models import ModelRegistry, ChatGPTModel, ClaudeModel, GeminiModel, DeepSeekModel
from orchestration.request_builder import LLMRequestBuilder
from orchestration.prompts import construct_prompt


class TestPromptIsolation:
    """Test that models cannot see each other's outputs."""

    def test_each_model_receives_unique_prompt(self):
        """Test that each model gets a unique prompt with different system context."""
        task_description = "Analyze security implications"
        constraints = "Must complete in 5 steps"

        builder = LLMRequestBuilder()
        requests = builder.build_requests_for_models(
            ["chatgpt", "claude", "gemini", "deepseek"],
            task_description,
            constraints
        )

        # All requests must have unique prompts
        prompts = [req.prompt for req in requests]
        assert len(prompts) == 4
        assert len(set(prompts)) == 4, "Each model must receive a unique prompt"

        # All requests must have unique prompt hashes
        prompt_hashes = [req.prompt_hash for req in requests]
        assert len(set(prompt_hashes)) == 4, "Each prompt hash must be unique"

    def test_no_model_names_in_prompts(self):
        """Test that prompts do not contain other model names."""
        task_description = "Review code for bugs"
        constraints = "Focus on security"

        builder = LLMRequestBuilder()
        requests = builder.build_requests_for_models(
            ["chatgpt", "claude"],
            task_description,
            constraints
        )

        # ChatGPT prompt must not mention Claude
        chatgpt_request = [r for r in requests if r.model == "chatgpt"][0]
        assert "claude" not in chatgpt_request.prompt.lower()
        assert "gemini" not in chatgpt_request.prompt.lower()
        assert "deepseek" not in chatgpt_request.prompt.lower()

        # Claude prompt must not mention ChatGPT
        claude_request = [r for r in requests if r.model == "claude"][0]
        assert "chatgpt" not in claude_request.prompt.lower()
        assert "gpt" not in claude_request.prompt.lower()
        assert "openai" not in claude_request.prompt.lower()

    def test_models_generate_proposals_independently(self):
        """Test that model outputs are independent (no shared state)."""
        registry = ModelRegistry()
        registry.register(ChatGPTModel())
        registry.register(ClaudeModel())

        task_hash = "test_task_123"
        prompt1 = "Analyze security risk A"
        prompt2 = "Analyze security risk B"

        # Generate proposals from both models
        chatgpt = registry.get_model("chatgpt")
        claude = registry.get_model("claude")

        response1 = chatgpt.generate_proposal(prompt1, task_hash)
        response2 = claude.generate_proposal(prompt2, task_hash)

        # Responses must be different (different models, different prompts)
        assert response1 != response2

        # Generate again with same inputs - must be deterministic
        response1_again = chatgpt.generate_proposal(prompt1, task_hash)
        response2_again = claude.generate_proposal(prompt2, task_hash)

        assert response1 == response1_again, "ChatGPT must be deterministic"
        assert response2 == response2_again, "Claude must be deterministic"

    def test_no_shared_state_between_models(self):
        """Test that models do not share any state."""
        registry = ModelRegistry()
        model1 = ChatGPTModel()
        model2 = ChatGPTModel()

        registry.register(model1)

        # Register another ChatGPT instance - should fail (closed world)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(model2)

    def test_request_builder_creates_independent_requests(self):
        """Test that request builder creates fully independent requests."""
        builder = LLMRequestBuilder(run_id="test-run-123")

        models = ["chatgpt", "claude", "gemini", "deepseek"]
        task_description = "Review proposal X"
        constraints = "Max 3 steps"

        requests = builder.build_requests_for_models(models, task_description, constraints)

        # All requests share same run_id (for traceability)
        run_ids = [req.run_id for req in requests]
        assert len(set(run_ids)) == 1
        assert run_ids[0] == "test-run-123"

        # But have different models
        model_ids = [req.model for req in requests]
        assert set(model_ids) == set(models)

        # And different prompts
        prompts = [req.prompt for req in requests]
        assert len(set(prompts)) == len(models)

        # All share same task_hash (same task)
        task_hashes = [req.task_hash for req in requests]
        assert len(set(task_hashes)) == 1

        # But different prompt_hashes (different prompts)
        prompt_hashes = [req.prompt_hash for req in requests]
        assert len(set(prompt_hashes)) == len(models)

    def test_prompt_construction_is_deterministic(self):
        """Test that prompt construction is deterministic for same inputs."""
        model_id = "chatgpt"
        task = "Analyze code"
        constraints = "Security focus"

        prompt1 = construct_prompt(model_id, task, constraints)
        prompt2 = construct_prompt(model_id, task, constraints)

        assert prompt1 == prompt2, "Prompt construction must be deterministic"

    def test_different_models_get_different_system_prompts(self):
        """Test that different models receive different system prompts."""
        task = "Review design"
        constraints = "Performance focus"

        prompt_chatgpt = construct_prompt("chatgpt", task, constraints)
        prompt_claude = construct_prompt("claude", task, constraints)
        prompt_gemini = construct_prompt("gemini", task, constraints)
        prompt_deepseek = construct_prompt("deepseek", task, constraints)

        # All prompts must be different
        prompts = [prompt_chatgpt, prompt_claude, prompt_gemini, prompt_deepseek]
        assert len(set(prompts)) == 4, "Each model must receive unique system prompt"

        # All must contain the task description
        for prompt in prompts:
            assert task in prompt
            assert constraints in prompt

        # ChatGPT prompt must contain its specific system context
        assert "precise technical advisor" in prompt_chatgpt.lower()

        # Claude prompt must contain its specific system context
        assert "thorough analyst" in prompt_claude.lower()
