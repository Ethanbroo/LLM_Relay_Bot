"""Execution event logging - Phase 2-3 Integration.

Phase 3 integration: Uses LogDaemon with Ed25519 signatures and hash chain.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Literal
from datetime import datetime, timezone
import uuid

from validator.canonicalize import canonicalize_json
from validator.time_policy import TimePolicy
from audit_logging.log_daemon import LogDaemon
from audit_logging.key_manager import KeyManager


EventType = Literal[
    "TASK_ENQUEUED",
    "TASK_DEQUEUED",
    "TASK_STARTED",
    "SANDBOX_CREATING",
    "SANDBOX_CREATED",
    "SANDBOX_DESTROYED",
    "SNAPSHOT_CREATING",
    "SNAPSHOT_CREATED",
    "SNAPSHOT_FAILED",
    "HANDLER_STARTED",
    "HANDLER_FINISHED",
    "HANDLER_FAILED",
    "HANDLER_TIMEOUT",
    "ROLLBACK_STARTED",
    "ROLLBACK_FINISHED",
    "ROLLBACK_FAILED",
    "TASK_FINISHED",
    "TASK_REQUEUED",
    "TASK_DEAD",
    "ENGINE_STARTED",
    "ENGINE_STOPPED",
    "ENGINE_HALTED"
]


class ExecutionEventLogger:
    """Logger for execution lifecycle events integrated with Phase 3 LogDaemon."""

    def __init__(
        self,
        log_daemon: Optional[LogDaemon] = None,
        log_path: str = "logs/execution_events.jsonl"  # Legacy fallback
    ):
        """Initialize execution event logger.

        Args:
            log_daemon: Phase 3 LogDaemon instance (if None, falls back to simple logging)
            log_path: Path to JSONL log file (fallback for Phase 2 mode)
        """
        self.log_daemon = log_daemon
        self.log_path = Path(log_path)
        self.time_policy = TimePolicy.from_config()

        # Fallback mode if no LogDaemon provided
        if not self.log_daemon:
            # Ensure log directory exists
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

            # Create empty log file if it doesn't exist
            if not self.log_path.exists():
                self.log_path.touch()

    def _compute_event_id(
        self,
        run_id: str,
        event_type: str,
        timestamp: str,
        event_data: Optional[dict]
    ) -> str:
        """Compute deterministic event ID.

        event_id = SHA-256(run_id + event_type + timestamp + canonical_event_data)

        Args:
            run_id: Execution attempt identifier
            event_type: Type of event
            timestamp: ISO 8601 timestamp
            event_data: Event-specific data (or None)

        Returns:
            Event ID with prefix: event_<hash>
        """
        # Canonical event data
        canonical_data = canonicalize_json(event_data) if event_data else ""

        # Deterministic concatenation
        components = f"{run_id}|{event_type}|{timestamp}|{canonical_data}"

        # SHA-256 hash
        event_hash = hashlib.sha256(components.encode('utf-8')).hexdigest()

        return f"event_{event_hash}"

    def log_event(
        self,
        event_type: EventType,
        run_id: str,
        task_id: str,
        attempt: int,
        action: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        event_data: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> str:
        """Log an execution event.

        Args:
            event_type: Type of event (TASK_STARTED, HANDLER_FINISHED, etc.)
            run_id: Execution attempt identifier (UUID v7)
            task_id: Deterministic task identifier
            attempt: Attempt number (1-indexed)
            action: Action name (optional)
            session_id: Session identifier (optional)
            message_id: Original envelope message_id (optional)
            event_data: Event-specific structured data (optional)
            error_code: Error code for failure events (optional)
            error_message: Error message for failure events (optional)

        Returns:
            Deterministic event_id
        """
        if self.log_daemon:
            # Phase 3: Use LogDaemon
            # Map Phase 2 event types to Phase 3 audit event types
            payload = {
                "task_id": task_id,
                "attempt": attempt
            }

            if event_data:
                payload.update(event_data)
            if error_code:
                payload["error_code"] = error_code
            if error_message:
                payload["reason"] = error_message

            event = self.log_daemon.ingest_event(
                event_type=event_type,
                actor="executor",
                correlation={
                    "session_id": session_id,
                    "message_id": message_id,
                    "task_id": task_id
                },
                payload=payload
            )
            return event['event_id']
        else:
            # Fallback: Simple JSONL logging
            # Get timestamp from time policy
            timestamp = self.time_policy.get_timestamp()

            # Compute deterministic event_id
            event_id = self._compute_event_id(run_id, event_type, timestamp, event_data)

            # Build event structure
            event = {
                "event_id": event_id,
                "event_type": event_type,
                "timestamp": timestamp,
                "run_id": run_id,
                "task_id": task_id,
                "attempt": attempt,
            }

            # Add optional fields
            if action:
                event["action"] = action
            if session_id:
                event["session_id"] = session_id
            if message_id:
                event["message_id"] = message_id
            if event_data:
                event["event_data"] = event_data
            if error_code:
                event["error_code"] = error_code
            if error_message:
                event["error_message"] = error_message

            # Phase 3: signature field (null for now)
            event["signature"] = None

            # Write to JSONL file (one event per line)
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + '\n')

            return event_id

    def read_events(
        self,
        limit: Optional[int] = None,
        task_id: Optional[str] = None,
        run_id: Optional[str] = None
    ) -> list[dict]:
        """Read execution events from log.

        Args:
            limit: Maximum number of events to return (most recent first)
            task_id: Filter by task_id (optional)
            run_id: Filter by run_id (optional)

        Returns:
            List of event dicts (most recent first)
        """
        if not self.log_path.exists():
            return []

        events = []
        with open(self.log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)

                    # Apply filters
                    if task_id and event.get("task_id") != task_id:
                        continue
                    if run_id and event.get("run_id") != run_id:
                        continue

                    events.append(event)
                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

        # Most recent first
        events.reverse()

        # Apply limit
        if limit:
            events = events[:limit]

        return events

    def get_task_lifecycle(self, task_id: str) -> list[dict]:
        """Get all events for a specific task (all attempts).

        Args:
            task_id: Task identifier

        Returns:
            List of events for this task (chronological order)
        """
        events = self.read_events(task_id=task_id)
        # Reverse to get chronological order (oldest first)
        events.reverse()
        return events

    def get_run_lifecycle(self, run_id: str) -> list[dict]:
        """Get all events for a specific execution attempt.

        Args:
            run_id: Execution attempt identifier

        Returns:
            List of events for this run (chronological order)
        """
        events = self.read_events(run_id=run_id)
        # Reverse to get chronological order (oldest first)
        events.reverse()
        return events
