"""Wait-for graph for deadlock detection.

Phase 4 Invariants:
- Graph represents task wait dependencies
- Cycles indicate deadlocks
- Deterministic victim selection for resolution
"""

from typing import Optional
from dataclasses import dataclass
from collections import defaultdict, deque


@dataclass
class TaskNode:
    """Node in wait-for graph."""
    task_id: str
    attempt: int
    enqueue_seq: int

    def __hash__(self):
        return hash((self.task_id, self.attempt))

    def __eq__(self, other):
        if not isinstance(other, TaskNode):
            return False
        return self.task_id == other.task_id and self.attempt == other.attempt

    def __lt__(self, other):
        """Comparison for deterministic victim selection.

        Victim selection priority:
        1. Highest enqueue_seq (newest task)
        2. Lexicographically highest task_id (tie-breaker)
        """
        if not isinstance(other, TaskNode):
            return NotImplemented

        # Higher enqueue_seq comes first (is "less than" for max selection)
        if self.enqueue_seq != other.enqueue_seq:
            return self.enqueue_seq > other.enqueue_seq

        # Lexicographically higher task_id comes first
        return self.task_id > other.task_id


@dataclass
class WaitEdge:
    """Edge in wait-for graph: waiter → holder."""
    waiter: TaskNode
    holder: TaskNode
    blocked_on_lock: str  # Lock ID causing the wait


class DeadlockGraph:
    """Wait-for graph for deadlock detection.

    Phase 4 Invariant: Graph represents current lock wait dependencies.
    """

    def __init__(self):
        """Initialize empty graph."""
        # Adjacency list: waiter -> list[(holder, lock_id)]
        self.edges: dict[TaskNode, list[tuple[TaskNode, str]]] = defaultdict(list)

        # Reverse index: holder -> list[waiter]
        self.reverse_edges: dict[TaskNode, list[TaskNode]] = defaultdict(list)

        # All nodes in graph
        self.nodes: set[TaskNode] = set()

    def add_wait_edge(
        self,
        waiter_task_id: str,
        waiter_attempt: int,
        waiter_enqueue_seq: int,
        holder_task_id: str,
        holder_attempt: int,
        holder_enqueue_seq: int,
        blocked_on_lock: str
    ):
        """Add wait edge: waiter is blocked waiting for holder.

        Args:
            waiter_task_id: Task ID of waiting task
            waiter_attempt: Attempt number of waiting task
            waiter_enqueue_seq: Enqueue seq of waiting task
            holder_task_id: Task ID of lock holder
            holder_attempt: Attempt number of lock holder
            holder_enqueue_seq: Enqueue seq of lock holder
            blocked_on_lock: Lock ID causing the wait
        """
        waiter = TaskNode(waiter_task_id, waiter_attempt, waiter_enqueue_seq)
        holder = TaskNode(holder_task_id, holder_attempt, holder_enqueue_seq)

        # Add to adjacency list
        self.edges[waiter].append((holder, blocked_on_lock))

        # Add to reverse index
        self.reverse_edges[holder].append(waiter)

        # Add nodes
        self.nodes.add(waiter)
        self.nodes.add(holder)

    def remove_task(self, task_id: str, attempt: int):
        """Remove all edges involving a task.

        Args:
            task_id: Task identifier
            attempt: Attempt number
        """
        node = TaskNode(task_id, attempt, 0)  # enqueue_seq not needed for equality

        # Remove from edges (as waiter)
        if node in self.edges:
            # Remove from reverse edges
            for holder, _ in self.edges[node]:
                if holder in self.reverse_edges:
                    self.reverse_edges[holder] = [
                        w for w in self.reverse_edges[holder]
                        if not (w.task_id == task_id and w.attempt == attempt)
                    ]
            del self.edges[node]

        # Remove from edges (as holder)
        if node in self.reverse_edges:
            # Remove from forward edges
            for waiter in self.reverse_edges[node]:
                if waiter in self.edges:
                    self.edges[waiter] = [
                        (h, lock_id) for h, lock_id in self.edges[waiter]
                        if not (h.task_id == task_id and h.attempt == attempt)
                    ]
            del self.reverse_edges[node]

        # Remove from nodes
        self.nodes = {
            n for n in self.nodes
            if not (n.task_id == task_id and n.attempt == attempt)
        }

    def detect_cycle(self) -> Optional[list[TaskNode]]:
        """Detect cycle in graph using DFS.

        Returns:
            List of TaskNodes in cycle, or None if no cycle
        """
        visited = set()
        rec_stack = set()
        parent = {}

        def dfs(node: TaskNode) -> Optional[list[TaskNode]]:
            """DFS helper to detect cycle."""
            visited.add(node)
            rec_stack.add(node)

            # Visit all neighbors
            if node in self.edges:
                for neighbor, _ in self.edges[node]:
                    if neighbor not in visited:
                        parent[neighbor] = node
                        cycle = dfs(neighbor)
                        if cycle:
                            return cycle
                    elif neighbor in rec_stack:
                        # Found cycle - reconstruct path
                        cycle = [neighbor]
                        current = node
                        while current != neighbor:
                            cycle.append(current)
                            current = parent.get(current)
                            if current is None:
                                break
                        cycle.reverse()
                        return cycle

            rec_stack.remove(node)
            return None

        # Try DFS from each unvisited node
        for node in self.nodes:
            if node not in visited:
                cycle = dfs(node)
                if cycle:
                    return cycle

        return None

    def select_victim(self, cycle: list[TaskNode]) -> TaskNode:
        """Select victim task from cycle for termination.

        Phase 4 Invariant: Deterministic victim selection.
        Selection priority:
        1. Highest enqueue_seq (newest task in cycle)
        2. Lexicographically highest task_id (tie-breaker)

        Args:
            cycle: List of TaskNodes in deadlock cycle

        Returns:
            TaskNode to terminate
        """
        if not cycle:
            raise ValueError("Cannot select victim from empty cycle")

        # Sort by TaskNode.__lt__ (highest enqueue_seq, then highest task_id)
        return min(cycle)

    def get_wait_chain(self, task_id: str, attempt: int) -> list[tuple[str, str]]:
        """Get wait chain for a task.

        Args:
            task_id: Task identifier
            attempt: Attempt number

        Returns:
            List of (task_id, lock_id) pairs showing wait chain
        """
        node = TaskNode(task_id, attempt, 0)

        if node not in self.edges:
            return []

        chain = []
        visited = set()
        current = node

        while current in self.edges and current not in visited:
            visited.add(current)

            if not self.edges[current]:
                break

            # Get first holder (arbitrary if multiple)
            holder, lock_id = self.edges[current][0]
            chain.append((holder.task_id, lock_id))
            current = holder

        return chain

    def get_graph_snapshot(self) -> dict[str, list[dict]]:
        """Get snapshot of graph for debugging/audit.

        Returns:
            Dict of task_id -> list of wait edges
        """
        snapshot = {}

        for waiter in self.edges:
            waiter_key = f"{waiter.task_id}:{waiter.attempt}"
            snapshot[waiter_key] = []

            for holder, lock_id in self.edges[waiter]:
                snapshot[waiter_key].append({
                    "holder_task_id": holder.task_id,
                    "holder_attempt": holder.attempt,
                    "blocked_on_lock": lock_id
                })

        return snapshot

    def clear(self):
        """Clear all edges and nodes."""
        self.edges.clear()
        self.reverse_edges.clear()
        self.nodes.clear()
