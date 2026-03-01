"""Typed result from a single pipeline phase execution."""

from dataclasses import dataclass
from typing import Any


@dataclass
class PhaseResult:
    """Result of executing a single pipeline phase."""
    phase_number: int
    phase_name: str
    success: bool

    # LLM response
    raw_output: str = ""
    parsed_json: dict[str, Any] | None = None

    # Session tracking
    session_id: str | None = None

    # Cost
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_used: str = ""

    # Error handling
    error_message: str = ""
    was_retried: bool = False
    retry_count: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
