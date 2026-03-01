"""
Audit log verification and tamper detection for Phase 3.

Responsibilities:
- Verify hash chain integrity (GLOBAL across all runs)
- Verify Ed25519 signatures on all events
- Detect tampering (chain mismatch, signature invalid)
- Verify per-run event_seq monotonicity
- Validate against schemas
"""

import json
from pathlib import Path
from typing import Optional

from audit_logging.canonicalize import compute_event_hash, compute_payload_hash, compute_event_id
from audit_logging.crypto import verify_signature
from audit_logging.key_manager import KeyManager
from audit_logging.log_daemon import GENESIS_HASH, VALID_EVENT_TYPES


class VerificationError(Exception):
    """Base class for verification errors."""
    pass


class HashChainMismatchError(VerificationError):
    """Raised when hash chain is broken."""
    pass


class SignatureInvalidError(VerificationError):
    """Raised when event signature is invalid."""
    pass


class EventIdMismatchError(VerificationError):
    """Raised when event_id doesn't match computed value."""
    pass


class SequenceMismatchError(VerificationError):
    """Raised when event_seq is not strictly monotonic."""
    pass


class VerificationResult:
    """
    Result of audit log verification.

    Attributes:
        success: True if verification passed
        events_verified: Number of events verified
        segments_verified: Number of segments verified
        errors: List of error messages (if any)
        tamper_detected: True if tampering detected
    """

    def __init__(
        self,
        success: bool,
        events_verified: int = 0,
        segments_verified: int = 0,
        errors: Optional[list[str]] = None,
        tamper_detected: bool = False,
    ):
        self.success = success
        self.events_verified = events_verified
        self.segments_verified = segments_verified
        self.errors = errors or []
        self.tamper_detected = tamper_detected

    def __repr__(self):
        return (
            f"VerificationResult(success={self.success}, "
            f"events={self.events_verified}, segments={self.segments_verified}, "
            f"errors={len(self.errors)}, tamper={self.tamper_detected})"
        )


