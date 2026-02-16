"""Global lock registry with deterministic acquisition semantics.

Phase 4 Invariants:
- Authoritative locks: no execution without ownership
- Task-bound ownership: locks belong to (task_id, attempt)
- No lock stealing: reassignment only via expiry
- In-memory state: rebuilt from Phase 3 audit ledger on restart
"""

from typing import Optional
from dataclasses import dataclass, field
from collections import deque


@dataclass
class WaitQueueEntry:
    """Entry in lock wait queue."""
    task_id: str
    attempt: int
    enqueue_seq: int  # From task queue


@dataclass
class LockRecord:
    """Canonical lock record.

    Phase 4 Invariant: Only owner may release lock.
    """
    lock_id: str
    owner_task_id: Optional[str] = None  # None = not held
    owner_attempt: Optional[int] = None
    acquired_event_seq: Optional[int] = None  # Event seq when acquired
    expires_event_seq: Optional[int] = None  # TTL-based expiry
    lock_version: int = 0  # Monotonic, increments on each acquisition
    wait_queue: deque[WaitQueueEntry] = field(default_factory=deque)

    def is_held(self) -> bool:
        """Check if lock is currently held."""
        return self.owner_task_id is not None

    def is_expired(self, current_event_seq: int) -> bool:
        """Check if lock has expired.

        Args:
            current_event_seq: Current event_seq from LogDaemon

        Returns:
            True if lock expired, False otherwise
        """
        if not self.is_held():
            return False

        if self.expires_event_seq is None:
            return False

        return current_event_seq >= self.expires_event_seq

    def is_owned_by(self, task_id: str, attempt: int) -> bool:
        """Check if lock is owned by specific task attempt."""
        return (
            self.owner_task_id == task_id and
            self.owner_attempt == attempt
        )


class LockExpiredError(Exception):
    """Raised when attempting to use an expired lock."""
    pass


class LockNotOwnedError(Exception):
    """Raised when attempting to release lock not owned by caller."""
    pass


class LockOrderViolationError(Exception):
    """Raised when lock acquisition order is violated."""
    pass


