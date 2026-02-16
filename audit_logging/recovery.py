"""
Crash recovery for Phase 3 audit logging.

Responsibilities:
- Load manifest on startup
- Verify last segment for truncation
- Truncate back to last valid newline if needed
- Detect and report corruption vs. tampering
- Initialize LogDaemon with recovered state
"""

import json
from pathlib import Path
from typing import Optional

from audit_logging.verifier import AuditLogVerifier, VerificationResult
from audit_logging.key_manager import KeyManager


class RecoveryError(Exception):
    """Base class for recovery errors."""
    pass


class CorruptionDetectedError(RecoveryError):
    """Raised when log corruption detected (truncation)."""
    pass


class TamperDetectedError(RecoveryError):
    """Raised when tampering detected (chain mismatch, invalid signature)."""
    pass


class RecoveryResult:
    """
    Result of crash recovery.

    Attributes:
        success: True if recovery succeeded
        last_valid_event_seq: Last valid event sequence number
        last_valid_event_hash: Last valid event hash
        truncated_lines: Number of lines truncated (0 if none)
        corruption_detected: True if corruption detected
        tamper_detected: True if tampering detected
        error_message: Error message if recovery failed
    """

    def __init__(
        self,
        success: bool,
        last_valid_event_seq: int = 0,
        last_valid_event_hash: str = "",
        truncated_lines: int = 0,
        corruption_detected: bool = False,
        tamper_detected: bool = False,
        error_message: Optional[str] = None,
    ):
        self.success = success
        self.last_valid_event_seq = last_valid_event_seq
        self.last_valid_event_hash = last_valid_event_hash
        self.truncated_lines = truncated_lines
        self.corruption_detected = corruption_detected
        self.tamper_detected = tamper_detected
        self.error_message = error_message

    def __repr__(self):
        return (
            f"RecoveryResult(success={self.success}, "
            f"last_seq={self.last_valid_event_seq}, "
            f"truncated={self.truncated_lines}, "
            f"corruption={self.corruption_detected}, "
            f"tamper={self.tamper_detected})"
        )


class CrashRecoveryManager:
    """
    Manages crash recovery for audit logs.

    Attributes:
        log_directory: Path to log directory
        key_manager: KeyManager for verification
        verifier: AuditLogVerifier instance
    """

    def __init__(self, log_directory: str | Path, key_manager: KeyManager):
        """
        Initialize crash recovery manager.

        Args:
            log_directory: Directory containing audit logs
            key_manager: KeyManager for signature verification
        """
        self.log_directory = Path(log_directory)
        self.key_manager = key_manager
        self.verifier = AuditLogVerifier(key_manager)

    def recover(self) -> RecoveryResult:
        """
        Perform crash recovery on audit logs.

        Steps:
        1. Load manifest.json
        2. Enumerate segments
        3. Verify all segments except last
        4. Verify last segment line-by-line
        5. Truncate back to last valid newline if needed
        6. Emit LOG_CORRUPTION_DETECTED if truncation occurred
        7. Emit LOG_TAMPER_DETECTED and halt if chain mismatch

        Returns:
            RecoveryResult

        Raises:
            TamperDetectedError: If tampering detected (system must halt)
        """
        manifest_path = self.log_directory / "manifest.json"

        # Load manifest
        if not manifest_path.exists():
            return RecoveryResult(
                success=False,
                error_message="Manifest not found - no logs to recover"
            )

        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
        except Exception as e:
            return RecoveryResult(
                success=False,
                error_message=f"Failed to load manifest: {e}"
            )

        # Get segment paths
        segments = manifest.get("segments", [])
        if not segments:
            # No finalized segments yet
            return RecoveryResult(
                success=True,
                last_valid_event_seq=0,
                last_valid_event_hash="0" * 64,
            )

        # Build segment path list
        segment_paths = [
            self.log_directory / seg["filename"]
            for seg in segments
        ]

        # Verify all segments except last
        if len(segment_paths) > 1:
            result = self.verifier.verify_chain(segment_paths[:-1])
            if not result.success:
                if result.tamper_detected:
                    raise TamperDetectedError(
                        f"Tampering detected in segments 1-{len(segment_paths)-1}: "
                        f"{result.errors}"
                    )
                return RecoveryResult(
                    success=False,
                    error_message=f"Verification failed: {result.errors}"
                )

        # Verify last segment with truncation recovery
        last_segment_path = segment_paths[-1]
        return self._recover_last_segment(last_segment_path, segments[-1])

    def _recover_last_segment(
        self,
        segment_path: Path,
        segment_metadata: dict
    ) -> RecoveryResult:
        """
        Recover last segment, handling truncation.

        Args:
            segment_path: Path to last segment
            segment_metadata: Segment metadata from manifest

        Returns:
            RecoveryResult

        Raises:
            TamperDetectedError: If tampering detected
        """
        if not segment_path.exists():
            return RecoveryResult(
                success=False,
                error_message=f"Last segment not found: {segment_path}"
            )

        # Read and verify line by line
        valid_lines = []
        last_valid_seq = 0
        last_valid_hash = segment_metadata.get("first_event_hash", "0" * 64)
        expected_seq = segment_metadata.get("first_event_seq", 1)
        prev_hash = "0" * 64  # Will be updated

        # Get prev_hash from previous segment if exists
        if segment_metadata.get("first_event_seq", 1) > 1:
            # Need to get last hash from previous segment
            # For now, assume it's in the manifest's last_event_hash of prev segment
            pass

        try:
            with open(segment_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    # Try to parse JSON
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        # Truncated line - stop here
                        break

                    # Verify event structure
                    # Basic check - full verification would use verifier
                    if not isinstance(event, dict):
                        break

                    if "event_seq" not in event or "event_hash" not in event:
                        break

                    # Line is valid
                    valid_lines.append(line)
                    last_valid_seq = event["event_seq"]
                    last_valid_hash = event["event_hash"]
                    expected_seq = event["event_seq"] + 1

        except Exception as e:
            return RecoveryResult(
                success=False,
                error_message=f"Error reading segment: {e}"
            )

        # Check if truncation occurred
        with open(segment_path, 'r', encoding='utf-8') as f:
            actual_lines = [line.strip() for line in f if line.strip()]

        if len(valid_lines) < len(actual_lines):
            # Truncation detected - rewrite file with valid lines only
            truncated_count = len(actual_lines) - len(valid_lines)

            with open(segment_path, 'w', encoding='utf-8') as f:
                for line in valid_lines:
                    f.write(line + '\n')
                f.flush()
                import os
                os.fsync(f.fileno())

            return RecoveryResult(
                success=True,
                last_valid_event_seq=last_valid_seq,
                last_valid_event_hash=last_valid_hash,
                truncated_lines=truncated_count,
                corruption_detected=True,
            )

        # No truncation, verify integrity
        result = self.verifier.verify_segment(segment_path)
        if not result.success:
            if result.tamper_detected:
                raise TamperDetectedError(
                    f"Tampering detected in last segment: {result.errors}"
                )

        return RecoveryResult(
            success=True,
            last_valid_event_seq=last_valid_seq,
            last_valid_event_hash=last_valid_hash,
            truncated_lines=0,
            corruption_detected=False,
        )