class AuditLogVerifier:
    """
    Verifies audit log integrity and detects tampering.

    Attributes:
        key_manager: KeyManager for signature verification
    """

    def __init__(self, key_manager: KeyManager):
        """
        Initialize verifier.

        Args:
            key_manager: KeyManager with public key for verification
        """
        self.key_manager = key_manager

    def verify_segment(self, segment_path: Path) -> VerificationResult:
        """
        Verify a single segment file.

        Checks:
        - Each event has valid JSON
        - event_seq is strictly monotonic PER RUN
        - Hash chain is unbroken (GLOBAL)
        - Signatures are valid
        - event_id matches computed value
        - event_type is valid

        Args:
            segment_path: Path to segment file

        Returns:
            VerificationResult
        """
        if not segment_path.exists():
            return VerificationResult(
                success=False,
                errors=[f"Segment not found: {segment_path}"]
            )

        errors = []
        events_verified = 0
        prev_event_hash = GENESIS_HASH
        expected_seq_by_run_id = {}  # Per-run sequence tracking

        try:
            with open(segment_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue  # Skip empty lines

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as e:
                        errors.append(f"Line {line_num}: Invalid JSON: {e}")
                        continue

                    # Verify event structure
                    error = self._verify_event(event, expected_seq_by_run_id, prev_event_hash, line_num)
                    if error:
                        errors.append(f"Line {line_num}: {error}")
                        if "chain mismatch" in error.lower() or "signature" in error.lower():
                            return VerificationResult(
                                success=False,
                                events_verified=events_verified,
                                segments_verified=0,
                                errors=errors,
                                tamper_detected=True
                            )
                        continue

                    # Update chain state (GLOBAL)
                    prev_event_hash = event["event_hash"]
                    events_verified += 1

        except Exception as e:
            errors.append(f"Error reading segment: {e}")
            return VerificationResult(
                success=False,
                events_verified=events_verified,
                errors=errors
            )

        if errors:
            return VerificationResult(
                success=False,
                events_verified=events_verified,
                errors=errors
            )

        return VerificationResult(
            success=True,
            events_verified=events_verified,
            segments_verified=1
        )

    def _verify_event(
        self,
        event: dict,
        expected_seq_by_run_id: dict,
        expected_prev_hash: str,
        line_num: int = 0
    ) -> Optional[str]:
        """
        Verify a single event.

        Args:
            event: Event dict
            expected_seq_by_run_id: Dict tracking expected event_seq per run_id
            expected_prev_hash: Expected prev_event_hash (GLOBAL chain)
            line_num: Line number for error reporting

        Returns:
            Error message if verification failed, None if success
        """
        # Check prev_event_hash matches GLOBAL chain
        if event.get("prev_event_hash") != expected_prev_hash:
            return f"Hash chain mismatch: expected {expected_prev_hash}, got {event.get('prev_event_hash')}"

        # Verify event_type is valid
        if event.get("event_type") not in VALID_EVENT_TYPES:
            return f"Invalid event_type: {event.get('event_type')}"

        # Verify per-run event_seq monotonicity
        run_id = event.get("run_id")
        event_seq = event.get("event_seq")

        if run_id and isinstance(event_seq, int):
            if run_id not in expected_seq_by_run_id:
                # First event for this run
                if event_seq != 1:
                    return f"event_seq mismatch: run_id={run_id} first event must be 1, got {event_seq}"
                expected_seq_by_run_id[run_id] = 2
            else:
                # Subsequent event for this run
                expected_seq = expected_seq_by_run_id[run_id]
                if event_seq != expected_seq:
                    event_type = event.get("event_type", "UNKNOWN")
                    return f"event_seq mismatch: run_id={run_id} expected {expected_seq}, got {event_seq} (event={event_type})"
                expected_seq_by_run_id[run_id] = event_seq + 1

        # Recompute event_hash and verify
        computed_event_hash = compute_event_hash(event)
        if event.get("event_hash") != computed_event_hash:
            return f"event_hash mismatch: computed {computed_event_hash}, stored {event.get('event_hash')}"

        # Recompute payload_hash and verify
        computed_payload_hash = compute_payload_hash(event.get("payload", {}))
        if event.get("payload_hash") != computed_payload_hash:
            return f"payload_hash mismatch: computed {computed_payload_hash}, stored {event.get('payload_hash')}"

        # Recompute event_id and verify
        computed_event_id = compute_event_id(
            run_id=event.get("run_id"),
            event_seq=event.get("event_seq"),
            event_type=event.get("event_type"),
            actor=event.get("actor"),
            correlation=event.get("correlation", {}),
            payload_hash=event.get("payload_hash")
        )
        if event.get("event_id") != computed_event_id:
            return f"event_id mismatch: computed {computed_event_id}, stored {event.get('event_id')}"

        # Verify signature
        signature_valid = verify_signature(
            self.key_manager.public_key,
            event.get("event_hash"),
            event.get("signature")
        )
        if not signature_valid:
            return f"Signature invalid for event_seq {event.get('event_seq')}"

        return None

    def verify_chain(
        self,
        segment_paths: list[Path],
        manifest_path: Optional[Path] = None
    ) -> VerificationResult:
        """
        Verify entire audit log chain across multiple segments.

        Maintains:
        - GLOBAL hash chain across all runs
        - PER-RUN event_seq monotonicity

        Args:
            segment_paths: List of segment paths in order
            manifest_path: Optional path to manifest.json for validation

        Returns:
            VerificationResult
        """
        total_events = 0
        total_segments = 0
        all_errors = []
        prev_event_hash = GENESIS_HASH
        expected_seq_by_run_id = {}  # Per-run sequence tracking

        for segment_path in segment_paths:
            if not segment_path.exists():
                all_errors.append(f"Segment not found: {segment_path}")
                continue

            # Read segment
            try:
                with open(segment_path, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as e:
                            all_errors.append(f"{segment_path.name} line {line_num}: Invalid JSON: {e}")
                            continue

                        # Verify event with chain context
                        error = self._verify_event(event, expected_seq_by_run_id, prev_event_hash, line_num)
                        if error:
                            all_errors.append(f"{segment_path.name} line {line_num}: {error}")
                            if "chain mismatch" in error.lower() or "signature" in error.lower():
                                return VerificationResult(
                                    success=False,
                                    events_verified=total_events,
                                    segments_verified=total_segments,
                                    errors=all_errors,
                                    tamper_detected=True
                                )
                            continue

                        # Update GLOBAL chain state
                        prev_event_hash = event["event_hash"]
                        total_events += 1

                total_segments += 1

            except Exception as e:
                all_errors.append(f"Error reading {segment_path.name}: {e}")

        # Verify against manifest if provided
        if manifest_path and manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)

                # Verify event count
                if manifest.get("event_count_total") != total_events:
                    all_errors.append(
                        f"Manifest event count mismatch: manifest says {manifest.get('event_count_total')}, "
                        f"verified {total_events}"
                    )

            except Exception as e:
                all_errors.append(f"Error reading manifest: {e}")

        if all_errors:
            return VerificationResult(
                success=False,
                events_verified=total_events,
                segments_verified=total_segments,
                errors=all_errors,
                tamper_detected="chain mismatch" in str(all_errors).lower() or "signature" in str(all_errors).lower()
            )

        return VerificationResult(
            success=True,
            events_verified=total_events,
            segments_verified=total_segments
        )
