"""LLM request envelope builder for Phase 6.

Phase 6 Invariant: Request envelopes are strictly typed and immutable.
"""

from dataclasses import dataclass
from typing import Optional
from orchestration.uuid7 import generate_uuid7
from orchestration.prompts import (
    construct_prompt,
    compute_prompt_hash,
    compute_task_hash,
    compute_constraints_hash
)


@dataclass(frozen=True)
class LLMRequest:
    """LLM request envelope.

    Phase 6 Invariant: No runtime mutation allowed.
    """
    schema_id: str
    schema_version: str
    run_id: str
    model: str
    prompt: str
    prompt_hash: str
    task_hash: str
    constraints_hash: str

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation
        """
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "task_hash": self.task_hash,
            "constraints_hash": self.constraints_hash
        }


class LLMRequestBuilder:
    """Builder for LLM request envelopes.

    Phase 6 Invariant: All requests conform to strict schema.
    """

    def __init__(self, run_id: Optional[str] = None):
        """Initialize request builder.

        Args:
            run_id: Run ID (generates UUID v7 if not provided)
        """
        self.run_id = run_id or generate_uuid7()

    def build_request(
        self,
        model_id: str,
        task_description: str,
        constraints: str
    ) -> LLMRequest:
        """Build LLM request envelope.

        Args:
            model_id: Model identifier
            task_description: Task to accomplish
            constraints: Constraints and requirements

        Returns:
            Immutable LLMRequest

        Raises:
            ValueError: If model_id invalid
        """
        # Construct prompt
        prompt = construct_prompt(model_id, task_description, constraints)

        # Compute hashes
        prompt_hash = compute_prompt_hash(prompt)
        task_hash = compute_task_hash(task_description, constraints)
        constraints_hash = compute_constraints_hash(constraints)

        # Build immutable request
        return LLMRequest(
            schema_id="relay.llm_request",
            schema_version="1.0.0",
            run_id=self.run_id,
            model=model_id,
            prompt=prompt,
            prompt_hash=prompt_hash,
            task_hash=task_hash,
            constraints_hash=constraints_hash
        )

    def build_requests_for_models(
        self,
        model_ids: list[str],
        task_description: str,
        constraints: str
    ) -> list[LLMRequest]:
        """Build requests for multiple models.

        Phase 6 Invariant: Same task, different prompts per model.

        Args:
            model_ids: List of model identifiers
            task_description: Task to accomplish
            constraints: Constraints and requirements

        Returns:
            List of LLMRequest objects

        Raises:
            ValueError: If any model_id invalid
        """
        requests = []
        for model_id in model_ids:
            request = self.build_request(model_id, task_description, constraints)
            requests.append(request)

        return requests
