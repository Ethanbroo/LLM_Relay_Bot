"""Claude API client for Phase 8.

Phase 8 Invariant: Claude is a stateless text transformation system.
"""

import hashlib
import json
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass


class ClaudeClientError(Exception):
    """Base exception for Claude client errors."""
    pass


@dataclass
class ClaudeRequest:
    """Immutable Claude request."""
    run_id: str
    session_id: str
    request_id: str
    task_type: str
    task_payload: dict
    constraints: dict
    output_schema_id: str
    output_schema_json: dict
    context_window_hash: str

    def to_dict(self) -> dict:
        """Convert to dict for API call."""
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "task_type": self.task_type,
            "task_payload": self.task_payload,
            "constraints": self.constraints,
            "output_schema_id": self.output_schema_id,
            "output_schema_json": self.output_schema_json,
            "context_window_hash": self.context_window_hash
        }


@dataclass
class ClaudeResponse:
    """Immutable Claude response."""
    status: str  # "success" or "cannot_comply"
    proposal: Optional[dict]
    reason_code: Optional[str]
    missing_fields: Optional[list]
    conflicting_constraints: Optional[list]
    metadata: Optional[dict]
    response_hash: str

    @staticmethod
    def from_dict(data: dict) -> 'ClaudeResponse':
        """Parse response from dict."""
        # Compute response hash
        response_json = json.dumps(data, sort_keys=True)
        response_hash = hashlib.sha256(response_json.encode('utf-8')).hexdigest()

        status = data.get("status")

        if status == "success":
            return ClaudeResponse(
                status="success",
                proposal=data.get("proposal"),
                reason_code=None,
                missing_fields=None,
                conflicting_constraints=None,
                metadata=data.get("metadata"),
                response_hash=response_hash
            )
        elif status == "cannot_comply":
            return ClaudeResponse(
                status="cannot_comply",
                proposal=None,
                reason_code=data.get("reason_code"),
                missing_fields=data.get("missing_fields", []),
                conflicting_constraints=data.get("conflicting_constraints", []),
                metadata=None,
                response_hash=response_hash
            )
        else:
            raise ClaudeClientError(f"Invalid status: {status}")


