"""Basic tests for Phase 7 Monitoring & Recovery.

Phase 7 Invariant: All monitoring is deterministic and reproducible.
"""

import pytest
import tempfile
import json
from pathlib import Path
from monitoring.metrics_types import MetricId, ValueType, MetricSource
from monitoring.metrics_collector import MetricsCollector, RingBuffer
from monitoring.metrics_sink import MetricsSink
from monitoring.rules_engine import RulesEngine, ThresholdRule, Operator, Severity, RecoveryAction
from monitoring.recovery_controller import RecoveryController
from monitoring.incident_writer import IncidentWriter


class TestRingBuffer:
    """Test deterministic ring buffer."""

    def test_ring_buffer_fixed_size(self):
        """Test that ring buffer has fixed size."""
        buffer = RingBuffer(5)

        for i in range(10):
            buffer.append(i)

        assert len(buffer) == 5
        # Should contain last 5 values: [5, 6, 7, 8, 9]
        assert list(buffer.buffer) == [5, 6, 7, 8, 9]

    def test_ring_buffer_sum(self):
        """Test ring buffer sum computation."""
        buffer = RingBuffer(5)

        for i in range(5):
            buffer.append(i)

        assert buffer.sum() == 0 + 1 + 2 + 3 + 4

    def test_ring_buffer_percentile(self):
        """Test deterministic percentile computation."""
        buffer = RingBuffer(100)

        for i in range(100):
            buffer.append(i)

        # p95 of [0..99] should be 94
        assert buffer.percentile(0.95) == 94


class TestMetricsCollector:
    """Test metrics collector."""

    def test_collector_initialization(self):
        """Test collector initializes correctly."""
        collector = MetricsCollector(
            run_id="test-run-123",
            time_policy="frozen"
        )

        assert collector.run_id == "test-run-123"
        assert collector.time_policy == "frozen"
        assert collector.seq == 0

    def test_collector_tick_produces_records(self):
        """Test that tick produces metrics records."""
        collector = MetricsCollector(
            run_id="test-run-123",
            time_policy="frozen"
        )

        records = collector.tick(current_time_ms=1000000)

        assert len(records) > 0
        assert all(r.run_id == "test-run-123" for r in records)
        assert all(r.time_policy == "frozen" for r in records)
        assert all(r.ts is None for r in records)

    def test_collector_seq_increments(self):
        """Test that seq increments monotonically."""
        collector = MetricsCollector(
            run_id="test-run-123",
            time_policy="frozen"
        )

        records1 = collector.tick(current_time_ms=1000000)
        seq_values1 = [r.seq for r in records1]

        records2 = collector.tick(current_time_ms=2000000)
        seq_values2 = [r.seq for r in records2]

        # All seqs should be monotonic with no gaps
        all_seqs = seq_values1 + seq_values2
        assert all_seqs == list(range(len(all_seqs)))