class LockRegistry:
    """Global lock registry.

    Phase 4 Invariant: Lock registry is in-memory, rebuilt from audit ledger.
    """

    def __init__(self, lock_ttl_events: int = 1000):
        """Initialize lock registry.

        Args:
            lock_ttl_events: TTL in event_seq units (from core.yaml)
        """
        self.locks: dict[str, LockRecord] = {}
        self.lock_ttl_events = lock_ttl_events

        # Track current event_seq (updated from LogDaemon)
        self.current_event_seq = 0

    def update_event_seq(self, event_seq: int):
        """Update current event_seq from LogDaemon.

        Args:
            event_seq: Current event_seq
        """
        self.current_event_seq = event_seq

        # Check for expired locks and release them
        self._release_expired_locks()

    def _release_expired_locks(self):
        """Release all expired locks."""
        for lock_id, lock in list(self.locks.items()):
            if lock.is_expired(self.current_event_seq):
                # Release expired lock
                lock.owner_task_id = None
                lock.owner_attempt = None
                lock.acquired_event_seq = None
                lock.expires_event_seq = None

                # Process wait queue (next waiter can acquire)
                # Note: Actual acquisition happens in acquire_lock_set()

    def get_or_create_lock(self, lock_id: str) -> LockRecord:
        """Get existing lock or create new one.

        Args:
            lock_id: Lock identifier

        Returns:
            LockRecord
        """
        if lock_id not in self.locks:
            self.locks[lock_id] = LockRecord(lock_id=lock_id)

        return self.locks[lock_id]

    def check_lock_availability(
        self,
        lock_ids: list[str]
    ) -> tuple[bool, Optional[str]]:
        """Check if all locks in set are available.

        Args:
            lock_ids: Sorted list of lock IDs

        Returns:
            Tuple of (all_available, first_unavailable_lock_id)
        """
        for lock_id in lock_ids:
            lock = self.get_or_create_lock(lock_id)

            # Check expiry first
            if lock.is_expired(self.current_event_seq):
                continue  # Expired locks are available

            if lock.is_held():
                return False, lock_id

        return True, None

    def acquire_lock_set(
        self,
        lock_ids: list[str],
        task_id: str,
        attempt: int,
        enqueue_seq: int
    ) -> tuple[bool, Optional[str]]:
        """Attempt to acquire a set of locks (all-or-nothing).

        Phase 4 Invariant: All-or-nothing acquisition prevents hold-and-wait deadlocks.

        Args:
            lock_ids: Sorted list of lock IDs
            task_id: Task identifier
            attempt: Attempt number
            enqueue_seq: Enqueue sequence number

        Returns:
            Tuple of (acquired, first_unavailable_lock_id)
                - If acquired=True, all locks acquired
                - If acquired=False, no locks acquired, task added to wait queue

        Raises:
            LockOrderViolationError: If lock_ids not sorted
        """
        # Validate lock order
        if lock_ids != sorted(lock_ids):
            raise LockOrderViolationError(
                f"Lock IDs must be sorted. Got: {lock_ids}, Expected: {sorted(lock_ids)}"
            )

        # Check if all locks available
        all_available, first_unavailable = self.check_lock_availability(lock_ids)

        if not all_available:
            # Cannot acquire - add to wait queue
            unavailable_lock = self.get_or_create_lock(first_unavailable)
            wait_entry = WaitQueueEntry(
                task_id=task_id,
                attempt=attempt,
                enqueue_seq=enqueue_seq
            )

            # Only add if not already in queue
            if not any(
                e.task_id == task_id and e.attempt == attempt
                for e in unavailable_lock.wait_queue
            ):
                unavailable_lock.wait_queue.append(wait_entry)

            return False, first_unavailable

        # All locks available - acquire atomically
        for lock_id in lock_ids:
            lock = self.get_or_create_lock(lock_id)

            # Release if expired
            if lock.is_expired(self.current_event_seq):
                lock.owner_task_id = None
                lock.owner_attempt = None

            # Acquire lock
            lock.owner_task_id = task_id
            lock.owner_attempt = attempt
            lock.acquired_event_seq = self.current_event_seq
            lock.expires_event_seq = self.current_event_seq + self.lock_ttl_events
            lock.lock_version += 1

            # Remove from wait queue if present
            lock.wait_queue = deque([
                e for e in lock.wait_queue
                if not (e.task_id == task_id and e.attempt == attempt)
            ])

        return True, None

    def release_lock_set(
        self,
        lock_ids: list[str],
        task_id: str,
        attempt: int
    ):
        """Release a set of locks.

        Phase 4 Invariant: Only owner may release locks.

        Args:
            lock_ids: List of lock IDs to release
            task_id: Task identifier
            attempt: Attempt number

        Raises:
            LockNotOwnedError: If task does not own all locks
        """
        # Verify ownership of all locks
        for lock_id in lock_ids:
            if lock_id not in self.locks:
                raise LockNotOwnedError(
                    f"Lock {lock_id} not owned by task {task_id} attempt {attempt}"
                )

            lock = self.locks[lock_id]

            if not lock.is_owned_by(task_id, attempt):
                raise LockNotOwnedError(
                    f"Lock {lock_id} not owned by task {task_id} attempt {attempt}. "
                    f"Current owner: {lock.owner_task_id} attempt {lock.owner_attempt}"
                )

        # Release all locks
        for lock_id in lock_ids:
            lock = self.locks[lock_id]
            lock.owner_task_id = None
            lock.owner_attempt = None
            lock.acquired_event_seq = None
            lock.expires_event_seq = None

    def get_lock_owners(self) -> dict[str, tuple[str, int]]:
        """Get current lock ownership map.

        Returns:
            Dict of lock_id -> (owner_task_id, owner_attempt)
        """
        owners = {}
        for lock_id, lock in self.locks.items():
            if lock.is_held() and not lock.is_expired(self.current_event_seq):
                owners[lock_id] = (lock.owner_task_id, lock.owner_attempt)

        return owners

    def get_wait_queue_snapshot(self) -> dict[str, list[tuple[str, int]]]:
        """Get snapshot of all wait queues.

        Returns:
            Dict of lock_id -> [(task_id, attempt), ...]
        """
        wait_queues = {}
        for lock_id, lock in self.locks.items():
            if lock.wait_queue:
                wait_queues[lock_id] = [
                    (entry.task_id, entry.attempt)
                    for entry in lock.wait_queue
                ]

        return wait_queues
