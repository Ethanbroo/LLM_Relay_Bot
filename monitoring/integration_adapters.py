"""Read-only integration adapters for Phase 7 monitoring.

Phase 7 Invariant: Adapters are read-only and cannot modify phases 2-6.
"""

import psutil
import os
import time
from collections import deque
from typing import Optional, Any, Deque
from dataclasses import dataclass, field


@dataclass
class PhaseMetrics:
    """Container for metrics from a specific phase."""
    phase: str
    metrics: dict


class _RateCounter:
    """Thread-safe sliding-window rate counter (events per minute).

    Keeps a deque of event timestamps. Call record() each time an event
    occurs; call rate_per_min(now_ms) to get the count in the last 60 s.
    """

    _WINDOW_MS = 60_000  # 1 minute

    def __init__(self) -> None:
        self._timestamps: Deque[int] = deque()

    def record(self, now_ms: Optional[int] = None) -> None:
        """Record one event at the given timestamp (ms). Defaults to now."""
        ts = now_ms if now_ms is not None else int(time.time() * 1000)
        self._timestamps.append(ts)

    def rate_per_min(self, now_ms: Optional[int] = None) -> int:
        """Return the count of events in the last 60 seconds."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now - self._WINDOW_MS
        # Trim old entries from the left
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        return len(self._timestamps)


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
        self._failures = _RateCounter()
        self._rollback_failures = _RateCounter()

    def record_failure(self, now_ms: Optional[int] = None) -> None:
        """Call this whenever an execution attempt fails."""
        self._failures.record(now_ms)

    def record_rollback_failure(self, now_ms: Optional[int] = None) -> None:
        """Call this whenever a rollback fails."""
        self._rollback_failures.record(now_ms)

    def collect(self) -> dict:
        """Collect Phase 2 metrics."""
        now_ms = int(time.time() * 1000)
        metrics = {}

        if self.task_queue is not None:
            metrics["queue.depth"] = self.task_queue.size()
            # Oldest pending task age — read from queue's oldest_task_enqueued_at if available
            oldest_ms = getattr(self.task_queue, "oldest_enqueued_at_ms", None)
            metrics["queue.oldest_age_ms"] = (now_ms - oldest_ms) if oldest_ms else 0

        if self.engine is not None:
            # Count of attempts currently marked inflight in the engine's state
            inflight = getattr(self.engine, "inflight_count", None)
            metrics["exec.attempts_inflight"] = inflight if inflight is not None else 0

        metrics["exec.failures_per_min"] = self._failures.rate_per_min(now_ms)
        metrics["exec.rollback_failures_per_min"] = self._rollback_failures.rate_per_min(now_ms)

        return metrics


class Phase3Adapter:
    """Read-only adapter for Phase 3 (LogDaemon) metrics."""

    def __init__(self, log_daemon: Optional[Any] = None):
        """Initialize adapter.

        Args:
            log_daemon: LogDaemon instance (optional)
        """
        self.log_daemon = log_daemon
        self._verify_failures = _RateCounter()

    def record_verify_failure(self, now_ms: Optional[int] = None) -> None:
        """Call this whenever a signature verification failure occurs."""
        self._verify_failures.record(now_ms)

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

        metrics["log.verify_failures_per_min"] = self._verify_failures.rate_per_min()

        return metrics


class Phase4Adapter:
    """Read-only adapter for Phase 4 (Coordination) metrics."""

    def __init__(self, coordination: Optional[Any] = None):
        """Initialize adapter.

        Args:
            coordination: CoordinationPipeline instance (optional)
        """
        self.coordination = coordination
        self._deadlocks = _RateCounter()

    def record_deadlock(self, now_ms: Optional[int] = None) -> None:
        """Call this whenever a deadlock is detected."""
        self._deadlocks.record(now_ms)

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

        metrics["deadlock.detected_per_min"] = self._deadlocks.rate_per_min()

        return metrics


class Phase5Adapter:
    """Read-only adapter for Phase 5 (Connectors) metrics."""

    def __init__(self, connector_registry: Optional[Any] = None):
        """Initialize adapter.

        Args:
            connector_registry: ConnectorRegistry instance (optional)
        """
        self.connector_registry = connector_registry
        self._calls = _RateCounter()
        self._failures = _RateCounter()
        self._idempotency_hits = _RateCounter()
        self._oversize_rejections = _RateCounter()

    def record_call(self, now_ms: Optional[int] = None) -> None:
        self._calls.record(now_ms)

    def record_failure(self, now_ms: Optional[int] = None) -> None:
        self._failures.record(now_ms)

    def record_idempotency_hit(self, now_ms: Optional[int] = None) -> None:
        self._idempotency_hits.record(now_ms)

    def record_oversize_rejection(self, now_ms: Optional[int] = None) -> None:
        self._oversize_rejections.record(now_ms)

    def collect(self) -> dict:
        """Collect Phase 5 metrics."""
        now_ms = int(time.time() * 1000)
        return {
            "conn.calls_per_min": self._calls.rate_per_min(now_ms),
            "conn.failures_per_min": self._failures.rate_per_min(now_ms),
            "conn.idempotency_hits_per_min": self._idempotency_hits.rate_per_min(now_ms),
            "conn.oversize_rejections_per_min": self._oversize_rejections.rate_per_min(now_ms),
        }


class Phase6Adapter:
    """Read-only adapter for Phase 6 (Orchestration) metrics."""

    def __init__(self, orchestration: Optional[Any] = None):
        """Initialize adapter.

        Args:
            orchestration: OrchestrationPipeline instance (optional)
        """
        self.orchestration = orchestration
        self._parse_failures = _RateCounter()
        self._consensus_fallbacks = _RateCounter()
        self._latency_samples: Deque[float] = deque(maxlen=1000)

    def record_parse_failure(self, now_ms: Optional[int] = None) -> None:
        """Call this whenever an LLM response fails to parse."""
        self._parse_failures.record(now_ms)

    def record_consensus_fallback(self, now_ms: Optional[int] = None) -> None:
        """Call this whenever consensus fails and escalation is triggered."""
        self._consensus_fallbacks.record(now_ms)

    def record_latency(self, latency_ms: float) -> None:
        """Call this with the round-trip latency for each LLM call."""
        self._latency_samples.append(latency_ms)

    def collect(self) -> dict:
        """Collect Phase 6 metrics.

        Returns:
            Dict of metric_id -> value
        """
        now_ms = int(time.time() * 1000)
        metrics = {}

        metrics["llm.parse_failures_per_min"] = self._parse_failures.rate_per_min(now_ms)
        metrics["llm.consensus_fallbacks_per_min"] = self._consensus_fallbacks.rate_per_min(now_ms)

        # p95 latency from recent samples
        if self._latency_samples:
            import numpy as np
            samples = list(self._latency_samples)
            metrics["llm.latency_p95_ms"] = float(np.percentile(samples, 95))
        else:
            metrics["llm.latency_p95_ms"] = 0.0

        return metrics
