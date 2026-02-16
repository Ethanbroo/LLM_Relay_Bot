"""Phase 4 coordination pipeline.

Sits between Phase 1 (Validation) and Phase 2 (Execution).
Provides locks, deadlock detection, and approval workflows.

Data Flow:
ValidatedAction → Phase 4 Pipeline → CoordinatedAction → Execution
"""

from typing import Optional
from dataclasses import dataclass

from coordination.lock_ids import compute_lock_id
from coordination.lock_registry import LockRegistry
from coordination.lock_protocol import LockProtocol, LockAcquisitionResult
from coordination.deadlock_detector import DeadlockDetector, DeadlockDetectionResult
from coordination.approval_gate import ApprovalGate, ApprovalCheckResult
from coordination.approval_registry import ApprovalRegistry
from coordination.approval_tokens import ApprovalTokenVerifier


@dataclass
class ValidatedAction:
    """Output from Phase 1 (Validation).

    This is the input to Phase 4.
    """
    validation_id: str
    task_id: str
    action: str
    action_version: str
    payload: dict
    schema_hash: str
    rbac_rule_id: str
    sender: str
    recipient: str
    message_id: str
    enqueue_seq: int  # From task queue
    attempt: int = 0
    approval_id: Optional[str] = None  # If action requires approval


@dataclass
class CoordinatedAction:
    """Output from Phase 4 (Coordination).

    This is the input to Phase 2 (Execution).
    """
    coordination_id: str  # UUID v7
    validated_action: ValidatedAction
    lock_set_id: Optional[str] = None
    acquired_locks: Optional[list[str]] = None
    approval_verified: bool = False
    coordination_event_seq: int = 0


@dataclass
class CoordinationError:
    """Coordination failure result."""
    error_id: str  # UUID v7
    error_code: str
    message: str
    task_id: str
    action: str
    stage: str  # "lock_acquisition" | "deadlock_detected" | "approval_rejected"


