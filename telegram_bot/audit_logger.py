"""
Structured JSON audit logger — Phase 5.

Provides a structured logging API for all security-relevant browser agent
events. Every entry is a JSON object written to a .jsonl file (one per line).

Event types:
  - task_started, task_completed
  - action_proposed, action_classified, action_executed, action_blocked
  - approval_requested, approval_resolved
  - security_event

Credential values are automatically redacted. Log files rotate daily
with 30-day retention.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Sensitive fields that should be redacted in logs
REDACTED_FIELDS = frozenset({"password", "username", "text", "cookies", "totp_seed", "totp", "secret"})

# Default log directory — can be overridden via AUDIT_LOG_DIR env var
DEFAULT_LOG_DIR = os.environ.get("AUDIT_LOG_DIR", "/var/log/browser-agent")


class AuditLogger:
    """Structured JSON audit logger for browser agent events."""

    def __init__(self, log_dir: str | None = None):
        log_dir = log_dir or DEFAULT_LOG_DIR
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Configure a dedicated Python logger for audit events
        self._logger = logging.getLogger("browser_agent.audit")
        self._logger.setLevel(logging.INFO)
        # Prevent propagation to root logger (avoids duplicate output)
        self._logger.propagate = False

        # Only add handler if none exist yet (prevents duplicates on reload)
        if not self._logger.handlers:
            handler = logging.handlers.TimedRotatingFileHandler(
                self.log_dir / "audit.jsonl",
                when="midnight",
                backupCount=30,
                utc=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

        logger.info("Audit logger initialized: %s", self.log_dir)

    def log(self, event_type: str, severity: str = "info", **kwargs):
        """Log a structured audit event.

        Args:
            event_type: One of the defined event types (task_started, etc.)
            severity: info, warning, or critical
            **kwargs: Additional fields specific to this event type.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "severity": severity,
        }
        for key, value in kwargs.items():
            if isinstance(value, dict):
                entry[key] = self._redact_dict(value)
            elif isinstance(value, set):
                entry[key] = sorted(value)
            else:
                entry[key] = value

        self._logger.info(json.dumps(entry, default=str))

    def log_task_started(self, task_id: str, user_id: int, user_task: str,
                         expected_domains: set[str] | None = None):
        self.log(
            "task_started",
            task_id=task_id,
            user_id=user_id,
            user_task=user_task[:500],
            expected_domains=expected_domains or set(),
        )

    def log_task_completed(self, task_id: str, status: str, total_steps: int,
                           total_tokens: int = 0, duration_seconds: float = 0,
                           summary: str = ""):
        self.log(
            "task_completed",
            task_id=task_id,
            status=status,
            total_steps=total_steps,
            total_tokens=total_tokens,
            duration_seconds=duration_seconds,
            summary=summary[:500],
        )

    def log_action_classified(self, task_id: str, step_number: int,
                              action_name: str, classification_tier: int,
                              classification_reason: str,
                              allowlist_result: str = "",
                              suspicion_score: float = 0.0,
                              verifier_result: str = ""):
        self.log(
            "action_classified",
            task_id=task_id,
            step_number=step_number,
            action_name=action_name,
            classification_tier=classification_tier,
            classification_reason=classification_reason,
            allowlist_result=allowlist_result,
            suspicion_score=round(suspicion_score, 2),
            verifier_result=verifier_result,
        )

    def log_action_executed(self, task_id: str, step_number: int,
                            action_name: str, action_params: dict,
                            result: str, execution_time_ms: float = 0,
                            new_url: str = ""):
        self.log(
            "action_executed",
            task_id=task_id,
            step_number=step_number,
            action_name=action_name,
            action_params=action_params,
            result=result,
            execution_time_ms=round(execution_time_ms, 1),
            new_url=new_url,
        )

    def log_action_blocked(self, task_id: str, step_number: int,
                           action_name: str, action_params: dict,
                           block_reason: str, block_source: str):
        self.log(
            "action_blocked",
            severity="warning",
            task_id=task_id,
            step_number=step_number,
            action_name=action_name,
            action_params=action_params,
            block_reason=block_reason,
            block_source=block_source,
        )

    def log_approval_requested(self, task_id: str, action_summary: str,
                               current_url: str, message_id: int | None = None):
        self.log(
            "approval_requested",
            task_id=task_id,
            action_summary=action_summary,
            current_url=current_url,
            message_id=message_id,
        )

    def log_approval_resolved(self, task_id: str, decision: str,
                              response_time_seconds: float = 0,
                              message_id: int | None = None):
        self.log(
            "approval_resolved",
            task_id=task_id,
            decision=decision,
            response_time_seconds=round(response_time_seconds, 1),
            message_id=message_id,
        )

    def log_security_event(self, task_id: str, event_subtype: str,
                           details: str, severity: str = "warning"):
        self.log(
            "security_event",
            severity=severity,
            task_id=task_id,
            event_subtype=event_subtype,
            details=details,
        )

    def _redact_dict(self, d: dict) -> dict:
        """Redact sensitive fields in a dictionary."""
        redacted = {}
        for key, value in d.items():
            if key.lower() in REDACTED_FIELDS:
                redacted[key] = "[REDACTED]"
            elif isinstance(value, dict):
                redacted[key] = self._redact_dict(value)
            else:
                redacted[key] = value
        return redacted
