"""Monitor daemon for Phase 7 - main coordinator.

Phase 7 Invariant: All monitoring is tick-driven, deterministic, and auditable.
"""

import time
from typing import Optional, Any, Callable
from pathlib import Path
from monitoring.metrics_collector import MetricsCollector
from monitoring.metrics_sink import MetricsSink
from monitoring.rules_engine import RulesEngine
from monitoring.recovery_controller import RecoveryController
from monitoring.incident_writer import IncidentWriter


class MonitorDaemonError(Exception):
    """Base exception for monitor daemon errors."""
    pass


class MonitorDaemon:
    """Monitor daemon coordinating metrics collection, rules evaluation, and recovery.

    Phase 7 Invariants:
    - Single tick loop drives all monitoring
    - Deterministic and reproducible
    - All actions auditable
    - Fail-closed on errors
    """

    def __init__(
        self,
        run_id: str,
        config: dict,
        config_hash: str,
        time_policy: str,
        task_queue: Optional[Any] = None,
        engine: Optional[Any] = None,
        log_daemon: Optional[Any] = None,
        coordination: Optional[Any] = None,
        connector_registry: Optional[Any] = None,
        orchestration: Optional[Any] = None,
        audit_callback: Optional[Callable] = None,
        supervisor_control_callback: Optional[Callable] = None
    ):
        """Initialize monitor daemon.

        Args:
            run_id: Run identifier (UUID v7)
            config: Monitoring configuration dict
            config_hash: SHA-256 hash of config
            time_policy: "frozen" or "recorded"
            task_queue: TaskQueue instance (Phase 2)
            engine: ExecutionEngine instance (Phase 2)
            log_daemon: LogDaemon instance (Phase 3)
            coordination: CoordinationPipeline instance (Phase 4)
            connector_registry: ConnectorRegistry instance (Phase 5)
            orchestration: OrchestrationPipeline instance (Phase 6)
            audit_callback: Callback for audit events
            supervisor_control_callback: Callback for Supervisor control signals

        Raises:
            MonitorDaemonError: If initialization fails
        """
        self.run_id = run_id
        self.config = config
        self.config_hash = config_hash
        self.time_policy = time_policy
        self.audit_callback = audit_callback
        self.supervisor_control_callback = supervisor_control_callback

        # Extract config
        self.tick_ms = config.get("tick_ms", 1000)
        self.metrics_dir = config.get("metrics_dir", f"run/{run_id}/metrics")
        self.metrics_max_segment_bytes = config.get("metrics_max_segment_bytes", 10_000_000)
        self.metrics_flush_policy = config.get("metrics_flush_policy", "fsync_each_line")
        self.rules_path = config.get("rules_path", "config/threshold_rules.json")
        self.max_restarts_per_target = config.get("max_restarts_per_target", 3)
        self.restart_cooldown_ms = config.get("restart_cooldown_ms", 60_000)
        self.incident_dir = config.get("incident_dir", f"run/{run_id}/incidents")
        self.incident_include_window_sec = config.get("incident_include_window_sec", 300)
        self.incident_max_bytes = config.get("incident_max_bytes", 2_000_000)

        # Resolve paths
        self.metrics_dir = self.metrics_dir.replace("<run_id>", run_id)
        self.incident_dir = self.incident_dir.replace("<run_id>", run_id)

        # Initialize components
        try:
            self.collector = MetricsCollector(
                run_id=run_id,
                time_policy=time_policy,
                task_queue=task_queue,
                engine=engine,
                log_daemon=log_daemon,
                coordination=coordination,
                connector_registry=connector_registry,
                orchestration=orchestration
            )

            self.sink = MetricsSink(
                metrics_dir=self.metrics_dir,
                run_id=run_id,
                max_segment_bytes=self.metrics_max_segment_bytes,
                flush_policy=self.metrics_flush_policy
            )

            self.rules_engine = RulesEngine(rules_path=self.rules_path)

            self.recovery_controller = RecoveryController(
                audit_callback=audit_callback,
                supervisor_control_callback=supervisor_control_callback,
                max_restarts_per_target=self.max_restarts_per_target,
                restart_cooldown_ms=self.restart_cooldown_ms
            )

            self.incident_writer = IncidentWriter(
                incident_dir=self.incident_dir,
                run_id=run_id,
                config_hash=config_hash,
                incident_include_window_sec=self.incident_include_window_sec,
                incident_max_bytes=self.incident_max_bytes
            )

        except Exception as e:
            raise MonitorDaemonError(f"Failed to initialize monitor daemon: {e}")

        # State
        self.running = False
        self.tick_count = 0

    def _emit_audit_event(self, event_type: str, metadata: dict) -> None:
        """Emit audit event to Phase 3.

        Args:
            event_type: Event type
            metadata: Event metadata
        """
        if self.audit_callback is not None:
            self.audit_callback(event_type, metadata)

    def tick(self, current_time_ms: Optional[int] = None) -> None:
        """Execute one monitoring tick.

        Phase 7 Invariant: All monitoring happens in single tick.

        Args:
            current_time_ms: Current time in milliseconds (or None to use time.time)
        """
        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)

        self.tick_count += 1

        try:
            # Step 1: Collect metrics
            records = self.collector.tick(current_time_ms)

            # Step 2: Write metrics to sink
            self.sink.write(records)

            # Step 3: Build metrics dict for rules evaluation
            metrics = {}
            for record in records:
                metrics[record.metric_id.value] = record.value

            # Step 4: Evaluate rules
            triggered_rules = self.rules_engine.evaluate_tick(metrics, current_time_ms)

            # Step 5: Execute recovery actions (one per tick)
            if len(triggered_rules) > 0:
                action_executed = self.recovery_controller.execute_tick(
                    triggered_rules,
                    current_time_ms,
                    metrics
                )

                # Step 6: Write incidents if needed
                for rule in triggered_rules:
                    if rule.emit_incident:
                        self._create_incident(rule, current_time_ms, records)

        except Exception as e:
            # Emit protocol violation
            self._emit_audit_event("MONITORING_PROTOCOL_VIOLATION", {
                "reason": "tick_failure",
                "error": str(e),
                "tick_count": self.tick_count
            })
            raise

    def _create_incident(
        self,
        rule: Any,
        current_time_ms: int,
        recent_records: list
    ) -> None:
        """Create incident for triggered rule.

        Args:
            rule: Triggered ThresholdRule
            current_time_ms: Current time in milliseconds
            recent_records: Recent metrics records
        """
        try:
            # Get timestamp
            from datetime import datetime, timezone
            first_trigger_ts = None
            if self.time_policy == "recorded":
                first_trigger_ts = datetime.fromtimestamp(
                    current_time_ms / 1000, tz=timezone.utc
                ).isoformat()
            else:
                first_trigger_ts = "frozen"

            # Get metrics window (last N seconds)
            window_start_seq = max(0, self.collector.seq - self.incident_include_window_sec)
            window_end_seq = self.collector.seq
            metrics_window = self.sink.read_window(window_start_seq, window_end_seq)

            # Create summary
            summary = f"Rule {rule.rule_id} triggered: {rule.metric_id.value} {rule.operator.value} {rule.threshold}"

            # Get audit event seq range from LogDaemon if available
            log_daemon = getattr(getattr(self.collector, "phase3_adapter", None), "log_daemon", None)
            if log_daemon is not None:
                last_seq = getattr(log_daemon, "last_event_seq", 0) or 0
                window_events = self.incident_include_window_sec  # approx 1 event/sec
                audit_event_seq_min = max(0, last_seq - window_events)
                audit_event_seq_max = last_seq
            else:
                audit_event_seq_min = 0
                audit_event_seq_max = 0

            # Write incident
            incident = self.incident_writer.write_incident(
                rule_id=rule.rule_id,
                severity=rule.severity.value,
                first_trigger_ts=first_trigger_ts,
                seq=self.tick_count,
                summary=summary,
                metrics_window=metrics_window,
                audit_event_seq_min=audit_event_seq_min,
                audit_event_seq_max=audit_event_seq_max,
                time_policy=self.time_policy
            )

            # Emit audit event
            self._emit_audit_event("INCIDENT_OPENED", {
                "incident_id": incident.incident_id,
                "rule_id": rule.rule_id,
                "severity": rule.severity.value
            })

        except Exception as e:
            self._emit_audit_event("MONITORING_PROTOCOL_VIOLATION", {
                "reason": "incident_creation_failed",
                "rule_id": rule.rule_id,
                "error": str(e)
            })

    def start(self) -> None:
        """Start monitor daemon (for testing - supervisor manages lifecycle)."""
        self.running = True

    def stop(self) -> None:
        """Stop monitor daemon and flush state."""
        self.running = False

        # Flush metrics sink
        self.sink.close()

        # Emit audit event
        self._emit_audit_event("MONITORING_STOPPED", {
            "tick_count": self.tick_count,
            "run_id": self.run_id
        })

    def get_stats(self) -> dict:
        """Get monitoring statistics.

        Returns:
            Dict of statistics
        """
        return {
            "tick_count": self.tick_count,
            "metrics_collected": self.collector.seq,
            "rules_loaded": len(self.rules_engine.rules),
            "triggered_rules": len(self.rules_engine.get_triggered_rules()),
            "open_incidents": len(self.incident_writer.get_open_incidents())
        }
