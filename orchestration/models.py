"""Model registry and base classes for Phase 6.

Phase 6 Invariant: Closed world - only explicitly allowed models.
"""

import hashlib
from typing import Optional, Dict, List
from abc import ABC, abstractmethod
from orchestration.errors import ModelNotAllowedError


# Closed set of allowed models (Phase 6 Invariant #1)
ALLOWED_MODELS = {"chatgpt", "claude", "gemini", "deepseek"}

# Model capabilities (informational only - must not change logic)
MODEL_CAPABILITIES = {
    "chatgpt": ["tie_break", "coordination"],
    "claude": ["analysis", "long_context"],
    "gemini": ["code_generation"],
    "deepseek": ["ideation"]
}


class BaseLLMModel(ABC):
    """Base class for LLM models.

    Phase 6 Invariants:
    - Models are stateless
    - Models are replaceable
    - Models receive prompts, return text
    - Models cannot see other model outputs
    - Models cannot influence routing
    """

    def __init__(self, model_id: str, api_key: Optional[str] = None):
        """Initialize LLM model.

        Args:
            model_id: Model identifier (must be in ALLOWED_MODELS)
            api_key: Optional API key for real integration

        Raises:
            ModelNotAllowedError: If model_id not in allowed set
        """
        if model_id not in ALLOWED_MODELS:
            raise ModelNotAllowedError(model_id)

        self.model_id = model_id
        self.api_key = api_key  # Stored but not used in stub

    @abstractmethod
    def generate_proposal(self, prompt: str, task_hash: str) -> str:
        """Generate proposal from prompt.

        Phase 6 Invariant: This is the ONLY method that interacts with LLM.

        Args:
            prompt: Full prompt (system + user)
            task_hash: Hash of task for deterministic stub responses

        Returns:
            Raw text response from model

        Raises:
            OrchestrationError: If generation fails
        """
        pass

    def _stub_response(self, prompt: str, task_hash: str) -> str:
        """Generate deterministic stub response.

        This is used for testing and will be replaced with real API calls.

        Args:
            prompt: Full prompt
            task_hash: Task hash for determinism

        Returns:
            Deterministic stub response in required format
        """
        # Compute deterministic seed from task_hash and model_id
        seed_str = f"{self.model_id}:{task_hash}"
        seed_hash = hashlib.sha256(seed_str.encode('utf-8')).hexdigest()

        # Extract deterministic values
        confidence = int(seed_hash[:8], 16) % 100 / 100.0  # 0.0-0.99
        proposal_variant = int(seed_hash[8:10], 16) % 3  # 0, 1, or 2

        # Generate deterministic proposal text
        proposals = [
            "Implement feature using modular architecture with clear separation of concerns",
            "Build solution with emphasis on performance optimization and caching strategies",
            "Design system with focus on scalability and fault tolerance patterns"
        ]

        proposal_text = proposals[proposal_variant]

        # Generate deterministic rationale
        rationales = [
            "This approach ensures maintainability and testability while minimizing technical debt",
            "This design prioritizes efficiency and resource utilization for production workloads",
            "This architecture provides resilience and graceful degradation under load"
        ]

        rationale_text = rationales[proposal_variant]

        # Format response in required structure
        response = f"""PROPOSAL:
{proposal_text}

RATIONALE:
{rationale_text}

CONFIDENCE:
{confidence:.2f}"""

        return response


class ChatGPTModel(BaseLLMModel):
    """ChatGPT (OpenAI) model stub.

    TODO: Replace stub with real OpenAI API integration.
    """

    def __init__(self, api_key: Optional[str] = None):
        """Initialize ChatGPT model.

        Args:
            api_key: OpenAI API key (optional, not used in stub)
        """
        super().__init__("chatgpt", api_key)

    def generate_proposal(self, prompt: str, task_hash: str) -> str:
        """Generate proposal using ChatGPT.

        STUB IMPLEMENTATION - Remove when adding real API.

        TODO: Replace with:
        ```python
        import openai
        openai.api_key = self.api_key
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0  # Deterministic
        )
        return response.choices[0].message.content
        ```

        Args:
            prompt: Full prompt
            task_hash: Task hash

        Returns:
            Response text in required format
        """
        # STUB: Return deterministic response
        return self._stub_response(prompt, task_hash)


