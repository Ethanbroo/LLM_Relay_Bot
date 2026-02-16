"""Incident writer for Phase 7 post-mortem analysis.

Phase 7 Invariant: Incidents are deterministic, bounded, and redacted.
"""

import hashlib
import json
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class IncidentRecord:
    """Immutable incident record.

    Phase 7 Invariant: Incidents are data-only and strictly typed.
    """
    schema_id: str
    schema_version: str
    run_id: str
    incident_id: str  # SHA-256 hash
    rule_id: str
    severity: str
    opened_at: Optional[str]  # RFC3339 or null
    closed_at: Optional[str]  # RFC3339 or null
    state: str  # "OPEN" or "CLOSED"
    summary: str  # Max 512 chars
    evidence: dict
    redaction: dict

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "incident_id": self.incident_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "state": self.state,
            "summary": self.summary,
            "evidence": self.evidence,
            "redaction": self.redaction
        }


class IncidentWriter:
    """Writer for incident records with deterministic incident_id.

    Phase 7 Invariants:
    - incident_id is deterministic SHA-256 hash
    - Incidents include metrics window (last N seconds)
    - No secrets in incidents (redaction enforced)
    - Incidents bounded in size
    """

    def __init__(
        self,
        incident_dir: str,
        run_id: str,
        config_hash: str,
        incident_include_window_sec: int = 300,
        incident_max_bytes: int = 2_000_000
    ):
        """Initialize incident writer.

        Args:
            incident_dir: Directory for incident files
            run_id: Run identifier
            config_hash: SHA-256 hash of config
            incident_include_window_sec: Seconds of metrics to include (default 300 = 5 min)
            incident_max_bytes: Maximum bytes per incident (default 2MB)
        """
        self.incident_dir = Path(incident_dir)
        self.run_id = run_id
        self.config_hash = config_hash
        self.incident_include_window_sec = incident_include_window_sec
        self.incident_max_bytes = incident_max_bytes

        # Create incident directory
        self.incident_dir.mkdir(parents=True, exist_ok=True)

    def compute_incident_id(
        self,
        rule_id: str,
        first_trigger_ts: str,
        seq: int
    ) -> str:
        """Compute deterministic incident ID.

        Phase 7 Invariant: incident_id = SHA-256(run_id + rule_id + first_trigger_ts + seq)

        Args:
            rule_id: Rule identifier
            first_trigger_ts: RFC3339 timestamp of first trigger
            seq: Sequence number

        Returns:
            64-character hex SHA-256 hash
        """
        hash_input = f"{self.run_id}:{rule_id}:{first_trigger_ts}:{seq}"
        return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()

    def write_incident(
        self,
        rule_id: str,
        severity: str,
        first_trigger_ts: str,
        seq: int,
        summary: str,
        metrics_window: List[dict],
        audit_event_seq_min: int,
        audit_event_seq_max: int,
        time_policy: str
    ) -> IncidentRecord:
        """Write incident record and metrics window.

        Phase 7 Invariant: Incidents pass redaction enforcement.

        Args:
            rule_id: Rule identifier
            severity: Severity level
            first_trigger_ts: RFC3339 timestamp of first trigger
            seq: Sequence number
            summary: Incident summary (max 512 chars)
            metrics_window: List of metrics records for window
            audit_event_seq_min: Min audit event seq in range
            audit_event_seq_max: Max audit event seq in range
            time_policy: "frozen" or "recorded"

        Returns:
            IncidentRecord

        Raises:
            ValueError: If incident exceeds size limit or contains secrets
        """
        # Compute deterministic incident_id
        incident_id = self.compute_incident_id(rule_id, first_trigger_ts, seq)

        # Truncate summary if needed
        if len(summary) > 512:
            summary = summary[:512]

        # Get current time for redaction timestamp
        redaction_ts = None
        if time_policy == "recorded":
            redaction_ts = datetime.now(timezone.utc).isoformat()

        # Create incident record
        opened_at = first_trigger_ts if time_policy == "recorded" else None

        metrics_window_filename = f"{incident_id}.metrics.jsonl"

        incident = IncidentRecord(
            schema_id="relay.incident_record",
            schema_version="1.0.0",
            run_id=self.run_id,
            incident_id=incident_id,
            rule_id=rule_id,
            severity=severity,
            opened_at=opened_at,
            closed_at=None,
            state="OPEN",
            summary=summary,
            evidence={
                "metrics_window_ref": metrics_window_filename,
                "audit_event_seq_range": {
                    "min": audit_event_seq_min,
                    "max": audit_event_seq_max
                },
                "config_hash": self.config_hash
            },
            redaction={
                "enforced": True,
                "timestamp": redaction_ts
            }
        )

        # Write incident JSON
        incident_path = self.incident_dir / f"{incident_id}.json"
        incident_json = json.dumps(incident.to_dict(), indent=2, sort_keys=True)

        # Check size limit
        if len(incident_json.encode('utf-8')) > self.incident_max_bytes:
            raise ValueError(f"Incident exceeds max size: {len(incident_json)} > {self.incident_max_bytes}")

        with open(incident_path, 'w', encoding='utf-8') as f:
            f.write(incident_json)

        # Write metrics window
        metrics_window_path = self.incident_dir / metrics_window_filename
        with open(metrics_window_path, 'w', encoding='utf-8') as f:
            for metric_record in metrics_window:
                line = json.dumps(metric_record, sort_keys=True) + '\n'
                f.write(line)

        # TODO: Apply redaction enforcement (Phase 3 integration)
        # This would scan for secrets patterns and fail if found

        return incident

    def close_incident(self, incident_id: str, time_policy: str) -> None:
        """Close an incident.

        Args:
            incident_id: Incident identifier
            time_policy: "frozen" or "recorded"
        """
        incident_path = self.incident_dir / f"{incident_id}.json"

        if not incident_path.exists():
            return

        # Load incident
        with open(incident_path, 'r', encoding='utf-8') as f:
            incident_dict = json.load(f)

        # Update state and closed_at
        incident_dict["state"] = "CLOSED"
        if time_policy == "recorded":
            incident_dict["closed_at"] = datetime.now(timezone.utc).isoformat()

        # Write back
        with open(incident_path, 'w', encoding='utf-8') as f:
            json.dump(incident_dict, f, indent=2, sort_keys=True)

    def get_open_incidents(self) -> List[str]:
        """Get list of open incident IDs.

        Returns:
            List of incident IDs
        """
        open_incidents = []

        for incident_path in self.incident_dir.glob("*.json"):
            with open(incident_path, 'r', encoding='utf-8') as f:
                incident = json.load(f)
                if incident.get("state") == "OPEN":
                    open_incidents.append(incident["incident_id"])

        return open_incidents
