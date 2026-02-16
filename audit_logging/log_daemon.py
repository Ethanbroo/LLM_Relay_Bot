"""
LogDaemon: Core audit logging write path with tamper-evident hash chain.

Responsibilities:
- Ingest events via bounded queue
- Validate event_type against closed enum
- Redact secrets from payloads
- Assign monotonic event_seq
- Compute hashes (payload_hash, event_hash, event_id)
- Build hash chain (prev_event_hash)
- Sign events with Ed25519
- Append JSONL lines to segment files
- Flush policy (every N events, immediate for critical types)
"""

import json
import threading
from pathlib import Path
from collections import deque
from typing import Any, Optional
from datetime import datetime, timezone

from audit_logging.canonicalize import (
    canonical_json,
    compute_payload_hash,
    compute_event_hash,
    compute_event_id,
)
from audit_logging.crypto import sign_event_hash
from audit_logging.redaction import redact, create_redaction_metadata
from audit_logging.key_manager import KeyManager


# Closed enum of valid event types (Phase 1-4 integration)
VALID_EVENT_TYPES = {
    # Phase 1: Validation events
    "VALIDATION_STARTED",
    "VALIDATION_PASSED",
    "VALIDATION_FAILED",
    "RBAC_DENIED",
    "PATH_REJECTED",
    # Phase 2: Execution events
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
    "ENGINE_HALTED",
    # Phase 3: System events
    "RUN_STARTED",
    "CONFIG_HASH_VERIFIED",
    "CONFIG_MISMATCH",
    "PROCESS_STARTED",
    "PROCESS_RESTARTED",
    "PROCESS_HALTED",
    "LOG_CORRUPTION_DETECTED",
    "LOG_TAMPER_DETECTED",
    "LOG_BACKPRESSURE",
    "PRODUCER_PROTOCOL_VIOLATION",
    "SECRET_REDACTED",
    # Phase 4: Coordination events (locks)
    "LOCK_SET_REQUESTED",
    "LOCK_SET_ACQUIRED",
    "LOCK_SET_WAITING",
    "LOCK_SET_RELEASED",
    "LOCK_EXPIRED",
    "LOCK_ORDER_VIOLATION",
    # Phase 4: Coordination events (deadlock)
    "DEADLOCK_DETECTED",
    "DEADLOCK_VICTIM_SELECTED",
    # Phase 4: Coordination events (approval)
    "APPROVAL_TOKEN_REGISTERED",
    "APPROVAL_VERIFIED",
    "APPROVAL_REJECTED",
    # Phase 4: Coordination events (pipeline)
    "COORDINATION_COMPLETED",
    "COORDINATION_BLOCKED",
    "COORDINATION_FAILED",
    # Phase 5: Connector events (lifecycle)
    "CONNECTOR_CONNECT_STARTED",
    "CONNECTOR_CONNECTED",
    "CONNECTOR_CONNECT_FAILED",
    "CONNECTOR_EXECUTE_STARTED",
    "CONNECTOR_EXECUTE_FINISHED",
    "CONNECTOR_EXECUTE_FAILED",
    "CONNECTOR_IDEMPOTENCY_HIT",
    "CONNECTOR_ROLLBACK_STARTED",
    "CONNECTOR_ROLLBACK_FINISHED",
    "CONNECTOR_ROLLBACK_FAILED",
    "CONNECTOR_DISCONNECT_STARTED",
    "CONNECTOR_DISCONNECTED",
    "CONNECTOR_DISCONNECT_FAILED",
    # Phase 6: Orchestration events
    "ORCHESTRATION_STARTED",
    "LLM_REQUEST_SENT",
    "LLM_RESPONSE_ACCEPTED",
    "LLM_RESPONSE_REJECTED",
    "CONSENSUS_REACHED",
    "CONSENSUS_FAILED",
    "ESCALATION_TRIGGERED",
    "ORCHESTRATION_DECISION_EMITTED",
    # Phase 7: Monitoring events
    "METRICS_TICK",
    "THRESHOLD_BREACHED",
    "THRESHOLD_CLEARED",
    "RECOVERY_ACTION_REQUESTED",
    "RECOVERY_ACTION_APPLIED",
    "INCIDENT_OPENED",
    "INCIDENT_CLOSED",
    "MONITORING_PROTOCOL_VIOLATION",
    "MONITORING_STOPPED",
    # Phase 8: Claude LLM events
    "LLM_PROMPT_SENT",
    "LLM_RESPONSE_RECEIVED",
    "LLM_OUTPUT_REJECTED",
    "LLM_OUTPUT_ACCEPTED",
}

