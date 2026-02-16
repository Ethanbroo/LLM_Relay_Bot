"""Recovery controller for Phase 7.

Phase 7 Invariant: Recovery actions are closed, auditable, and never bypass Supervisor.
"""

from typing import Optional, Callable, List
from monitoring.rules_engine import ThresholdRule, RecoveryAction, RestartTarget


class RecoveryControllerError(Exception):
    """Base exception for recovery controller errors."""
    pass


class RecoveryController:
    """Recovery controller for executing threshold-triggered actions.

    Phase 7 Invariants:
    - Only one action per tick (highest severity wins)
    - Cannot directly kill processes
    - All actions send control signals to Supervisor
    - Every action is auditable
    """

    def __init__(
        self,
        audit_callback: Optional[Callable] = None,
        supervisor_control_callback: Optional[Callable] = None,
        max_restarts_per_target: int = 3,
        restart_cooldown_ms: int = 60000
    ):
        """Initialize recovery controller.

        Args:
            audit_callback: Callback for emitting audit events
            supervisor_control_callback: Callback for sending control signals to Supervisor
            max_restarts_per_target: Maximum restarts per target per run
            restart_cooldown_ms: Cooldown between restarts (default 60 seconds)
        """
        self.audit_callback = audit_callback
        self.supervisor_control_callback = supervisor_control_callback
        self.max_restarts_per_target = max_restarts_per_target
        self.restart_cooldown_ms = restart_cooldown_ms

        # Track restarts
        self.restart_count = {
            RestartTarget.MONITOR_DAEMON: 0,
            RestartTarget.LOG_DAEMON: 0
        }
        self.last_restart_time = {
            RestartTarget.MONITOR_DAEMON: None,
            RestartTarget.LOG_DAEMON: None
        }

    def _emit_audit_event(self, event_type: str, metadata: dict) -> None:
        """Emit audit event to Phase 3.

        Args:
            event_type: Event type
            metadata: Event metadata
        """
        if self.audit_callback is not None:
            self.audit_callback(event_type, metadata)

    def _send_supervisor_control(self, control_type: str, payload: dict) -> None:
        """Send control signal to Supervisor.

        Args:
            control_type: Control signal type
            payload: Control payload
        """
        if self.supervisor_control_callback is not None:
            self.supervisor_control_callback(control_type, payload)

    def execute_tick(
        self,
        triggered_rules: List[ThresholdRule],
        current_time_ms: int,
        metrics: dict
    ) -> Optional[RecoveryAction]:
        """Execute recovery actions for triggered rules.

        Phase 7 Invariant: Execute exactly one action per tick (highest severity).

        Args:
            triggered_rules: List of triggered rules (already sorted by severity)
            current_time_ms: Current time in milliseconds
            metrics: Current metrics dict

        Returns:
            RecoveryAction executed (or None)
        """
        if len(triggered_rules) == 0:
            return None

        # Take first rule (highest severity due to sorting)
        rule = triggered_rules[0]

        # Emit THRESHOLD_BREACHED audit event
        self._emit_audit_event("THRESHOLD_BREACHED", {
            "rule_id": rule.rule_id,
            "metric_id": rule.metric_id.value,
            "observed_value": metrics.get(rule.metric_id.value),
            "threshold": rule.threshold,
            "action": rule.action.value,
            "correlation": {
                "trace_id": f"recovery_{current_time_ms}"
            }
        })

        # Execute action
        action_executed = self._execute_action(rule, current_time_ms)

        if action_executed:
            # Emit RECOVERY_ACTION_APPLIED
            self._emit_audit_event("RECOVERY_ACTION_APPLIED", {
                "rule_id": rule.rule_id,
                "action": rule.action.value,
                "correlation": {
                    "trace_id": f"recovery_{current_time_ms}"
                }
            })

        return rule.action if action_executed else None

    def _execute_action(self, rule: ThresholdRule, current_time_ms: int) -> bool:
        """Execute specific recovery action.

        Args:
            rule: Rule triggering action
            current_time_ms: Current time in milliseconds

        Returns:
            True if action executed, False if blocked
        """
        action = rule.action

        # Emit RECOVERY_ACTION_REQUESTED
        self._emit_audit_event("RECOVERY_ACTION_REQUESTED", {
            "rule_id": rule.rule_id,
            "action": action.value,
            "correlation": {
                "trace_id": f"recovery_{current_time_ms}"
            }
        })

        if action == RecoveryAction.NOOP:
            # Audit only - no action
            return True

        elif action == RecoveryAction.THROTTLE_INGRESS:
            # Set Supervisor control flag
            self._send_supervisor_control("SET_INGRESS_MODE", {
                "mode": "throttled",
                "reason": f"rule_{rule.rule_id}_triggered"
            })
            return True

        elif action == RecoveryAction.PAUSE_NEW_TASKS:
            # Set Supervisor control flag
            self._send_supervisor_control("SET_TASK_CONSUME", {
                "enabled": False,
                "reason": f"rule_{rule.rule_id}_triggered"
            })
            return True

        elif action == RecoveryAction.REQUEST_APPROVAL:
            # Create approval request (Phase 4 integration)
            self._send_supervisor_control("REQUEST_APPROVAL", {
                "rule_id": rule.rule_id,
                "reason": "threshold_breach",
                "severity": rule.severity.value
            })
            return True

        elif action == RecoveryAction.RESTART_SUBSYSTEM:
            # Check restart target specified
            if rule.restart_target is None:
                self._emit_audit_event("MONITORING_PROTOCOL_VIOLATION", {
                    "reason": "restart_target_missing",
                    "rule_id": rule.rule_id
                })
                return False

            # Check max restarts
            if self.restart_count[rule.restart_target] >= self.max_restarts_per_target:
                self._emit_audit_event("MONITORING_PROTOCOL_VIOLATION", {
                    "reason": "max_restarts_exceeded",
                    "target": rule.restart_target.value,
                    "count": self.restart_count[rule.restart_target]
                })
                return False

            # Check cooldown
            last_restart = self.last_restart_time[rule.restart_target]
            if last_restart is not None:
                elapsed = current_time_ms - last_restart
                if elapsed < self.restart_cooldown_ms:
                    return False  # Still in cooldown

            # Send restart request to Supervisor
            self._send_supervisor_control("RESTART_SUBSYSTEM", {
                "target": rule.restart_target.value,
                "reason": f"rule_{rule.rule_id}_triggered"
            })

            # Update restart tracking
            self.restart_count[rule.restart_target] += 1
            self.last_restart_time[rule.restart_target] = current_time_ms
            return True

        elif action == RecoveryAction.HALT_SYSTEM:
            # Send halt request to Supervisor
            self._send_supervisor_control("HALT_SYSTEM", {
                "reason": f"rule_{rule.rule_id}_triggered",
                "severity": rule.severity.value
            })
            return True

        else:
            # Unknown action (should never happen due to enum)
            return False
