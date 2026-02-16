"""Deadlock detection and resolution.

Phase 4 Invariants:
- Periodic cycle detection in wait-for graph
- Deterministic victim selection
- Victim task is terminated with TASK_ABORTED_DEADLOCK
"""

from typing import Optional
from dataclasses import dataclass

from coordination.deadlock_graph import DeadlockGraph, TaskNode
from coordination.lock_registry import LockRegistry


@dataclass
class DeadlockDetectionResult:
    """Result of deadlock detection."""
    deadlock_detected: bool
    cycle: Optional[list[TaskNode]] = None
    victim: Optional[TaskNode] = None
    detection_event_seq: Optional[int] = None


class DeadlockDetector:
    """Deadlock detector with periodic cycle detection.

    Phase 4 Invariant: Runs after every lock acquisition attempt.
    """

    def __init__(
        self,
        lock_registry: LockRegistry,
        log_daemon=None
    ):
        """Initialize deadlock detector.

        Args:
            lock_registry: LockRegistry instance
            log_daemon: Optional LogDaemon for audit events
        """
        self.lock_registry = lock_registry
        self.log_daemon = log_daemon
        self.graph = DeadlockGraph()

    def rebuild_graph(self):
        """Rebuild wait-for graph from lock registry state.

        Phase 4 Invariant: Graph reflects current lock ownership and wait queues.
        """
        # Clear existing graph
        self.graph.clear()

        # Build graph from lock registry
        for lock_id, lock in self.lock_registry.locks.items():
            # Skip if not held
            if not lock.is_held():
                continue

            holder_task_id = lock.owner_task_id
            holder_attempt = lock.owner_attempt

            # Add edges for all waiters
            for wait_entry in lock.wait_queue:
                # Need to find holder's enqueue_seq (not stored in LockRecord)
                # For now, use 0 as placeholder - will be populated from task queue
                holder_enqueue_seq = 0

                self.graph.add_wait_edge(
                    waiter_task_id=wait_entry.task_id,
                    waiter_attempt=wait_entry.attempt,
                    waiter_enqueue_seq=wait_entry.enqueue_seq,
                    holder_task_id=holder_task_id,
                    holder_attempt=holder_attempt,
                    holder_enqueue_seq=holder_enqueue_seq,
                    blocked_on_lock=lock_id
                )

    def detect_and_resolve(self) -> Optional[DeadlockDetectionResult]:
        """Detect deadlock and select victim for resolution.

        Emits audit events:
        - DEADLOCK_DETECTED
        - DEADLOCK_VICTIM_SELECTED

        Returns:
            DeadlockDetectionResult if deadlock detected, None otherwise
        """
        # Rebuild graph from current state
        self.rebuild_graph()

        # Detect cycle
        cycle = self.graph.detect_cycle()

        if cycle is None:
            return None

        # Deadlock detected
        detection_event_seq = self.lock_registry.current_event_seq

        # Emit DEADLOCK_DETECTED event
        self._emit_audit_event(
            event_type="DEADLOCK_DETECTED",
            payload={
                "detection_event_seq": detection_event_seq,
                "cycle_length": len(cycle),
                "cycle_task_ids": [
                    {"task_id": node.task_id, "attempt": node.attempt}
                    for node in cycle
                ]
            }
        )

        # Select victim
        victim = self.graph.select_victim(cycle)

        # Emit DEADLOCK_VICTIM_SELECTED event
        self._emit_audit_event(
            event_type="DEADLOCK_VICTIM_SELECTED",
            payload={
                "detection_event_seq": detection_event_seq,
                "victim_task_id": victim.task_id,
                "victim_attempt": victim.attempt,
                "victim_enqueue_seq": victim.enqueue_seq,
                "cycle_length": len(cycle),
                "selection_reason": "highest_enqueue_seq_then_lexicographic"
            }
        )

        return DeadlockDetectionResult(
            deadlock_detected=True,
            cycle=cycle,
            victim=victim,
            detection_event_seq=detection_event_seq
        )

    def remove_task_from_graph(self, task_id: str, attempt: int):
        """Remove task from wait-for graph.

        Args:
            task_id: Task identifier
            attempt: Attempt number
        """
        self.graph.remove_task(task_id, attempt)

    def get_wait_chain(self, task_id: str, attempt: int) -> list[tuple[str, str]]:
        """Get wait chain for task.

        Args:
            task_id: Task identifier
            attempt: Attempt number

        Returns:
            List of (holder_task_id, lock_id) pairs
        """
        return self.graph.get_wait_chain(task_id, attempt)

    def get_graph_snapshot(self) -> dict[str, list[dict]]:
        """Get snapshot of wait-for graph.

        Returns:
            Dict representation of graph
        """
        return self.graph.get_graph_snapshot()

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
            actor="deadlock_detector",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload=payload
        )
