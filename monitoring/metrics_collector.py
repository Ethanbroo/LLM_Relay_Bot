"""Metrics collector for Phase 7.

Phase 7 Invariant: All sampling is tick-driven, deterministic, and reproducible.
"""

import time
from typing import Optional, Any, List
from collections import deque
from monitoring.metrics_types import MetricId, ValueType, MetricSource, MetricsRecord, validate_metric_value_type
from monitoring.integration_adapters import (
    SystemMetricsAdapter,
    Phase2Adapter,
    Phase3Adapter,
    Phase4Adapter,
    Phase5Adapter,
    Phase6Adapter
)


class RingBuffer:
    """Fixed-size ring buffer for deterministic aggregation.

    Phase 7 Invariant: Ring buffers are deterministic and have fixed size.
    """

    def __init__(self, size: int):
        """Initialize ring buffer.

        Args:
            size: Fixed buffer size
        """
        self.size = size
        self.buffer = deque(maxlen=size)

    def append(self, value: Any) -> None:
        """Append value to buffer.

        Args:
            value: Value to append
        """
        self.buffer.append(value)

    def sum(self) -> int:
        """Compute sum of all values.

        Returns:
            Sum of buffer values
        """
        return sum(self.buffer)

    def percentile(self, p: float) -> int:
        """Compute percentile via deterministic sorted copy.

        Args:
            p: Percentile (0.0 to 1.0)

        Returns:
            Percentile value
        """
        if len(self.buffer) == 0:
            return 0

        sorted_buffer = sorted(self.buffer)
        index = int(p * (len(sorted_buffer) - 1))
        return sorted_buffer[index]

    def __len__(self) -> int:
        """Get buffer length."""
        return len(self.buffer)


