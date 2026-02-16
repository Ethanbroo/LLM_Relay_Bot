"""Lock protocol with Phase 3 audit integration.

Phase 4 Invariants:
- All lock operations emit audit events
- Lock acquisition is all-or-nothing
- Lock expiry detection is event-seq based
- Lock release verifies ownership
"""

from typing import Optional
from dataclasses import dataclass

from coordination.lock_registry import (
    LockRegistry,
    LockExpiredError,
    LockNotOwnedError,
    LockOrderViolationError
)
from coordination.lock_ids import compute_lock_set_id


@dataclass
class LockAcquisitionResult:
    """Result of lock acquisition attempt."""
    acquired: bool
    lock_set_id: str
    task_id: str
    attempt: int
    lock_ids: list[str]
    first_unavailable: Optional[str] = None  # If not acquired
    acquired_event_seq: Optional[int] = None  # If acquired
    expires_event_seq: Optional[int] = None  # If acquired


@dataclass
class LockReleaseResult:
    """Result of lock release."""
    released: bool
    lock_set_id: str
    task_id: str
    attempt: int
    lock_ids: list[str]
    release_event_seq: int


class LockProtocol:
    """High-level lock protocol with audit integration.

    Phase 4 Invariant: All operations emit audit events to LogDaemon.
    """

    def __init__(self, lock_registry: LockRegistry, log_daemon=None):
        """Initialize lock protocol.

        Args:
            lock_registry: LockRegistry instance
            log_daemon: Optional LogDaemon instance for audit events
        """
        self.lock_registry = lock_registry
        self.log_daemon = log_daemon

    def request_lock_set(
        self,
        lock_ids: list[str],
        task_id: str,
        attempt: int,
        enqueue_seq: int
    ) -> LockAcquisitionResult:
        """Request acquisition of lock set.

        Emits audit events:
        - LOCK_SET_REQUESTED (always)
        - LOCK_SET_ACQUIRED (if successful)
        - LOCK_SET_WAITING (if blocked)
        - LOCK_ORDER_VIOLATION (if lock order invalid)

        Args:
            lock_ids: Sorted list of lock IDs to acquire
            task_id: Task identifier
            attempt: Attempt number
            enqueue_seq: Enqueue sequence number from task queue

        Returns:
            LockAcquisitionResult

        Raises:
            LockOrderViolationError: If lock_ids not sorted
        """
        # Compute lock set ID
        lock_set_id = compute_lock_set_id(lock_ids)

        # Emit LOCK_SET_REQUESTED event
        self._emit_audit_event(
            event_type="LOCK_SET_REQUESTED",
            payload={
                "lock_set_id": lock_set_id,
                "lock_ids": lock_ids,
                "task_id": task_id,
                "attempt": attempt,
                "enqueue_seq": enqueue_seq
            }
        )

        try:
            # Attempt acquisition
            acquired, first_unavailable = self.lock_registry.acquire_lock_set(
                lock_ids=lock_ids,
                task_id=task_id,
                attempt=attempt,
                enqueue_seq=enqueue_seq
            )

            if acquired:
                # Success - emit LOCK_SET_ACQUIRED
                current_event_seq = self.lock_registry.current_event_seq
                expires_event_seq = current_event_seq + self.lock_registry.lock_ttl_events

                self._emit_audit_event(
                    event_type="LOCK_SET_ACQUIRED",
                    payload={
                        "lock_set_id": lock_set_id,
                        "lock_ids": lock_ids,
                        "task_id": task_id,
                        "attempt": attempt,
                        "acquired_event_seq": current_event_seq,
                        "expires_event_seq": expires_event_seq,
                        "lock_ttl_events": self.lock_registry.lock_ttl_events
                    }
                )

                return LockAcquisitionResult(
                    acquired=True,
                    lock_set_id=lock_set_id,
                    task_id=task_id,
                    attempt=attempt,
                    lock_ids=lock_ids,
                    acquired_event_seq=current_event_seq,
                    expires_event_seq=expires_event_seq
                )
            else:
                # Blocked - emit LOCK_SET_WAITING
                self._emit_audit_event(
                    event_type="LOCK_SET_WAITING",
                    payload={
                        "lock_set_id": lock_set_id,
                        "lock_ids": lock_ids,
                        "task_id": task_id,
                        "attempt": attempt,
                        "enqueue_seq": enqueue_seq,
                        "blocked_on_lock": first_unavailable
                    }
                )

                return LockAcquisitionResult(
                    acquired=False,
                    lock_set_id=lock_set_id,
                    task_id=task_id,
                    attempt=attempt,
                    lock_ids=lock_ids,
                    first_unavailable=first_unavailable
                )

        except LockOrderViolationError as e:
            # Emit violation event
            self._emit_audit_event(
                event_type="LOCK_ORDER_VIOLATION",
                payload={
                    "lock_set_id": lock_set_id,
                    "lock_ids": lock_ids,
                    "task_id": task_id,
                    "attempt": attempt,
                    "error_message": str(e)
                }
            )
            raise

    def release_lock_set(
        self,
        lock_ids: list[str],
        task_id: str,
        attempt: int
    ) -> LockReleaseResult:
        """Release lock set.

        Emits audit event:
        - LOCK_SET_RELEASED

        Args:
            lock_ids: List of lock IDs to release
            task_id: Task identifier
            attempt: Attempt number

        Returns:
            LockReleaseResult

        Raises:
            LockNotOwnedError: If locks not owned by caller
        """
        # Compute lock set ID
        lock_set_id = compute_lock_set_id(sorted(lock_ids))

        # Release locks
        self.lock_registry.release_lock_set(
            lock_ids=lock_ids,
            task_id=task_id,
            attempt=attempt
        )

        # Emit LOCK_SET_RELEASED event
        release_event_seq = self.lock_registry.current_event_seq

        self._emit_audit_event(
            event_type="LOCK_SET_RELEASED",
            payload={
                "lock_set_id": lock_set_id,
                "lock_ids": sorted(lock_ids),
                "task_id": task_id,
                "attempt": attempt,
                "release_event_seq": release_event_seq
            }
        )

        return LockReleaseResult(
            released=True,
            lock_set_id=lock_set_id,
            task_id=task_id,
            attempt=attempt,
            lock_ids=sorted(lock_ids),
            release_event_seq=release_event_seq
        )

    def check_lock_expiry(
        self,
        lock_ids: list[str],
        task_id: str,
        attempt: int
    ) -> bool:
        """Check if locks are expired.

        Emits audit event:
        - LOCK_EXPIRED (if expired)

        Args:
            lock_ids: List of lock IDs to check
            task_id: Task identifier
            attempt: Attempt number

        Returns:
            True if any lock expired, False otherwise
        """
        current_event_seq = self.lock_registry.current_event_seq
        expired_locks = []

        for lock_id in lock_ids:
            if lock_id not in self.lock_registry.locks:
                continue

            lock = self.lock_registry.locks[lock_id]

            # Check if owned by this task
            if not lock.is_owned_by(task_id, attempt):
                continue

            # Check expiry
            if lock.is_expired(current_event_seq):
                expired_locks.append(lock_id)

        if expired_locks:
            # Emit LOCK_EXPIRED event
            lock_set_id = compute_lock_set_id(sorted(lock_ids))

            self._emit_audit_event(
                event_type="LOCK_EXPIRED",
                payload={
                    "lock_set_id": lock_set_id,
                    "expired_lock_ids": expired_locks,
                    "task_id": task_id,
                    "attempt": attempt,
                    "current_event_seq": current_event_seq
                }
            )

            return True

        return False

    def get_lock_status(
        self,
        lock_ids: list[str]
    ) -> dict[str, dict]:
        """Get status of locks.

        Args:
            lock_ids: List of lock IDs to query

        Returns:
            Dict of lock_id -> status dict
        """
        status = {}

        for lock_id in lock_ids:
            if lock_id not in self.lock_registry.locks:
                status[lock_id] = {
                    "exists": False,
                    "held": False
                }
                continue

            lock = self.lock_registry.locks[lock_id]

            status[lock_id] = {
                "exists": True,
                "held": lock.is_held(),
                "owner_task_id": lock.owner_task_id,
                "owner_attempt": lock.owner_attempt,
                "acquired_event_seq": lock.acquired_event_seq,
                "expires_event_seq": lock.expires_event_seq,
                "expired": lock.is_expired(self.lock_registry.current_event_seq),
                "lock_version": lock.lock_version,
                "wait_queue_size": len(lock.wait_queue)
            }

        return status

    def _emit_audit_event(self, event_type: str, payload: dict):
        """Emit audit event to LogDaemon.

        Args:
            event_type: Event type
            payload: Event payload
        """
        if self.log_daemon is None:
            return  # No audit logging

        self.log_daemon.ingest_event(
            event_type=event_type,
            actor="lock_protocol",
            correlation={"session_id": None, "message_id": None, "task_id": payload.get("task_id")},
            payload=payload
        )
