"""Read-only integration adapters for Phase 7 monitoring.

Phase 7 Invariant: Adapters are read-only and cannot modify phases 2-6.
"""

import psutil
import os
import time
from typing import Optional, Any
from dataclasses import dataclass


@dataclass
class PhaseMetrics:
    """Container for metrics from a specific phase."""
    phase: str
    metrics: dict


class SystemMetricsAdapter:
    """Read-only adapter for system/process metrics."""

    def __init__(self, start_time_ms: int):
        """Initialize adapter.

        Args:
            start_time_ms: Process start time in milliseconds
        """
        self.start_time_ms = start_time_ms
        self.process = psutil.Process(os.getpid())
        self.last_tick_ms = None

    def collect(self, current_time_ms: int) -> dict:
        """Collect system metrics.

        Args:
            current_time_ms: Current time in milliseconds

        Returns:
            Dict of metric_id -> value
        """
        metrics = {}

        # RSS bytes
        mem_info = self.process.memory_info()
        metrics["proc.rss_bytes"] = mem_info.rss

        # CPU percent (over last interval)
        metrics["proc.cpu_percent"] = self.process.cpu_percent(interval=None)

        # Open file descriptors
        try:
            metrics["proc.open_fds"] = self.process.num_fds()
        except AttributeError:
            # Not available on Windows
            metrics["proc.open_fds"] = 0

        # Uptime
        metrics["proc.uptime_ms"] = current_time_ms - self.start_time_ms

        # Tick lag (if we have last tick)
        if self.last_tick_ms is not None:
            expected_interval_ms = 1000  # 1 second
            actual_interval_ms = current_time_ms - self.last_tick_ms
            metrics["loop.tick_lag_ms"] = max(0, actual_interval_ms - expected_interval_ms)
        else:
            metrics["loop.tick_lag_ms"] = 0

        self.last_tick_ms = current_time_ms

        return metrics


class Phase2Adapter:
    """Read-only adapter for Phase 2 (TaskQueue/Execution) metrics."""

    def __init__(self, task_queue: Optional[Any] = None, engine: Optional[Any] = None):
        """Initialize adapter.

        Args:
            task_queue: TaskQueue instance (optional)
            engine: ExecutionEngine instance (optional)
        """
        self.task_queue = task_queue
        self.engine = engine

    def collect(self) -> dict:
        """Collect Phase 2 metrics.

        Returns:
            Dict of metric_id -> value
        """
        metrics = {}

        if self.task_queue is not None:
            # Queue depth
            metrics["queue.depth"] = self.task_queue.size()

            # Oldest age (stub for now)
            metrics["queue.oldest_age_ms"] = 0

        if self.engine is not None:
            # Attempts inflight (stub for now)
            metrics["exec.attempts_inflight"] = 0

        # Per-minute counters (stub - would use ring buffers)
        metrics["exec.failures_per_min"] = 0
        metrics["exec.rollback_failures_per_min"] = 0

        return metrics


class Phase3Adapter:
    """Read-only adapter for Phase 3 (LogDaemon) metrics."""

    def __init__(self, log_daemon: Optional[Any] = None):
        """Initialize adapter.

        Args:
            log_daemon: LogDaemon instance (optional)
        """
        self.log_daemon = log_daemon

    def collect(self) -> dict:
        """Collect Phase 3 metrics.

        Returns:
            Dict of metric_id -> value
        """
        metrics = {}

        if self.log_daemon is not None:
            # Backpressure active
            metrics["log.backpressure_active"] = getattr(
                self.log_daemon, "backpressure_active", False
            )

            # Ingest buffer depth
            ingest_buffer = getattr(self.log_daemon, "ingest_buffer", [])
            metrics["log.ingest_buffer_depth"] = len(ingest_buffer)

            # Last event seq
            metrics["log.last_event_seq"] = getattr(
                self.log_daemon, "last_event_seq", 0
            )
        else:
            metrics["log.backpressure_active"] = False
            metrics["log.ingest_buffer_depth"] = 0
            metrics["log.last_event_seq"] = 0

        # Per-minute counters (stub)
        metrics["log.verify_failures_per_min"] = 0

        return metrics


class Phase4Adapter:
    """Read-only adapter for Phase 4 (Coordination) metrics."""

    def __init__(self, coordination: Optional[Any] = None):
        """Initialize adapter.

        Args:
            coordination: CoordinationPipeline instance (optional)
        """
        self.coordination = coordination

    def collect(self) -> dict:
        """Collect Phase 4 metrics.

        Returns:
            Dict of metric_id -> value
        """
        metrics = {}

        if self.coordination is not None:
            lock_manager = getattr(self.coordination, "lock_manager", None)
            if lock_manager is not None:
                metrics["locks.held_count"] = len(getattr(lock_manager, "held_locks", {}))
                metrics["locks.waiting_count"] = len(getattr(lock_manager, "waiting_queue", []))
            else:
                metrics["locks.held_count"] = 0
                metrics["locks.waiting_count"] = 0

            approval_manager = getattr(self.coordination, "approval_manager", None)
            if approval_manager is not None:
                metrics["approval.pending_count"] = len(
                    getattr(approval_manager, "pending_approvals", {})
                )
            else:
                metrics["approval.pending_count"] = 0
        else:
            metrics["locks.held_count"] = 0
            metrics["locks.waiting_count"] = 0
            metrics["approval.pending_count"] = 0

        # Per-minute counters (stub)
        metrics["deadlock.detected_per_min"] = 0

        return metrics


class Phase5Adapter:
    """Read-only adapter for Phase 5 (Connectors) metrics."""

    def __init__(self, connector_registry: Optional[Any] = None):
        """Initialize adapter.

        Args:
            connector_registry: ConnectorRegistry instance (optional)
        """
        self.connector_registry = connector_registry

    def collect(self) -> dict:
        """Collect Phase 5 metrics.

        Returns:
            Dict of metric_id -> value
        """
        metrics = {}

        # Per-minute counters (stub - would track in registry)
        metrics["conn.calls_per_min"] = 0
        metrics["conn.failures_per_min"] = 0
        metrics["conn.idempotency_hits_per_min"] = 0
        metrics["conn.oversize_rejections_per_min"] = 0

        return metrics


class Phase6Adapter:
    """Read-only adapter for Phase 6 (Orchestration) metrics."""

    def __init__(self, orchestration: Optional[Any] = None):
        """Initialize adapter.

        Args:
            orchestration: OrchestrationPipeline instance (optional)
        """
        self.orchestration = orchestration

    def collect(self) -> dict:
        """Collect Phase 6 metrics.

        Returns:
            Dict of metric_id -> value
        """
        metrics = {}

        # Per-minute counters (stub)
        metrics["llm.parse_failures_per_min"] = 0
        metrics["llm.consensus_fallbacks_per_min"] = 0
        metrics["llm.latency_p95_ms"] = 0

        return metrics