class ClaudeModel(BaseLLMModel):
    """Claude (Anthropic) model stub.

    TODO: Replace stub with real Anthropic API integration.
    """

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude model.

        Args:
            api_key: Anthropic API key (optional, not used in stub)
        """
        super().__init__("claude", api_key)

    def generate_proposal(self, prompt: str, task_hash: str) -> str:
        """Generate proposal using Claude.

        STUB IMPLEMENTATION - Remove when adding real API.

        TODO: Replace with:
        ```python
        import anthropic
        client = anthropic.Client(api_key=self.api_key)
        response = client.messages.create(
            model="claude-3-opus-20240229",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0  # Deterministic
        )
        return response.content[0].text
        ```

        Args:
            prompt: Full prompt
            task_hash: Task hash

        Returns:
            Response text in required format
        """
        # STUB: Return deterministic response
        return self._stub_response(prompt, task_hash)


class GeminiModel(BaseLLMModel):
    """Gemini (Google) model stub.

    TODO: Replace stub with real Google AI API integration.
    """

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Gemini model.

        Args:
            api_key: Google API key (optional, not used in stub)
        """
        super().__init__("gemini", api_key)

    def generate_proposal(self, prompt: str, task_hash: str) -> str:
        """Generate proposal using Gemini.

        STUB IMPLEMENTATION - Remove when adding real API.

        TODO: Replace with:
        ```python
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.0  # Deterministic
            )
        )
        return response.text
        ```

        Args:
            prompt: Full prompt
            task_hash: Task hash

        Returns:
            Response text in required format
        """
        # STUB: Return deterministic response
        return self._stub_response(prompt, task_hash)


class DeepSeekModel(BaseLLMModel):
    """DeepSeek model stub.

    TODO: Replace stub with real DeepSeek API integration.
    """

    def __init__(self, api_key: Optional[str] = None):
        """Initialize DeepSeek model.

        Args:
            api_key: DeepSeek API key (optional, not used in stub)
        """
        super().__init__("deepseek", api_key)

    def generate_proposal(self, prompt: str, task_hash: str) -> str:
        """Generate proposal using DeepSeek.

        STUB IMPLEMENTATION - Remove when adding real API.

        TODO: Replace with real DeepSeek API integration when available.

        Args:
            prompt: Full prompt
            task_hash: Task hash

        Returns:
            Response text in required format
        """
        # STUB: Return deterministic response
        return self._stub_response(prompt, task_hash)


class ModelRegistry:
    """Registry for LLM models.

    Phase 6 Invariant: Closed world - only registered models allowed.
    """

    def __init__(self):
        """Initialize model registry."""
        self._models: Dict[str, BaseLLMModel] = {}

    def register(self, model: BaseLLMModel) -> None:
        """Register a model.

        Args:
            model: LLM model instance

        Raises:
            ValueError: If model already registered
        """
        if model.model_id in self._models:
            raise ValueError(f"Model {model.model_id} already registered")

        self._models[model.model_id] = model

    def get_model(self, model_id: str) -> BaseLLMModel:
        """Get registered model.

        Args:
            model_id: Model identifier

        Returns:
            LLM model instance

        Raises:
            ModelNotAllowedError: If model not registered
        """
        if model_id not in self._models:
            raise ModelNotAllowedError(model_id)

        return self._models[model_id]

    def is_registered(self, model_id: str) -> bool:
        """Check if model is registered.

        Args:
            model_id: Model identifier

        Returns:
            True if registered, False otherwise
        """
        return model_id in self._models

    def list_models(self) -> List[str]:
        """List all registered model IDs.

        Returns:
            List of model identifiers
        """
        return list(self._models.keys())

    def get_capabilities(self, model_id: str) -> List[str]:
        """Get model capabilities (informational only).

        Phase 6 Invariant: Capabilities must not influence routing logic.

        Args:
            model_id: Model identifier

        Returns:
            List of capability strings

        Raises:
            ModelNotAllowedError: If model not in allowed set
        """
        if model_id not in ALLOWED_MODELS:
            raise ModelNotAllowedError(model_id)

        return MODEL_CAPABILITIES.get(model_id, [])
