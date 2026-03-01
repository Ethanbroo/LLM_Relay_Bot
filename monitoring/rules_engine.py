"""Rules engine for Phase 7 threshold monitoring.

Phase 7 Invariant: Rule evaluation is deterministic, fixed-order, and reproducible.
"""

import json
from pathlib import Path
from typing import List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from monitoring.metrics_types import MetricId


class Operator(str, Enum):
    """Threshold operators (closed enum)."""
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    EQ = "EQ"
    NEQ = "NEQ"


class Severity(str, Enum):
    """Rule severity levels (closed enum).

    Phase 7 Invariant: FATAL > CRITICAL > WARN > INFO
    """
    FATAL = "FATAL"
    CRITICAL = "CRITICAL"
    WARN = "WARN"
    INFO = "INFO"


class RecoveryAction(str, Enum):
    """Recovery actions (closed enum).

    Phase 7 Invariant: Only these actions are allowed.
    """
    NOOP = "NOOP"
    THROTTLE_INGRESS = "THROTTLE_INGRESS"
    PAUSE_NEW_TASKS = "PAUSE_NEW_TASKS"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    RESTART_SUBSYSTEM = "RESTART_SUBSYSTEM"
    HALT_SYSTEM = "HALT_SYSTEM"


class RestartTarget(str, Enum):
    """Restart targets (closed enum)."""
    MONITOR_DAEMON = "monitor_daemon"
    LOG_DAEMON = "log_daemon"


@dataclass
class ThresholdRule:
    """Threshold rule with deterministic evaluation.

    Phase 7 Invariants:
    - Rules are immutable once loaded
    - Evaluation is deterministic
    - Cooldown/hysteresis prevents flapping
    """
    schema_id: str
    schema_version: str
    rule_id: str
    enabled: bool
    metric_id: MetricId
    operator: Operator
    threshold: Any  # number or bool
    window_mode: str  # Must be "consecutive"
    window_n: int
    severity: Severity
    action: RecoveryAction
    cooldown_ms: int
    hysteresis_clear_after_n: int
    emit_incident: bool
    restart_target: Optional[RestartTarget] = None

    # Runtime state (not persisted)
    consecutive_breaches: int = 0
    consecutive_clears: int = 0
    last_triggered_ms: Optional[int] = None
    is_triggered: bool = False

    @staticmethod
    def from_dict(data: dict) -> 'ThresholdRule':
        """Load rule from dict.

        Args:
            data: Rule data dict

        Returns:
            ThresholdRule instance

        Raises:
            ValueError: If rule validation fails
        """
        # Validate schema
        if data.get("schema_id") != "relay.threshold_rule":
            raise ValueError(f"Invalid schema_id: {data.get('schema_id')}")

        if data.get("schema_version") != "1.0.0":
            raise ValueError(f"Invalid schema_version: {data.get('schema_version')}")

        # Validate window mode
        window = data.get("window", {})
        if window.get("mode") != "consecutive":
            raise ValueError(f"Only consecutive window mode allowed, got {window.get('mode')}")

        # Parse restart target if present
        restart_target = None
        if data.get("action") == "RESTART_SUBSYSTEM":
            restart_target_str = data.get("restart_target")
            if not restart_target_str:
                raise ValueError("restart_target required for RESTART_SUBSYSTEM action")
            restart_target = RestartTarget(restart_target_str)

        return ThresholdRule(
            schema_id=data["schema_id"],
            schema_version=data["schema_version"],
            rule_id=data["rule_id"],
            enabled=data["enabled"],
            metric_id=MetricId(data["metric_id"]),
            operator=Operator(data["operator"]),
            threshold=data["threshold"],
            window_mode=window["mode"],
            window_n=window["n"],
            severity=Severity(data["severity"]),
            action=RecoveryAction(data["action"]),
            cooldown_ms=data["cooldown_ms"],
            hysteresis_clear_after_n=data["hysteresis"]["clear_after_n"],
            emit_incident=data["emit_incident"],
            restart_target=restart_target
        )

    def evaluate(self, value: Any) -> bool:
        """Evaluate if threshold is breached.

        Args:
            value: Current metric value

        Returns:
            True if breached, False otherwise
        """
        if self.operator == Operator.GT:
            return value > self.threshold
        elif self.operator == Operator.GTE:
            return value >= self.threshold
        elif self.operator == Operator.LT:
            return value < self.threshold
        elif self.operator == Operator.LTE:
            return value <= self.threshold
        elif self.operator == Operator.EQ:
            return value == self.threshold
        elif self.operator == Operator.NEQ:
            return value != self.threshold
        else:
            return False


