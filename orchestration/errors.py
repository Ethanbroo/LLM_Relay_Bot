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


class CodeProposalInvalidError(OrchestrationError):
    """Raised when a CodeDiffProposal fails structural validation.

    Layer 2 Invariant: Structural validation is fail-fast and deterministic.
    Covers size limit violations and malformed diff entries — not path
    traversal (see PathEscapeError) and not intent safety (out of scope).
    """

    def __init__(self, reason: str):
        super().__init__(
            f"Code proposal invalid: {reason}",
            error_code="CODE_PROPOSAL_INVALID"
        )


class PathEscapeError(OrchestrationError):
    """Raised when a diff entry's file_path resolves outside the workspace root.

    Layer 2 Invariant: realpath() resolves symlinks before the startswith check,
    preventing all forms of path traversal including ../../ sequences and
    symlink indirection.
    """

    def __init__(self, file_path: str, workspace_root: str):
        super().__init__(
            f"Path escape detected: {file_path!r} resolves outside workspace root {workspace_root!r}",
            error_code="PATH_ESCAPE"
        )
        self.file_path = file_path
        self.workspace_root = workspace_root


class PromptRegistryError(OrchestrationError):
    """Raised when prompt registry invariants are violated.

    Layer 2 Invariant: Prompt versions are immutable once registered.
    Re-registration with a different hash is a hard error, not a warning.
    """

    def __init__(self, reason: str):
        super().__init__(
            f"Prompt registry error: {reason}",
            error_code="PROMPT_REGISTRY_ERROR"
        )


class CodeGateError(OrchestrationError):
    """Raised when an unexpected internal error occurs in the code gate.

    A gate *failure* (proposal rejected by a stage) is represented by
    CodeGateResult.passed == False. This error is only raised for unexpected
    errors during gate execution itself (e.g. subprocess crash, sandbox error).
    """

    def __init__(self, stage: str, reason: str):
        super().__init__(
            f"Code gate internal error at stage {stage!r}: {reason}",
            error_code="CODE_GATE_ERROR"
        )
        self.stage = stage