class ClaudeClient:
    """Claude API client with deterministic settings.

    Phase 8 Invariants:
    - Temperature fixed
    - Top-p fixed
    - No streaming
    - No tool calls
    - No memory
    - System works without Claude (stub mode)
    """

    # Fixed parameters for determinism
    TEMPERATURE = 0.0
    TOP_P = 1.0
    MAX_TOKENS = 4096

    def __init__(
        self,
        api_key: Optional[str] = None,
        prompts_dir: str = "prompts/claude",
        audit_callback: Optional[Callable] = None,
        stub_mode: bool = True
    ):
        """Initialize Claude client.

        Args:
            api_key: Anthropic API key (optional, for stub mode)
            prompts_dir: Directory containing prompt templates
            audit_callback: Callback for audit events
            stub_mode: Use stub responses instead of real API (default True)
        """
        self.api_key = api_key
        self.prompts_dir = Path(prompts_dir)
        self.audit_callback = audit_callback
        self.stub_mode = stub_mode

        # Load prompts
        self._load_prompts()

    def _load_prompts(self) -> None:
        """Load prompt templates from disk."""
        try:
            with open(self.prompts_dir / "system.txt", 'r') as f:
                self.system_prompt = f.read()

            with open(self.prompts_dir / "task.txt", 'r') as f:
                self.task_prompt_template = f.read()

            with open(self.prompts_dir / "failure.txt", 'r') as f:
                self.failure_prompt_template = f.read()

        except FileNotFoundError as e:
            raise ClaudeClientError(f"Failed to load prompts: {e}")

    def _emit_audit_event(self, event_type: str, metadata: dict) -> None:
        """Emit audit event to Phase 3."""
        if self.audit_callback is not None:
            self.audit_callback(event_type, metadata)

    def _build_prompt(self, request: ClaudeRequest, is_retry: bool = False, rejection_reason: str = "") -> str:
        """Build prompt from request.

        Args:
            request: Claude request
            is_retry: Whether this is a retry after rejection
            rejection_reason: Reason for rejection (if retry)

        Returns:
            Full prompt string
        """
        if is_retry:
            return self.failure_prompt_template.format(
                rejection_reason=rejection_reason
            )
        else:
            return self.task_prompt_template.format(
                run_id=request.run_id,
                session_id=request.session_id,
                request_id=request.request_id,
                task_type=request.task_type,
                task_payload=json.dumps(request.task_payload, indent=2),
                constraints=json.dumps(request.constraints, indent=2),
                output_schema_id=request.output_schema_id,
                output_schema_json=json.dumps(request.output_schema_json, indent=2),
                context_window_hash=request.context_window_hash
            )

    def _stub_response(self, request: ClaudeRequest, is_retry: bool = False) -> dict:
        """Generate deterministic stub response.

        Args:
            request: Claude request
            is_retry: Whether this is a retry

        Returns:
            Stub response dict
        """
        # Deterministic stub based on request_id
        seed = hashlib.sha256(request.request_id.encode('utf-8')).hexdigest()
        seed_int = int(seed[:8], 16)

        if seed_int % 10 == 0:
            # 10% failure rate for testing
            return {
                "status": "cannot_comply",
                "reason_code": "missing_required_data",
                "missing_fields": ["example_field"],
                "conflicting_constraints": []
            }
        else:
            # Success case - return minimal valid proposal
            return {
                "status": "success",
                "proposal": {
                    "result": "stub_response",
                    "request_id": request.request_id
                },
                "metadata": {
                    "request_id": request.request_id,
                    "response_hash": seed
                }
            }

    def generate(self, request: ClaudeRequest, is_retry: bool = False, rejection_reason: str = "") -> ClaudeResponse:
        """Generate response from Claude.

        Phase 8 Invariant: This is the ONLY way to interact with Claude.

        Args:
            request: Claude request
            is_retry: Whether this is a retry after rejection
            rejection_reason: Reason for rejection (if retry)

        Returns:
            Claude response

        Raises:
            ClaudeClientError: If API call fails
        """
        # Build prompt
        prompt = self._build_prompt(request, is_retry, rejection_reason)

        # Compute prompt hash
        prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()

        # Emit audit event
        self._emit_audit_event("LLM_PROMPT_SENT", {
            "request_id": request.request_id,
            "prompt_hash": prompt_hash,
            "is_retry": is_retry
        })

        try:
            if self.stub_mode:
                # Use stub response
                response_dict = self._stub_response(request, is_retry)
            else:
                # TODO: Real Anthropic API call
                # import anthropic
                # client = anthropic.Anthropic(api_key=self.api_key)
                # message = client.messages.create(
                #     model="claude-3-5-sonnet-20241022",
                #     max_tokens=self.MAX_TOKENS,
                #     temperature=self.TEMPERATURE,
                #     top_p=self.TOP_P,
                #     system=self.system_prompt,
                #     messages=[{"role": "user", "content": prompt}]
                # )
                # response_text = message.content[0].text
                # response_dict = json.loads(response_text)
                raise ClaudeClientError("Real API mode not implemented - use stub_mode=True")

            # Parse response
            response = ClaudeResponse.from_dict(response_dict)

            # Emit audit event
            self._emit_audit_event("LLM_RESPONSE_RECEIVED", {
                "request_id": request.request_id,
                "status": response.status,
                "response_hash": response.response_hash
            })

            return response

        except Exception as e:
            self._emit_audit_event("LLM_RESPONSE_REJECTED", {
                "request_id": request.request_id,
                "error": str(e)
            })
            raise ClaudeClientError(f"Claude API call failed: {e}")