# Critical event types that require immediate fsync
CRITICAL_EVENT_TYPES = {
    "LOG_TAMPER_DETECTED",
    "LOG_CORRUPTION_DETECTED",
    "ENGINE_HALTED",
    "CONFIG_MISMATCH",
    "DEADLOCK_DETECTED",  # Phase 4: Deadlock detection is critical
    "LOCK_ORDER_VIOLATION",  # Phase 4: Lock violations are critical
    "CONNECTOR_EXECUTE_FAILED",  # Phase 5: Connector execution failures are critical
    "CONNECTOR_ROLLBACK_FAILED",  # Phase 5: Rollback failures are critical
    "LLM_OUTPUT_REJECTED",  # Phase 8: LLM output rejections are critical
}

# Genesis value for prev_event_hash (first event)
GENESIS_HASH = "0" * 64


class InvalidEventTypeError(Exception):
    """Raised when event_type is not in closed enum."""
    pass


class SecretLeakError(Exception):
    """Raised when secrets detected after redaction."""
    pass


class LogDaemon:
    """
    Core audit logging daemon with tamper-evident hash chain.

    Attributes:
        run_id: UUID of current run
        config_hash: SHA-256 of core.yaml
        time_policy: "frozen" or "recorded"
        key_manager: KeyManager instance for signing
        log_directory: Path to log directory
        segment_path: Path to current segment file
        event_seq: Monotonic event sequence number
        prev_event_hash: Hash of previous event (for chain)
        fsync_every_n_events: Fsync after N events
        events_since_fsync: Counter for fsync policy
    """

    def __init__(
        self,
        run_id: str,
        config_hash: str,
        time_policy: str,
        key_manager: KeyManager,
        log_directory: str | Path,
        segment_filename: str = "audit.000001.jsonl",
        fsync_every_n_events: int = 100,
    ):
        """
        Initialize LogDaemon.

        Args:
            run_id: UUID v4/v7 for this run
            config_hash: SHA-256 hash of core.yaml
            time_policy: "frozen" or "recorded"
            key_manager: KeyManager instance
            log_directory: Directory for log files
            segment_filename: Initial segment filename
            fsync_every_n_events: Fsync after this many events
        """
        self.run_id = run_id
        self.config_hash = config_hash
        self.time_policy = time_policy
        self.key_manager = key_manager
        self.log_directory = Path(log_directory)
        self.segment_path = self.log_directory / segment_filename
        self.fsync_every_n_events = fsync_every_n_events

        # Create log directory if needed
        self.log_directory.mkdir(parents=True, exist_ok=True)

        # Initialize chain state
        self.event_seq = 0
        self.prev_event_hash = GENESIS_HASH  # Genesis value for first event
        self.events_since_fsync = 0

        # Thread safety
        self._lock = threading.Lock()

        # Open segment file in append mode
        self._segment_file = open(self.segment_path, 'a', encoding='utf-8')

    def ingest_event(
        self,
        event_type: str,
        actor: str,
        correlation: dict,
        payload: dict,
        timestamp: Optional[str] = None,
    ) -> dict:
        """
        Ingest an event into the audit log.

        This is the main entry point for logging events.

        Steps:
        1. Validate event_type against closed enum
        2. Redact secrets from payload
        3. Assign monotonic event_seq
        4. Compute payload_hash
        5. Compute event_id
        6. Compute event_hash
        7. Build hash chain (prev_event_hash)
        8. Sign event_hash
        9. Append JSONL line
        10. Flush/fsync based on policy

        Args:
            event_type: Event type (must be in VALID_EVENT_TYPES)
            actor: Module/component name
            correlation: Correlation IDs (session_id, message_id, task_id)
            payload: Event-specific data
            timestamp: RFC3339 timestamp (or None for frozen time)

        Returns:
            Complete audit event dict (as persisted)

        Raises:
            InvalidEventTypeError: If event_type not in closed enum
            SecretLeakError: If secrets remain after redaction
        """
        with self._lock:
            # Step 1: Validate event_type
            if event_type not in VALID_EVENT_TYPES:
                raise InvalidEventTypeError(
                    f"Unknown event_type: {event_type}. "
                    f"Must be one of {sorted(VALID_EVENT_TYPES)}"
                )

            # Step 2: Redact secrets from payload
            redacted_payload, redacted_paths = redact(payload)
            redaction_metadata = create_redaction_metadata(
                was_redacted=len(redacted_paths) > 0,
                redacted_paths=redacted_paths
            )

            # Verify all redacted values are literally "REDACTED" (no leaks)
            if redacted_paths:
                for path in redacted_paths:
                    # Navigate to the value at this path
                    parts = [p for p in path.split('/') if p]
                    obj = redacted_payload
                    for part in parts[:-1]:  # Navigate to parent
                        if part.isdigit():
                            obj = obj[int(part)]
                        else:
                            obj = obj[part]

                    # Check final value
                    final_key = parts[-1]
                    if final_key.isdigit():
                        value = obj[int(final_key)]
                    else:
                        value = obj[final_key]

                    if value != "REDACTED":
                        raise SecretLeakError(
                            f"Secret at {path} not properly redacted: {value}"
                        )

            # Step 3: Assign monotonic event_seq
            self.event_seq += 1

            # Step 4: Compute payload_hash
            payload_hash = compute_payload_hash(redacted_payload)

            # Step 5: Compute event_id
            event_id = compute_event_id(
                run_id=self.run_id,
                event_seq=self.event_seq,
                event_type=event_type,
                actor=actor,
                correlation=correlation,
                payload_hash=payload_hash
            )

            # Handle timestamp based on time_policy
            if timestamp is None:
                if self.time_policy == "frozen":
                    timestamp = None  # Null for frozen time
                else:
                    timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            # Build event body (without event_hash and signature)
            event_body = {
                "schema_id": "relay.audit_event",
                "schema_version": "1.0.0",
                "run_id": self.run_id,
                "event_seq": self.event_seq,
                "event_id": event_id,
                "event_type": event_type,
                "timestamp": timestamp,
                "actor": actor,
                "correlation": correlation,
                "payload": redacted_payload,
                "payload_hash": payload_hash,
                "prev_event_hash": self.prev_event_hash,
                "redaction": redaction_metadata,
            }

            # Step 6: Compute event_hash (excludes event_hash and signature)
            event_hash = compute_event_hash(event_body)

            # Step 7: Sign event_hash
            signature = sign_event_hash(self.key_manager.private_key, event_hash)

            # Add event_hash and signature to complete event
            complete_event = {
                **event_body,
                "event_hash": event_hash,
                "signature": signature,
            }

            # Step 8: Append JSONL line
            jsonl_line = json.dumps(complete_event, ensure_ascii=False, separators=(',', ':'))
            self._segment_file.write(jsonl_line + '\n')

            # Step 9: Flush and fsync policy
            self._segment_file.flush()  # Flush after every event
            self.events_since_fsync += 1

            # Fsync policy: immediate for critical events, or every N events
            if event_type in CRITICAL_EVENT_TYPES or self.events_since_fsync >= self.fsync_every_n_events:
                self._segment_file.flush()
                import os
                os.fsync(self._segment_file.fileno())
                self.events_since_fsync = 0

            # Step 10: Update chain state for next event
            self.prev_event_hash = event_hash

            return complete_event

    def close(self):
        """
        Close the log daemon and flush any pending writes.
        """
        with self._lock:
            if self._segment_file:
                self._segment_file.flush()
                import os
                os.fsync(self._segment_file.fileno())
                self._segment_file.close()
                self._segment_file = None

    def get_current_state(self) -> dict:
        """
        Get current daemon state for debugging/monitoring.

        Returns:
            Dict with: event_seq, prev_event_hash, events_since_fsync
        """
        with self._lock:
            return {
                "event_seq": self.event_seq,
                "prev_event_hash": self.prev_event_hash,
                "events_since_fsync": self.events_since_fsync,
                "segment_path": str(self.segment_path),
            }

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure close is called."""
        self.close()
        return False
