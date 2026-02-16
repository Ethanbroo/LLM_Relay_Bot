"""FIFO task queue for deterministic task execution.

Invariants:
1. Single-consumer (one executor processes tasks sequentially)
2. FIFO ordering (tasks executed in enqueue order)
3. In-memory (Phase 2 - no persistence)
4. Thread-safe (basic locking for future IPC)
5. Deduplication (same task_id → single queue entry)
"""

from collections import deque
from typing import Optional
import threading


class TaskQueueError(Exception):
    """Base exception for task queue errors."""
    pass


class TaskQueue:
    """FIFO task queue with deduplication.

    Thread-safe, single-consumer queue for ValidatedAction tasks.
    """

    def __init__(self):
        """Initialize empty task queue."""
        self._queue: deque = deque()
        self._task_ids_in_queue: set[str] = set()  # For deduplication
        self._lock = threading.Lock()
        self._total_enqueued = 0  # Metrics
        self._total_dequeued = 0

    def enqueue(self, validated_action: dict, task_id: str) -> bool:
        """Enqueue a ValidatedAction for execution.

        Args:
            validated_action: ValidatedAction dict from Phase 1
            task_id: Deterministic task identifier

        Returns:
            True if enqueued, False if duplicate (already in queue)

        Raises:
            TaskQueueError: If input is invalid
        """
        if not isinstance(validated_action, dict):
            raise TaskQueueError("validated_action must be a dict")

        if not isinstance(task_id, str) or len(task_id) != 64:
            raise TaskQueueError(f"Invalid task_id format: {task_id}")

        with self._lock:
            # Deduplication: don't enqueue same task twice
            if task_id in self._task_ids_in_queue:
                return False

            # Add to queue
            task_entry = {
                "task_id": task_id,
                "validated_action": validated_action,
                "enqueued_at": self._total_enqueued  # Sequence number
            }

            self._queue.append(task_entry)
            self._task_ids_in_queue.add(task_id)
            self._total_enqueued += 1

            return True

    def dequeue(self) -> Optional[dict]:
        """Dequeue next task for execution (FIFO).

        Returns:
            Task entry dict with task_id, validated_action, enqueued_at
            None if queue is empty

        Thread-safe: Removes task from queue and deduplication set.
        """
        with self._lock:
            if not self._queue:
                return None

            task_entry = self._queue.popleft()
            task_id = task_entry["task_id"]

            # Remove from deduplication set
            self._task_ids_in_queue.discard(task_id)
            self._total_dequeued += 1

            return task_entry

    def requeue(self, validated_action: dict, task_id: str) -> bool:
        """Re-enqueue a failed task for retry.

        This is the same as enqueue() - retry tasks go to back of queue (FIFO).

        Args:
            validated_action: ValidatedAction dict
            task_id: Task identifier

        Returns:
            True if re-enqueued, False if already in queue
        """
        return self.enqueue(validated_action, task_id)

    def peek(self) -> Optional[dict]:
        """Peek at next task without dequeuing.

        Returns:
            Task entry dict or None if empty
        """
        with self._lock:
            if not self._queue:
                return None
            return self._queue[0]

    def size(self) -> int:
        """Get current queue size.

        Returns:
            Number of tasks in queue
        """
        with self._lock:
            return len(self._queue)

    def is_empty(self) -> bool:
        """Check if queue is empty.

        Returns:
            True if no tasks in queue
        """
        with self._lock:
            return len(self._queue) == 0

    def contains(self, task_id: str) -> bool:
        """Check if task_id is currently in queue.

        Args:
            task_id: Task identifier to check

        Returns:
            True if task is in queue
        """
        with self._lock:
            return task_id in self._task_ids_in_queue

    def clear(self) -> int:
        """Clear all tasks from queue.

        Returns:
            Number of tasks removed
        """
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
            self._task_ids_in_queue.clear()
            return count

    def get_metrics(self) -> dict:
        """Get queue metrics.

        Returns:
            Dict with current_size, total_enqueued, total_dequeued
        """
        with self._lock:
            return {
                "current_size": len(self._queue),
                "total_enqueued": self._total_enqueued,
                "total_dequeued": self._total_dequeued,
                "in_flight": self._total_enqueued - self._total_dequeued - len(self._queue)
            }
