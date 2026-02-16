"""
Audit event logging - Phase 1-3 Integration.

Phase 3 integration: Uses LogDaemon with Ed25519 signatures and hash chain.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Any
import uuid

from audit_logging.log_daemon import LogDaemon
from audit_logging.key_manager import KeyManager


class AuditLogger:
    """
    Audit event logger integrated with Phase 3 LogDaemon.

    Phase 1-3 integration: Uses Phase 3 LogDaemon for tamper-evident logging.
    """

    def __init__(
        self,
        log_daemon: Optional[LogDaemon] = None,
        audit_path: str = "/tmp/llm-relay-audit.jsonl"  # Legacy fallback
    ):
        """
        Initialize audit logger.

        Args:
            log_daemon: Phase 3 LogDaemon instance (if None, falls back to simple logging)
            audit_path: Path to audit log file (fallback for Phase 1 mode)
        """
        self.log_daemon = log_daemon
        self.audit_path = Path(audit_path)

        # Fallback mode if no LogDaemon provided
        if not self.log_daemon:
            # Ensure parent directory exists
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def log_validation_started(
        self,
        stage: str,
        message_id: Optional[str] = None,
        principal: Optional[str] = None,
        action: Optional[str] = None,
    ) -> str:
        """
        Log validation started event.

        Args:
            stage: Pipeline stage
            message_id: Message ID being validated
            principal: Principal identifier
            action: Action identifier

        Returns:
            Event ID
        """
        if self.log_daemon:
            # Phase 3: Use LogDaemon
            event = self.log_daemon.ingest_event(
                event_type="VALIDATION_STARTED",
                actor="validator",
                correlation={
                    "session_id": None,
                    "message_id": message_id,
                    "task_id": None
                },
                payload={
                    "validation_id": str(uuid.uuid4()),
                    "stage": stage,
                }
            )
            return event['event_id']
        else:
            # Fallback: Simple JSONL logging
            event = self._create_event(
                event_type="validation_started",
                stage=stage,
                result="pass",
                message_id=message_id,
                principal=principal,
                action=action,
            )
            self._write_event(event)
            return event['event_id']

    def log_validation_passed(
        self,
        stage: str,
        message_id: Optional[str] = None,
        principal: Optional[str] = None,
        action: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> str:
        """Log validation passed event."""
        if self.log_daemon:
            # Phase 3: Use LogDaemon
            payload = {
                "validation_id": str(uuid.uuid4()),
                "stage": stage,
            }
            if details:
                payload.update(details)

            event = self.log_daemon.ingest_event(
                event_type="VALIDATION_PASSED",
                actor="validator",
                correlation={
                    "session_id": None,
                    "message_id": message_id,
                    "task_id": None
                },
                payload=payload
            )
            return event['event_id']
        else:
            # Fallback: Simple JSONL logging
            event = self._create_event(
                event_type="validation_passed",
                stage=stage,
                result="pass",
                message_id=message_id,
                principal=principal,
                action=action,
                details=details,
            )
            self._write_event(event)
            return event['event_id']

    def log_validation_failed(
        self,
        stage: str,
        error_code: str,
        error_message: str,
        message_id: Optional[str] = None,
        principal: Optional[str] = None,
        action: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> str:
        """Log validation failed event."""
        if self.log_daemon:
            # Phase 3: Use LogDaemon
            payload = {
                "validation_id": str(uuid.uuid4()),
                "error_code": error_code,
                "stage": stage,
                "reason": error_message
            }
            if details:
                payload.update(details)

            event = self.log_daemon.ingest_event(
                event_type="VALIDATION_FAILED",
                actor="validator",
                correlation={
                    "session_id": None,
                    "message_id": message_id,
                    "task_id": None
                },
                payload=payload
            )
            return event['event_id']
        else:
            # Fallback: Simple JSONL logging
            event = self._create_event(
                event_type="validation_failed",
                stage=stage,
                result="fail",
                message_id=message_id,
                principal=principal,
                action=action,
                error_code=error_code,
                error_message=error_message,
                details=details,
            )
            self._write_event(event)
            return event['event_id']

    def _create_event(
        self,
        event_type: str,
        stage: str,
        result: str,
        message_id: Optional[str] = None,
        principal: Optional[str] = None,
        action: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> dict:
        """Create audit event dict."""
        event = {
            "event_id": str(uuid.uuid4()),  # TODO: Use UUID v7 in production
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "stage": stage,
            "result": result,
        }

        # Add optional fields
        if message_id:
            event["message_id"] = message_id
        if principal:
            event["principal"] = principal
        if action:
            event["action"] = action
        if error_code:
            event["error_code"] = error_code
        if error_message:
            event["error_message"] = error_message
        if details:
            event["details"] = details

        # Signature stub (null in Phase 1)
        event["signature"] = None

        return event

    def _write_event(self, event: dict):
        """
        Write event to JSONL file.

        Args:
            event: Event dict
        """
        # Append to file (one JSON object per line)
        with open(self.audit_path, 'a') as f:
            json.dump(event, f, ensure_ascii=False, sort_keys=True)
            f.write('\n')

    def read_events(self, limit: Optional[int] = None) -> list[dict]:
        """
        Read audit events from log.

        Args:
            limit: Maximum number of events to read (most recent first)

        Returns:
            List of audit event dicts
        """
        if not self.audit_path.exists():
            return []

        events = []
        with open(self.audit_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        # Most recent first
        events.reverse()

        if limit:
            events = events[:limit]

        return events
