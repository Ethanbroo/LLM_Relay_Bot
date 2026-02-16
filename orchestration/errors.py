"""Orchestration error definitions.

Phase 6 Invariant: All errors are deterministic and auditable.
"""


class OrchestrationError(Exception):
    """Base exception for orchestration errors."""

    def __init__(self, message: str, error_code: str = "ORCHESTRATION_ERROR"):
        """Initialize orchestration error.

        Args:
            message: Error message
            error_code: Error code for categorization
        """
        super().__init__(message)
        self.error_code = error_code


class ModelNotAllowedError(OrchestrationError):
    """Raised when model identifier not in allowed set."""

    def __init__(self, model: str):
        super().__init__(
            f"Model not allowed: {model}. Must be one of: chatgpt, claude, gemini, deepseek",
            error_code="MODEL_NOT_ALLOWED"
        )


class LLMResponseInvalidError(OrchestrationError):
    """Raised when LLM response doesn't match required format."""

    def __init__(self, reason: str):
        super().__init__(
            f"LLM response invalid: {reason}",
            error_code="LLM_RESPONSE_INVALID"
        )


class ConsensusFailedError(OrchestrationError):
    """Raised when no consensus reached."""

    def __init__(self, reason: str):
        super().__init__(
            f"Consensus failed: {reason}",
            error_code="CONSENSUS_FAILED"
        )


class EscalationRequiredError(OrchestrationError):
    """Raised when escalation is required."""

    def __init__(self, reason: str):
        super().__init__(
            f"Escalation required: {reason}",
            error_code="ESCALATION_REQUIRED"
        )


class ProposalNormalizationError(OrchestrationError):
    """Raised when proposal normalization fails."""

    def __init__(self, reason: str):
        super().__init__(
            f"Proposal normalization failed: {reason}",
            error_code="PROPOSAL_NORMALIZATION_ERROR"
        )
