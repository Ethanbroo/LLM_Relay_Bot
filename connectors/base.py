"""Base connector interface.

Phase 5 Invariant: All connectors must implement this exact interface.
No additional public methods allowed.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import hashlib
import json

from connectors.results import ConnectorResult, RollbackResult, ExecutionArtifact
from connectors.errors import PhaseBoundaryViolationError, ConnectorInputTooLargeError


@dataclass
class CoordinationProof:
    """Proof that action passed Phase 4 coordination.

    Phase 5 Invariant: ConnectorRequest must include coordination proof.
    """
    coordination_id: str
    lock_ids: Optional[list[str]] = None
    approval_id: Optional[str] = None
    coordination_event_seq: int = 0


@dataclass
class ConnectorRequest:
    """Input to connector.

    Phase 5 Invariant: Must be derived from CoordinatedAction.
    """
    run_id: str
    task_id: str
    attempt: int
    action: str
    action_version: str
    payload_canonical: str  # Canonical JSON string
    payload_hash: str  # SHA-256 hex
    config_hash: str
    principal: str  # Role/subject
    idempotency_key: str
    coordination_proof: CoordinationProof

    @classmethod
    def from_coordinated_action(cls, coordinated_action, run_id: str, config_hash: str):
        """Create ConnectorRequest from CoordinatedAction.

        Args:
            coordinated_action: CoordinatedAction from Phase 4
            run_id: Current run ID
            config_hash: Current config hash

        Returns:
            ConnectorRequest

        Raises:
            PhaseBoundaryViolationError: If coordination proof missing
        """
        # Extract validated action
        validated = coordinated_action.validated_action

        # Verify coordination proof
        if coordinated_action.coordination_id is None:
            raise PhaseBoundaryViolationError(
                "ConnectorRequest missing coordination_id"
            )

        # Create coordination proof
        proof = CoordinationProof(
            coordination_id=coordinated_action.coordination_id,
            lock_ids=coordinated_action.acquired_locks,
            approval_id=validated.approval_id,
            coordination_event_seq=coordinated_action.coordination_event_seq
        )

        # Canonicalize payload
        payload_canonical = json.dumps(
            validated.payload,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True
        )

        # Compute payload hash
        payload_hash = hashlib.sha256(payload_canonical.encode('utf-8')).hexdigest()

        # Compute idempotency key
        idempotency_spec = {
            "run_id": run_id,
            "action": validated.action,
            "action_version": validated.action_version,
            "payload_hash": payload_hash,
            "config_hash": config_hash
        }
        idempotency_canonical = json.dumps(
            idempotency_spec,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True
        )
        idempotency_key = hashlib.sha256(idempotency_canonical.encode('utf-8')).hexdigest()

        return cls(
            run_id=run_id,
            task_id=validated.task_id,
            attempt=validated.attempt,
            action=validated.action,
            action_version=validated.action_version,
            payload_canonical=payload_canonical,
            payload_hash=payload_hash,
            config_hash=config_hash,
            principal=validated.sender,
            idempotency_key=idempotency_key,
            coordination_proof=proof
        )

    def validate_size_limits(self, max_payload_bytes: int, max_nesting_depth: int):
        """Validate size limits.

        Args:
            max_payload_bytes: Maximum payload size
            max_nesting_depth: Maximum JSON nesting depth

        Raises:
            ConnectorInputTooLargeError: If limits exceeded
        """
        # Check payload size
        payload_bytes = len(self.payload_canonical.encode('utf-8'))
        if payload_bytes > max_payload_bytes:
            raise ConnectorInputTooLargeError(
                f"Payload size {payload_bytes} exceeds limit {max_payload_bytes}"
            )

        # Check nesting depth
        payload = json.loads(self.payload_canonical)
        depth = self._get_nesting_depth(payload)
        if depth > max_nesting_depth:
            raise ConnectorInputTooLargeError(
                f"Nesting depth {depth} exceeds limit {max_nesting_depth}"
            )

    @staticmethod
    def _get_nesting_depth(obj, current_depth=0):
        """Get maximum nesting depth of JSON object."""
        if not isinstance(obj, (dict, list)):
            return current_depth

        if isinstance(obj, dict):
            if not obj:
                return current_depth
            return max(
                ConnectorRequest._get_nesting_depth(v, current_depth + 1)
                for v in obj.values()
            )
        else:  # list
            if not obj:
                return current_depth
            return max(
                ConnectorRequest._get_nesting_depth(item, current_depth + 1)
                for item in obj
            )


@dataclass
class ConnectorContext:
    """Context for connector execution.

    Phase 5 Invariant: Context is per-(task_id, attempt).
    """
    task_id: str
    attempt: int
    workspace_root: str  # Enforced workspace boundary
    secrets_provider: Optional[object] = None  # SecretsProvider instance


class BaseConnector(ABC):
    """Base connector interface.

    Phase 5 Invariants:
    - No additional public methods
    - No asyncio.create_task or background threads
    - No internal caching across calls
    - No network unless connector type explicitly allows it
    """

    connector_type: str  # Must be set by subclass

    @abstractmethod
    def connect(self, ctx: ConnectorContext) -> None:
        """Establish connection/initialize connector.

        Phase 5 Invariant: Called once per (task_id, attempt).

        Args:
            ctx: ConnectorContext

        Raises:
            ConnectorError: If connection fails
        """
        pass

    @abstractmethod
    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        """Execute connector operation.

        Phase 5 Invariants:
        - Must be deterministic for same idempotency_key
        - Must not retry internally
        - Must produce ExecutionArtifact for rollback

        Args:
            req: ConnectorRequest

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If execution fails
        """
        pass

    @abstractmethod
    def rollback(
        self,
        req: ConnectorRequest,
        artifact: Optional[ExecutionArtifact]
    ) -> RollbackResult:
        """Rollback connector operation.

        Phase 5 Invariant: Must be verifiable.

        Args:
            req: Original ConnectorRequest
            artifact: ExecutionArtifact from execute (if any)

        Returns:
            RollbackResult

        Raises:
            ConnectorError: If rollback fails
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Clean up connector resources.

        Phase 5 Invariant: Called once per (task_id, attempt).

        Raises:
            ConnectorError: If disconnect fails
        """
        pass
