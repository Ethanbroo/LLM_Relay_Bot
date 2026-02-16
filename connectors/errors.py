"""Connector error definitions.

Phase 5 Invariant: All connector errors are deterministic and non-retryable
unless explicitly marked as retryable by Phase 2 engine.
"""


class ConnectorError(Exception):
    """Base exception for all connector errors."""

    def __init__(self, message: str, error_code: str = "CONNECTOR_ERROR"):
        """Initialize connector error.

        Args:
            message: Error message
            error_code: Error code for categorization
        """
        super().__init__(message)
        self.error_code = error_code


class ConnectorUnknownError(ConnectorError):
    """Raised when connector type is not registered."""
    error_code = "CONNECTOR_UNKNOWN"


class ConnectorInputTooLargeError(ConnectorError):
    """Raised when connector input exceeds size limits."""
    error_code = "CONNECTOR_INPUT_TOO_LARGE"


class PhaseBoundaryViolationError(ConnectorError):
    """Raised when ConnectorRequest missing coordination proof."""
    error_code = "PHASE_BOUNDARY_VIOLATION"


class SecretUnavailableError(ConnectorError):
    """Raised when secret cannot be resolved."""
    error_code = "ERR_SECRET_UNAVAILABLE"


class SecretLeakDetectedError(ConnectorError):
    """Raised when secret pattern detected in output."""
    error_code = "SECRET_LEAK_DETECTED"


class RollbackFailedError(ConnectorError):
    """Raised when rollback cannot be completed."""
    error_code = "ROLLBACK_FAILED"


class ConnectorNotRegisteredError(ConnectorError):
    """Raised when action has no registered connector."""
    error_code = "ERR_CONNECTOR_NOT_REGISTERED"