class CoordinationPipeline:
    """Phase 4 coordination pipeline.

    Phase 4 Invariants:
    - All lock operations are all-or-nothing
    - Deadlock detection runs after each lock attempt
    - Approval tokens are single-use
    - All operations emit audit events
    """

    def __init__(
        self,
        lock_registry: LockRegistry,
        approval_registry: ApprovalRegistry,
        approval_verifier: ApprovalTokenVerifier,
        log_daemon=None,
        requires_approval_fn=None
    ):
        """Initialize coordination pipeline.

        Args:
            lock_registry: LockRegistry instance
            approval_registry: ApprovalRegistry instance
            approval_verifier: ApprovalTokenVerifier instance
            log_daemon: Optional LogDaemon for audit events
            requires_approval_fn: Optional function(action, payload) -> bool
        """
        self.lock_registry = lock_registry
        self.approval_registry = approval_registry
        self.log_daemon = log_daemon

        # Initialize components
        self.lock_protocol = LockProtocol(lock_registry, log_daemon)
        self.deadlock_detector = DeadlockDetector(lock_registry, log_daemon)
        self.approval_gate = ApprovalGate(
            approval_registry,
            approval_verifier,
            log_daemon
        )

        # Function to determine if action requires approval
        self.requires_approval_fn = requires_approval_fn or (lambda action, payload: False)

    def coordinate_action(
        self,
        validated_action: ValidatedAction
    ) -> tuple[Optional[CoordinatedAction], Optional[CoordinationError]]:
        """Coordinate action through Phase 4 pipeline.

        Steps:
        1. Check approval (if required)
        2. Compute lock IDs from payload
        3. Attempt lock acquisition
        4. Run deadlock detection if blocked
        5. Return CoordinatedAction or CoordinationError

        Args:
            validated_action: ValidatedAction from Phase 1

        Returns:
            Tuple of (CoordinatedAction, CoordinationError)
            - If successful: (CoordinatedAction, None)
            - If failed: (None, CoordinationError)
        """
        import uuid

        # Generate coordination ID
        coordination_id = str(uuid.uuid4())  # TODO: Use UUID v7 when available

        # Step 1: Check approval (if required)
        requires_approval = self.requires_approval_fn(
            validated_action.action,
            validated_action.payload
        )

        approval_result = self.approval_gate.check_approval(
            action=validated_action.action,
            payload=validated_action.payload,
            approval_id=validated_action.approval_id,
            task_id=validated_action.task_id,
            requires_approval=requires_approval
        )

        if not approval_result.approved:
            # Emit coordination failure event
            self._emit_audit_event(
                event_type="COORDINATION_FAILED",
                payload={
                    "coordination_id": coordination_id,
                    "task_id": validated_action.task_id,
                    "action": validated_action.action,
                    "stage": "approval_check",
                    "error_code": approval_result.error_code,
                    "error_message": approval_result.error_message
                }
            )

            return None, CoordinationError(
                error_id=coordination_id,
                error_code=approval_result.error_code,
                message=approval_result.error_message,
                task_id=validated_action.task_id,
                action=validated_action.action,
                stage="approval_rejected"
            )

        # Step 2: Compute lock IDs from payload
        lock_ids = self._extract_lock_ids(validated_action.action, validated_action.payload)

        # If no locks needed, skip to coordination success
        if not lock_ids:
            self._emit_audit_event(
                event_type="COORDINATION_COMPLETED",
                payload={
                    "coordination_id": coordination_id,
                    "task_id": validated_action.task_id,
                    "action": validated_action.action,
                    "locks_required": False,
                    "approval_verified": approval_result.approved
                }
            )

            return CoordinatedAction(
                coordination_id=coordination_id,
                validated_action=validated_action,
                approval_verified=approval_result.approved,
                coordination_event_seq=self.lock_registry.current_event_seq
            ), None

        # Step 3: Attempt lock acquisition
        lock_ids_sorted = sorted(lock_ids)

        lock_result = self.lock_protocol.request_lock_set(
            lock_ids=lock_ids_sorted,
            task_id=validated_action.task_id,
            attempt=validated_action.attempt,
            enqueue_seq=validated_action.enqueue_seq
        )

        if lock_result.acquired:
            # Success - emit coordination completed event
            self._emit_audit_event(
                event_type="COORDINATION_COMPLETED",
                payload={
                    "coordination_id": coordination_id,
                    "task_id": validated_action.task_id,
                    "action": validated_action.action,
                    "lock_set_id": lock_result.lock_set_id,
                    "lock_ids": lock_ids_sorted,
                    "approval_verified": approval_result.approved,
                    "acquired_event_seq": lock_result.acquired_event_seq,
                    "expires_event_seq": lock_result.expires_event_seq
                }
            )

            return CoordinatedAction(
                coordination_id=coordination_id,
                validated_action=validated_action,
                lock_set_id=lock_result.lock_set_id,
                acquired_locks=lock_ids_sorted,
                approval_verified=approval_result.approved,
                coordination_event_seq=lock_result.acquired_event_seq
            ), None

        # Step 4: Lock acquisition failed - run deadlock detection
        deadlock_result = self.deadlock_detector.detect_and_resolve()

        if deadlock_result and deadlock_result.deadlock_detected:
            # Check if this task is the victim
            if (deadlock_result.victim.task_id == validated_action.task_id and
                deadlock_result.victim.attempt == validated_action.attempt):

                # This task is the deadlock victim - abort it
                self._emit_audit_event(
                    event_type="COORDINATION_FAILED",
                    payload={
                        "coordination_id": coordination_id,
                        "task_id": validated_action.task_id,
                        "action": validated_action.action,
                        "stage": "deadlock_victim",
                        "error_code": "TASK_ABORTED_DEADLOCK",
                        "error_message": f"Task selected as deadlock victim (cycle length: {len(deadlock_result.cycle)})"
                    }
                )

                return None, CoordinationError(
                    error_id=coordination_id,
                    error_code="TASK_ABORTED_DEADLOCK",
                    message=f"Task selected as deadlock victim (cycle length: {len(deadlock_result.cycle)})",
                    task_id=validated_action.task_id,
                    action=validated_action.action,
                    stage="deadlock_detected"
                )

        # Lock acquisition blocked but not deadlock victim - task waits in queue
        # Return error to indicate waiting state
        self._emit_audit_event(
            event_type="COORDINATION_BLOCKED",
            payload={
                "coordination_id": coordination_id,
                "task_id": validated_action.task_id,
                "action": validated_action.action,
                "lock_set_id": lock_result.lock_set_id,
                "blocked_on_lock": lock_result.first_unavailable
            }
        )

        return None, CoordinationError(
            error_id=coordination_id,
            error_code="LOCK_ACQUISITION_BLOCKED",
            message=f"Waiting for lock: {lock_result.first_unavailable}",
            task_id=validated_action.task_id,
            action=validated_action.action,
            stage="lock_acquisition"
        )

    def release_locks(
        self,
        task_id: str,
        attempt: int,
        lock_ids: list[str]
    ):
        """Release locks after task completion.

        Args:
            task_id: Task identifier
            attempt: Attempt number
            lock_ids: List of lock IDs to release
        """
        if not lock_ids:
            return

        self.lock_protocol.release_lock_set(
            lock_ids=sorted(lock_ids),
            task_id=task_id,
            attempt=attempt
        )

    def _extract_lock_ids(self, action: str, payload: dict) -> list[str]:
        """Extract lock IDs from action payload.

        This is action-specific logic. For now, we check for common patterns.

        Args:
            action: Action identifier
            payload: Action payload

        Returns:
            List of lock IDs
        """
        lock_ids = []

        # Extract locks based on action and payload structure
        # This is a simplified version - production would have action-specific logic

        # Example: filesystem.write_file
        if action == "filesystem.write_file" and "path" in payload:
            lock_id = compute_lock_id(
                resource_type="filesystem_path",
                resource_id=payload["path"],
                scope="global"
            )
            lock_ids.append(lock_id)

        # Example: document.update
        if action == "document.update" and "document_id" in payload:
            lock_id = compute_lock_id(
                resource_type="document_id",
                resource_id=payload["document_id"],
                scope="global"
            )
            lock_ids.append(lock_id)

        # Add more action-specific lock extraction here

        return lock_ids

    def _emit_audit_event(self, event_type: str, payload: dict):
        """Emit audit event to LogDaemon.

        Args:
            event_type: Event type
            payload: Event payload
        """
        if self.log_daemon is None:
            return

        self.log_daemon.ingest_event(
            event_type=event_type,
            actor="coordination",
            correlation={"session_id": None, "message_id": None, "task_id": payload.get("task_id")},
            payload=payload
        )
