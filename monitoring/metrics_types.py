"""Metrics types and closed enum for Phase 7.

Phase 7 Invariant: Closed-world metrics - no additions without schema update.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Union, Optional


class MetricId(str, Enum):
    """Closed enum of all Phase 7 metrics.

    Phase 7 Hard Rule: No additional metrics without updating:
    - This enum
    - schemas/metrics_record.json
    - schemas/threshold_rule.json
    - Tests
    """

    # System / Process
    PROC_RSS_BYTES = "proc.rss_bytes"
    PROC_CPU_PERCENT = "proc.cpu_percent"
    PROC_OPEN_FDS = "proc.open_fds"
    PROC_UPTIME_MS = "proc.uptime_ms"
    LOOP_TICK_LAG_MS = "loop.tick_lag_ms"

    # Phase 2 - TaskQueue / Execution
    QUEUE_DEPTH = "queue.depth"
    QUEUE_OLDEST_AGE_MS = "queue.oldest_age_ms"
    EXEC_ATTEMPTS_INFLIGHT = "exec.attempts_inflight"
    EXEC_FAILURES_PER_MIN = "exec.failures_per_min"
    EXEC_ROLLBACK_FAILURES_PER_MIN = "exec.rollback_failures_per_min"

    # Phase 3 - LogDaemon
    LOG_BACKPRESSURE_ACTIVE = "log.backpressure_active"
    LOG_INGEST_BUFFER_DEPTH = "log.ingest_buffer_depth"
    LOG_LAST_EVENT_SEQ = "log.last_event_seq"
    LOG_VERIFY_FAILURES_PER_MIN = "log.verify_failures_per_min"

    # Phase 4 - Coordination
    LOCKS_HELD_COUNT = "locks.held_count"
    LOCKS_WAITING_COUNT = "locks.waiting_count"
    DEADLOCK_DETECTED_PER_MIN = "deadlock.detected_per_min"
    APPROVAL_PENDING_COUNT = "approval.pending_count"

    # Phase 5 - Connectors
    CONN_CALLS_PER_MIN = "conn.calls_per_min"
    CONN_FAILURES_PER_MIN = "conn.failures_per_min"
    CONN_IDEMPOTENCY_HITS_PER_MIN = "conn.idempotency_hits_per_min"
    CONN_OVERSIZE_REJECTIONS_PER_MIN = "conn.oversize_rejections_per_min"

    # Phase 6 - Orchestration
    LLM_PARSE_FAILURES_PER_MIN = "llm.parse_failures_per_min"
    LLM_CONSENSUS_FALLBACKS_PER_MIN = "llm.consensus_fallbacks_per_min"
    LLM_LATENCY_P95_MS = "llm.latency_p95_ms"


class ValueType(str, Enum):
    """Metric value types (closed enum)."""
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"


class MetricSource(str, Enum):
    """Metric source (closed enum)."""
    COLLECTOR = "collector"
    RULES_ENGINE = "rules_engine"
    RECOVERY_CONTROLLER = "recovery_controller"


# Metric metadata: defines expected type for each metric
METRIC_METADATA = {
    # System / Process
    MetricId.PROC_RSS_BYTES: ValueType.INT,
    MetricId.PROC_CPU_PERCENT: ValueType.FLOAT,
    MetricId.PROC_OPEN_FDS: ValueType.INT,
    MetricId.PROC_UPTIME_MS: ValueType.INT,
    MetricId.LOOP_TICK_LAG_MS: ValueType.INT,

    # Phase 2
    MetricId.QUEUE_DEPTH: ValueType.INT,
    MetricId.QUEUE_OLDEST_AGE_MS: ValueType.INT,
    MetricId.EXEC_ATTEMPTS_INFLIGHT: ValueType.INT,
    MetricId.EXEC_FAILURES_PER_MIN: ValueType.INT,
    MetricId.EXEC_ROLLBACK_FAILURES_PER_MIN: ValueType.INT,

    # Phase 3
    MetricId.LOG_BACKPRESSURE_ACTIVE: ValueType.BOOL,
    MetricId.LOG_INGEST_BUFFER_DEPTH: ValueType.INT,
    MetricId.LOG_LAST_EVENT_SEQ: ValueType.INT,
    MetricId.LOG_VERIFY_FAILURES_PER_MIN: ValueType.INT,

    # Phase 4
    MetricId.LOCKS_HELD_COUNT: ValueType.INT,
    MetricId.LOCKS_WAITING_COUNT: ValueType.INT,
    MetricId.DEADLOCK_DETECTED_PER_MIN: ValueType.INT,
    MetricId.APPROVAL_PENDING_COUNT: ValueType.INT,

    # Phase 5
    MetricId.CONN_CALLS_PER_MIN: ValueType.INT,
    MetricId.CONN_FAILURES_PER_MIN: ValueType.INT,
    MetricId.CONN_IDEMPOTENCY_HITS_PER_MIN: ValueType.INT,
    MetricId.CONN_OVERSIZE_REJECTIONS_PER_MIN: ValueType.INT,

    # Phase 6
    MetricId.LLM_PARSE_FAILURES_PER_MIN: ValueType.INT,
    MetricId.LLM_CONSENSUS_FALLBACKS_PER_MIN: ValueType.INT,
    MetricId.LLM_LATENCY_P95_MS: ValueType.INT,
}


@dataclass(frozen=True)
class MetricsRecord:
    """Immutable metrics record.

    Phase 7 Invariant: Records are data-only and strictly typed.
    """
    schema_id: str  # "relay.metrics_record"
    schema_version: str  # "1.0.0"
    run_id: str  # UUID v7
    ts: Optional[str]  # RFC3339 or None
    time_policy: str  # "frozen" | "recorded"
    seq: int  # Monotonic, no gaps
    metric_id: MetricId
    value_type: ValueType
    value: Union[int, float, bool]
    source: MetricSource
    correlation: dict  # {trace_id, task_id, event_seq}

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "ts": self.ts,
            "time_policy": self.time_policy,
            "seq": self.seq,
            "metric_id": self.metric_id.value,
            "value_type": self.value_type.value,
            "value": self.value,
            "source": self.source.value,
            "correlation": self.correlation
        }


def validate_metric_value_type(metric_id: MetricId, value: Union[int, float, bool]) -> ValueType:
    """Validate that value matches expected type for metric.

    Args:
        metric_id: Metric identifier
        value: Value to validate

    Returns:
        ValueType enum

    Raises:
        ValueError: If value type doesn't match expected type
    """
    expected_type = METRIC_METADATA[metric_id]

    if expected_type == ValueType.INT and not isinstance(value, int):
        raise ValueError(f"Metric {metric_id.value} expects int, got {type(value).__name__}")
    elif expected_type == ValueType.FLOAT and not isinstance(value, (int, float)):
        raise ValueError(f"Metric {metric_id.value} expects float, got {type(value).__name__}")
    elif expected_type == ValueType.BOOL and not isinstance(value, bool):
        raise ValueError(f"Metric {metric_id.value} expects bool, got {type(value).__name__}")

    return expected_type