class MetricsCollector:
    """Metrics collector with deterministic tick-driven sampling.

    Phase 7 Invariants:
    - All sampling driven by single tick (no background threads)
    - Ring buffers for per_min aggregation (60 ticks)
    - Deterministic p95 computation via sorted copy
    - seq monotonically increments with no gaps
    """

    def __init__(
        self,
        run_id: str,
        time_policy: str,
        task_queue: Optional[Any] = None,
        engine: Optional[Any] = None,
        log_daemon: Optional[Any] = None,
        coordination: Optional[Any] = None,
        connector_registry: Optional[Any] = None,
        orchestration: Optional[Any] = None,
        latency_buffer_size: int = 200
    ):
        """Initialize metrics collector.

        Args:
            run_id: Run identifier (UUID v7)
            time_policy: "frozen" or "recorded"
            task_queue: TaskQueue instance (Phase 2)
            engine: ExecutionEngine instance (Phase 2)
            log_daemon: LogDaemon instance (Phase 3)
            coordination: CoordinationPipeline instance (Phase 4)
            connector_registry: ConnectorRegistry instance (Phase 5)
            orchestration: OrchestrationPipeline instance (Phase 6)
            latency_buffer_size: Size of latency ring buffer (default 200)
        """
        self.run_id = run_id
        self.time_policy = time_policy
        self.seq = 0  # Monotonic sequence counter
        self.tick_count = 0  # Tick counter

        # Start time for uptime calculation
        self.start_time_ms = int(time.time() * 1000)

        # Initialize adapters
        self.system_adapter = SystemMetricsAdapter(self.start_time_ms)
        self.phase2_adapter = Phase2Adapter(task_queue, engine)
        self.phase3_adapter = Phase3Adapter(log_daemon)
        self.phase4_adapter = Phase4Adapter(coordination)
        self.phase5_adapter = Phase5Adapter(connector_registry)
        self.phase6_adapter = Phase6Adapter(orchestration)

        # Ring buffers for per_min metrics (60 ticks = 60 seconds = 1 minute)
        self.per_min_buffers = {
            MetricId.EXEC_FAILURES_PER_MIN: RingBuffer(60),
            MetricId.EXEC_ROLLBACK_FAILURES_PER_MIN: RingBuffer(60),
            MetricId.LOG_VERIFY_FAILURES_PER_MIN: RingBuffer(60),
            MetricId.DEADLOCK_DETECTED_PER_MIN: RingBuffer(60),
            MetricId.CONN_CALLS_PER_MIN: RingBuffer(60),
            MetricId.CONN_FAILURES_PER_MIN: RingBuffer(60),
            MetricId.CONN_IDEMPOTENCY_HITS_PER_MIN: RingBuffer(60),
            MetricId.CONN_OVERSIZE_REJECTIONS_PER_MIN: RingBuffer(60),
            MetricId.LLM_PARSE_FAILURES_PER_MIN: RingBuffer(60),
            MetricId.LLM_CONSENSUS_FALLBACKS_PER_MIN: RingBuffer(60),
        }

        # Ring buffer for LLM latency p95
        self.latency_buffer = RingBuffer(latency_buffer_size)

    def tick(self, current_time_ms: Optional[int] = None) -> List[MetricsRecord]:
        """Execute one tick of metrics collection.

        Phase 7 Invariant: All metrics collected in single tick, deterministically.

        Args:
            current_time_ms: Current time in milliseconds (or None to use time.time)

        Returns:
            List of MetricsRecord instances
        """
        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)

        self.tick_count += 1
        records = []

        # Collect all metrics
        all_metrics = {}

        # System metrics (every tick)
        all_metrics.update(self.system_adapter.collect(current_time_ms))

        # Phase 2 metrics (every tick)
        all_metrics.update(self.phase2_adapter.collect())

        # Phase 3 metrics (every tick)
        all_metrics.update(self.phase3_adapter.collect())

        # Phase 4 metrics (every tick)
        all_metrics.update(self.phase4_adapter.collect())

        # Phase 5 metrics (every tick)
        all_metrics.update(self.phase5_adapter.collect())

        # Phase 6 metrics (every tick for counters, every 60 ticks for p95)
        phase6_metrics = self.phase6_adapter.collect()
        all_metrics.update(phase6_metrics)

        # Compute LLM latency p95 every 60 ticks (once per minute)
        if self.tick_count % 60 == 0 and len(self.latency_buffer) > 0:
            all_metrics["llm.latency_p95_ms"] = self.latency_buffer.percentile(0.95)

        # Create MetricsRecord for each metric
        for metric_id_str, value in all_metrics.items():
            try:
                metric_id = MetricId(metric_id_str)
                value_type = validate_metric_value_type(metric_id, value)

                # Get timestamp (RFC3339 or null)
                ts = None
                if self.time_policy == "recorded":
                    from datetime import datetime, timezone
                    ts = datetime.fromtimestamp(current_time_ms / 1000, tz=timezone.utc).isoformat()

                record = MetricsRecord(
                    schema_id="relay.metrics_record",
                    schema_version="1.0.0",
                    run_id=self.run_id,
                    ts=ts,
                    time_policy=self.time_policy,
                    seq=self.seq,
                    metric_id=metric_id,
                    value_type=value_type,
                    value=value,
                    source=MetricSource.COLLECTOR,
                    correlation={
                        "trace_id": f"tick_{self.tick_count}",
                        "task_id": None,
                        "event_seq": None
                    }
                )

                records.append(record)
                self.seq += 1

            except (ValueError, KeyError) as e:
                # Unknown metric or type mismatch - skip
                continue

        return records

    def record_llm_latency(self, latency_ms: int) -> None:
        """Record LLM latency for p95 computation.

        Args:
            latency_ms: Latency in milliseconds
        """
        self.latency_buffer.append(latency_ms)

    def increment_counter(self, metric_id: MetricId) -> None:
        """Increment a per-tick counter for per_min aggregation.

        Args:
            metric_id: Metric to increment
        """
        if metric_id in self.per_min_buffers:
            # Get current tick's count or initialize to 0
            if len(self.per_min_buffers[metric_id]) == 0:
                self.per_min_buffers[metric_id].append(0)

            # Increment current tick's count
            current_idx = len(self.per_min_buffers[metric_id]) - 1
            current_value = self.per_min_buffers[metric_id].buffer[current_idx]
            self.per_min_buffers[metric_id].buffer[current_idx] = current_value + 1
