"""Pydantic models for Phase 2 execution."""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime


class ExecutionResult(BaseModel):
    """Output from executor after executing a ValidatedAction."""

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
    )

    run_id: str = Field(
        description="UUID (v4 or v7) - unique identifier for this execution attempt",
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    session_id: str = Field(
        description="UUID (v4 or v7) - groups all attempts for this task",
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    message_id: str = Field(
        description="UUID (v4 or v7) - from original envelope",
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    task_id: str = Field(
        description="Deterministic task identifier",
        pattern=r'^[a-f0-9]{64}$'
    )
    attempt: int = Field(ge=1, le=10, description="Attempt number (1-indexed)")
    action: str = Field(
        description="Action name",
        pattern=r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'
    )
    action_version: str = Field(
        description="Action version (semver)",
        pattern=r'^\d+\.\d+\.\d+$'
    )
    status: Literal["success", "failure", "dead"] = Field(
        description="Execution outcome"
    )
    started_at: str = Field(
        description="ISO 8601 timestamp when execution started"
    )
    finished_at: str = Field(
        description="ISO 8601 timestamp when execution finished"
    )
    retryable: bool = Field(
        description="Can this be retried? Always False if status=dead"
    )
    error_code: Optional[str] = Field(
        default=None,
        description="Error code if status != success"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Human-readable error message if status != success"
    )
    error_details: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured error details"
    )
    artifacts: Optional[dict[str, Any]] = Field(
        default=None,
        description="Action-specific output artifacts"
    )
    snapshot_id: Optional[str] = Field(
        default=None,
        description="Snapshot identifier for rollback",
        pattern=r'^snapshot_[a-f0-9]{64}$'
    )
    rollback_id: Optional[str] = Field(
        default=None,
        description="Rollback operation identifier",
        pattern=r'^rollback_[a-f0-9]{64}$'
    )
    sandbox_id: str = Field(
        description="Sandbox identifier",
        pattern=r'^sandbox_[a-f0-9]{64}$'
    )
    handler_duration_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Handler execution time (ms)"
    )
    total_duration_ms: int = Field(
        ge=0,
        description="Total time from start to finish (ms)"
    )
    resource_usage: Optional[dict[str, int]] = Field(
        default=None,
        description="Resource consumption metrics"
    )
    signature: Optional[str] = Field(
        default=None,
        description="Ed25519 signature (Phase 3)",
        pattern=r'^[a-f0-9]{128}$'
    )

    @field_validator('started_at', 'finished_at')
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Validate ISO 8601 timestamp format."""
        try:
            # Parse to validate format
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError as e:
            raise ValueError(f"Invalid ISO 8601 timestamp: {v}") from e


class ExecutionEvent(BaseModel):
    """Audit event emitted during task execution lifecycle."""

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
    )

    event_id: str = Field(
        description="Deterministic event ID",
        pattern=r'^event_[a-f0-9]{64}$'
    )
    event_type: Literal[
        "TASK_ENQUEUED",
        "TASK_DEQUEUED",
        "TASK_STARTED",
        "SANDBOX_CREATING",
        "SANDBOX_CREATED",
        "SANDBOX_DESTROYED",
        "SNAPSHOT_CREATING",
        "SNAPSHOT_CREATED",
        "SNAPSHOT_FAILED",
        "HANDLER_STARTED",
        "HANDLER_FINISHED",
        "HANDLER_FAILED",
        "HANDLER_TIMEOUT",
        "ROLLBACK_STARTED",
        "ROLLBACK_FINISHED",
        "ROLLBACK_FAILED",
        "TASK_FINISHED",
        "TASK_REQUEUED",
        "TASK_DEAD",
        "ENGINE_STARTED",
        "ENGINE_STOPPED",
        "ENGINE_HALTED"
    ]
    timestamp: str = Field(description="ISO 8601 timestamp")
    run_id: str = Field(
        description="Execution attempt identifier",
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    task_id: str = Field(
        description="Deterministic task identifier",
        pattern=r'^[a-f0-9]{64}$'
    )
    attempt: int = Field(ge=1, le=10, description="Attempt number")
    action: Optional[str] = Field(
        default=None,
        description="Action name",
        pattern=r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Session identifier",
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    message_id: Optional[str] = Field(
        default=None,
        description="Original envelope message_id",
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    event_data: Optional[dict[str, Any]] = Field(
        default=None,
        description="Event-specific structured data"
    )
    error_code: Optional[str] = Field(
        default=None,
        description="Error code for failure events"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message for failure events"
    )
    signature: Optional[str] = Field(
        default=None,
        description="Ed25519 signature (Phase 3)",
        pattern=r'^[a-f0-9]{128}$'
    )

    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Validate ISO 8601 timestamp format."""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError as e:
            raise ValueError(f"Invalid ISO 8601 timestamp: {v}") from e