class TestMetricsSink:
    """Test metrics sink."""

    def test_sink_writes_jsonl(self):
        """Test sink writes JSONL format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sink = MetricsSink(
                metrics_dir=tmpdir,
                run_id="test-run-123"
            )

            collector = MetricsCollector(
                run_id="test-run-123",
                time_policy="frozen"
            )

            records = collector.tick(current_time_ms=1000000)
            sink.write(records)
            sink.close()

            # Check file exists
            segment_paths = sink.get_segment_paths()
            assert len(segment_paths) == 1

            # Check JSONL format
            with open(segment_paths[0], 'r') as f:
                lines = f.readlines()
                assert len(lines) > 0

                # Each line should be valid JSON
                for line in lines:
                    data = json.loads(line)
                    assert data["schema_id"] == "relay.metrics_record"


class TestRulesEngine:
    """Test rules engine."""

    def test_rules_engine_loads_rules(self):
        """Test that rules engine loads rules from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "rules.json"

            rule_data = {
                "schema_id": "relay.threshold_rule",
                "schema_version": "1.0.0",
                "rule_id": "test_rule",
                "enabled": True,
                "metric_id": "proc.cpu_percent",
                "operator": "GT",
                "threshold": 80.0,
                "window": {"mode": "consecutive", "n": 3},
                "severity": "WARN",
                "action": "NOOP",
                "cooldown_ms": 10000,
                "hysteresis": {"clear_after_n": 5},
                "emit_incident": False
            }

            with open(rules_path, 'w') as f:
                json.dump([rule_data], f)

            engine = RulesEngine(rules_path=str(rules_path))
            assert len(engine.rules) == 1
            assert engine.rules[0].rule_id == "test_rule"

    def test_rules_sorted_by_severity(self):
        """Test that rules are sorted by severity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "rules.json"

            rules_data = [
                {
                    "schema_id": "relay.threshold_rule",
                    "schema_version": "1.0.0",
                    "rule_id": "info_rule",
                    "enabled": True,
                    "metric_id": "proc.cpu_percent",
                    "operator": "GT",
                    "threshold": 50.0,
                    "window": {"mode": "consecutive", "n": 1},
                    "severity": "INFO",
                    "action": "NOOP",
                    "cooldown_ms": 0,
                    "hysteresis": {"clear_after_n": 1},
                    "emit_incident": False
                },
                {
                    "schema_id": "relay.threshold_rule",
                    "schema_version": "1.0.0",
                    "rule_id": "fatal_rule",
                    "enabled": True,
                    "metric_id": "proc.cpu_percent",
                    "operator": "GT",
                    "threshold": 95.0,
                    "window": {"mode": "consecutive", "n": 1},
                    "severity": "FATAL",
                    "action": "HALT_SYSTEM",
                    "cooldown_ms": 0,
                    "hysteresis": {"clear_after_n": 1},
                    "emit_incident": True
                }
            ]

            with open(rules_path, 'w') as f:
                json.dump(rules_data, f)

            engine = RulesEngine(rules_path=str(rules_path))

            # FATAL should come before INFO
            assert engine.rules[0].severity == Severity.FATAL
            assert engine.rules[1].severity == Severity.INFO

    def test_rule_evaluation(self):
        """Test rule evaluation logic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "rules.json"

            rule_data = {
                "schema_id": "relay.threshold_rule",
                "schema_version": "1.0.0",
                "rule_id": "test_rule",
                "enabled": True,
                "metric_id": "proc.cpu_percent",
                "operator": "GT",
                "threshold": 80.0,
                "window": {"mode": "consecutive", "n": 3},
                "severity": "WARN",
                "action": "NOOP",
                "cooldown_ms": 10000,
                "hysteresis": {"clear_after_n": 5},
                "emit_incident": False
            }

            with open(rules_path, 'w') as f:
                json.dump([rule_data], f)

            engine = RulesEngine(rules_path=str(rules_path))

            # Trigger rule with 3 consecutive breaches
            for i in range(3):
                triggered = engine.evaluate_tick(
                    {"proc.cpu_percent": 90.0},
                    current_time_ms=i * 1000
                )

            # Should trigger on 3rd breach
            assert len(triggered) == 1
            assert triggered[0].rule_id == "test_rule"


class TestIncidentWriter:
    """Test incident writer."""

    def test_incident_id_deterministic(self):
        """Test that incident_id is deterministic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = IncidentWriter(
                incident_dir=tmpdir,
                run_id="test-run-123",
                config_hash="abc123"
            )

            incident_id1 = writer.compute_incident_id("rule1", "2024-01-01T00:00:00Z", 1)
            incident_id2 = writer.compute_incident_id("rule1", "2024-01-01T00:00:00Z", 1)

            assert incident_id1 == incident_id2
            assert len(incident_id1) == 64  # SHA-256 hex

    def test_incident_write(self):
        """Test incident writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = IncidentWriter(
                incident_dir=tmpdir,
                run_id="test-run-123",
                config_hash="abc123"
            )

            incident = writer.write_incident(
                rule_id="test_rule",
                severity="CRITICAL",
                first_trigger_ts="2024-01-01T00:00:00Z",
                seq=1,
                summary="Test incident",
                metrics_window=[],
                audit_event_seq_min=0,
                audit_event_seq_max=10,
                time_policy="recorded"
            )

            assert incident.state == "OPEN"
            assert incident.rule_id == "test_rule"

            # Check files created
            incident_path = Path(tmpdir) / f"{incident.incident_id}.json"
            assert incident_path.exists()


class TestRecoveryController:
    """Test recovery controller."""

    def test_recovery_controller_emits_audit_events(self):
        """Test that recovery controller emits audit events."""
        audit_events = []

        def audit_callback(event_type, metadata):
            audit_events.append({"event_type": event_type, "metadata": metadata})

        controller = RecoveryController(audit_callback=audit_callback)

        # Create a triggered rule (mock)
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "rules.json"

            rule_data = {
                "schema_id": "relay.threshold_rule",
                "schema_version": "1.0.0",
                "rule_id": "test_rule",
                "enabled": True,
                "metric_id": "proc.cpu_percent",
                "operator": "GT",
                "threshold": 80.0,
                "window": {"mode": "consecutive", "n": 1},
                "severity": "WARN",
                "action": "NOOP",
                "cooldown_ms": 0,
                "hysteresis": {"clear_after_n": 1},
                "emit_incident": False
            }

            with open(rules_path, 'w') as f:
                json.dump([rule_data], f)

            engine = RulesEngine(rules_path=str(rules_path))
            triggered = engine.evaluate_tick(
                {"proc.cpu_percent": 90.0},
                current_time_ms=1000
            )

            controller.execute_tick(triggered, 1000, {"proc.cpu_percent": 90.0})

            # Should have emitted audit events
            assert len(audit_events) >= 2
            assert any(e["event_type"] == "THRESHOLD_BREACHED" for e in audit_events)
            assert any(e["event_type"] == "RECOVERY_ACTION_REQUESTED" for e in audit_events)