class RulesEngine:
    """Rules engine with fixed evaluation order.

    Phase 7 Invariants:
    - Rules evaluated in strict order: severity then rule_id
    - Only consecutive window mode supported
    - Cooldown prevents flapping
    - Hysteresis for clearing
    """

    def __init__(self, rules_path: str):
        """Initialize rules engine.

        Args:
            rules_path: Path to rules file (JSON or YAML)

        Raises:
            FileNotFoundError: If rules file not found
            ValueError: If rules file invalid
        """
        self.rules_path = Path(rules_path)
        self.rules: List[ThresholdRule] = []

        # Load and validate rules
        self._load_rules()

        # Sort rules by evaluation order
        self._sort_rules()

    def _load_rules(self) -> None:
        """Load rules from file.

        Phase 7 Invariant: Invalid rules file causes startup failure.

        Raises:
            FileNotFoundError: If rules file missing
            ValueError: If rules file invalid
        """
        if not self.rules_path.exists():
            raise FileNotFoundError(f"Rules file not found: {self.rules_path}")

        try:
            with open(self.rules_path, 'r', encoding='utf-8') as f:
                if self.rules_path.suffix == '.json':
                    data = json.load(f)
                elif self.rules_path.suffix in ('.yaml', '.yml'):
                    try:
                        import yaml
                        data = yaml.safe_load(f)
                    except ImportError:
                        raise ValueError(
                            "PyYAML is required to load YAML rules files. "
                            "Install it with: pip install pyyaml"
                        )
                else:
                    raise ValueError(
                        f"Unsupported rules file format: {self.rules_path.suffix}. "
                        "Use .json or .yaml"
                    )

                # Handle both single rule and array of rules
                if isinstance(data, dict):
                    rules_data = [data]
                else:
                    rules_data = data

                for rule_data in rules_data:
                    rule = ThresholdRule.from_dict(rule_data)
                    self.rules.append(rule)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid rules file: {e}")

    def _sort_rules(self) -> None:
        """Sort rules by evaluation order.

        Phase 7 Invariant: FATAL > CRITICAL > WARN > INFO, then lexicographic by rule_id.
        """
        severity_order = {
            Severity.FATAL: 0,
            Severity.CRITICAL: 1,
            Severity.WARN: 2,
            Severity.INFO: 3
        }

        self.rules.sort(key=lambda r: (severity_order[r.severity], r.rule_id))

    def evaluate_tick(
        self,
        metrics: dict,
        current_time_ms: int
    ) -> List[ThresholdRule]:
        """Evaluate all rules for current tick.

        Phase 7 Invariant: Rules evaluated in strict order, first match wins.

        Args:
            metrics: Dict of metric_id -> value
            current_time_ms: Current time in milliseconds

        Returns:
            List of triggered rules (in evaluation order)
        """
        triggered_rules = []

        for rule in self.rules:
            if not rule.enabled:
                continue

            # Get metric value
            metric_id_str = rule.metric_id.value
            if metric_id_str not in metrics:
                continue

            value = metrics[metric_id_str]

            # Evaluate threshold
            is_breach = rule.evaluate(value)

            if is_breach:
                # Increment consecutive breaches
                rule.consecutive_breaches += 1
                rule.consecutive_clears = 0

                # Check if window threshold met
                if rule.consecutive_breaches >= rule.window_n:
                    # Check cooldown
                    if rule.last_triggered_ms is None or \
                       (current_time_ms - rule.last_triggered_ms) >= rule.cooldown_ms:
                        # Trigger rule
                        if not rule.is_triggered:
                            rule.is_triggered = True
                            rule.last_triggered_ms = current_time_ms
                            triggered_rules.append(rule)
            else:
                # Clear breach counter
                rule.consecutive_breaches = 0
                rule.consecutive_clears += 1

                # Check if hysteresis threshold met for clearing
                if rule.is_triggered and rule.consecutive_clears >= rule.hysteresis_clear_after_n:
                    rule.is_triggered = False

        return triggered_rules

    def get_triggered_rules(self) -> List[ThresholdRule]:
        """Get currently triggered rules.

        Returns:
            List of triggered rules
        """
        return [r for r in self.rules if r.is_triggered]

    def reset_all(self) -> None:
        """Reset all rule states (for testing)."""
        for rule in self.rules:
            rule.consecutive_breaches = 0
            rule.consecutive_clears = 0
            rule.last_triggered_ms = None
            rule.is_triggered = False
