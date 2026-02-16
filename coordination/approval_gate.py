"""Approval gate for action approval verification.

Phase 4 Invariants:
- Actions requiring approval must have valid token
- Token signature verified with Ed25519
- Token payload hash matches action payload
- Token is single-use only
"""

from typing import Optional
from dataclasses import dataclass

from coordination.approval_tokens import (
    ApprovalToken,
    ApprovalTokenVerifier,
    verify_payload_match
)
from coordination.approval_registry import (
    ApprovalRegistry,
    TokenAlreadyUsedError,
    TokenExpiredError,
    TokenNotFoundError
)


@dataclass
class ApprovalCheckResult:
    """Result of approval check."""
    approved: bool
    approval_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ApprovalGate:
    """Gate for verifying action approvals.

    Phase 4 Invariant: Actions requiring approval must pass gate.
    """

    def __init__(
        self,
        approval_registry: ApprovalRegistry,
        token_verifier: ApprovalTokenVerifier,
        log_daemon=None
    ):
        """Initialize approval gate.

        Args:
            approval_registry: ApprovalRegistry instance
            token_verifier: ApprovalTokenVerifier instance
            log_daemon: Optional LogDaemon for audit events
        """
        self.approval_registry = approval_registry
        self.token_verifier = token_verifier
        self.log_daemon = log_daemon

    def check_approval(
        self,
        action: str,
        payload: dict,
        approval_id: Optional[str],
        task_id: str,
        requires_approval: bool
    ) -> ApprovalCheckResult:
        """Check if action is approved.

        Emits audit events:
        - APPROVAL_VERIFIED (if approved)
        - APPROVAL_REJECTED (if rejected)

        Args:
            action: Action identifier
            payload: Action payload
            approval_id: Approval ID (if provided)
            task_id: Task ID
            requires_approval: Whether action requires approval

        Returns:
            ApprovalCheckResult
        """
        # If action doesn't require approval, pass
        if not requires_approval:
            return ApprovalCheckResult(approved=True)

        # If no approval_id provided but required
        if approval_id is None:
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "error_code": "APPROVAL_REQUIRED",
                    "error_message": "Action requires approval but no approval_id provided"
                }
            )

            return ApprovalCheckResult(
                approved=False,
                error_code="APPROVAL_REQUIRED",
                error_message="Action requires approval but no approval_id provided"
            )

        # Get token from registry
        try:
            token = self.approval_registry.consume_token(
                approval_id=approval_id,
                task_id=task_id
            )
        except TokenNotFoundError:
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "approval_id": approval_id,
                    "error_code": "TOKEN_NOT_FOUND",
                    "error_message": f"Approval token {approval_id} not found"
                }
            )

            return ApprovalCheckResult(
                approved=False,
                approval_id=approval_id,
                error_code="TOKEN_NOT_FOUND",
                error_message=f"Approval token {approval_id} not found"
            )

        except TokenAlreadyUsedError as e:
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "approval_id": approval_id,
                    "error_code": "TOKEN_ALREADY_USED",
                    "error_message": str(e)
                }
            )

            return ApprovalCheckResult(
                approved=False,
                approval_id=approval_id,
                error_code="TOKEN_ALREADY_USED",
                error_message=str(e)
            )

        except TokenExpiredError as e:
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "approval_id": approval_id,
                    "error_code": "TOKEN_EXPIRED",
                    "error_message": str(e)
                }
            )

            return ApprovalCheckResult(
                approved=False,
                approval_id=approval_id,
                error_code="TOKEN_EXPIRED",
                error_message=str(e)
            )

        # Verify signature
        if not self.token_verifier.verify_token(token):
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "approval_id": approval_id,
                    "error_code": "INVALID_SIGNATURE",
                    "error_message": "Token signature verification failed"
                }
            )

            return ApprovalCheckResult(
                approved=False,
                approval_id=approval_id,
                error_code="INVALID_SIGNATURE",
                error_message="Token signature verification failed"
            )

        # Verify action matches
        if token.action != action:
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "approval_id": approval_id,
                    "error_code": "ACTION_MISMATCH",
                    "error_message": f"Token for action {token.action}, got {action}"
                }
            )

            return ApprovalCheckResult(
                approved=False,
                approval_id=approval_id,
                error_code="ACTION_MISMATCH",
                error_message=f"Token for action {token.action}, got {action}"
            )

        # Verify payload hash matches
        if not verify_payload_match(token, payload):
            self._emit_audit_event(
                event_type="APPROVAL_REJECTED",
                payload={
                    "task_id": task_id,
                    "action": action,
                    "approval_id": approval_id,
                    "error_code": "PAYLOAD_HASH_MISMATCH",
                    "error_message": "Token payload hash does not match actual payload"
                }
            )

            return ApprovalCheckResult(
                approved=False,
                approval_id=approval_id,
                error_code="PAYLOAD_HASH_MISMATCH",
                error_message="Token payload hash does not match actual payload"
            )

        # All checks passed - emit APPROVAL_VERIFIED
        self._emit_audit_event(
            event_type="APPROVAL_VERIFIED",
            payload={
                "task_id": task_id,
                "action": action,
                "approval_id": approval_id,
                "approver_principal": token.approver_principal,
                "issued_event_seq": token.issued_event_seq,
                "expires_event_seq": token.expires_event_seq
            }
        )

        return ApprovalCheckResult(
            approved=True,
            approval_id=approval_id
        )

    def register_approval_token(self, token: ApprovalToken):
        """Register approval token in registry.

        Emits audit event:
        - APPROVAL_TOKEN_REGISTERED

        Args:
            token: ApprovalToken to register
        """
        self.approval_registry.register_token(token)

        self._emit_audit_event(
            event_type="APPROVAL_TOKEN_REGISTERED",
            payload={
                "approval_id": token.approval_id,
                "action": token.action,
                "approver_principal": token.approver_principal,
                "issued_event_seq": token.issued_event_seq,
                "expires_event_seq": token.expires_event_seq,
                "payload_hash": token.payload_hash
            }
        )

    def _emit_audit_event(self, event_type: str, payload: dict):
        """Emit audit event to LogDaemon.

        Args:
            event_type: Event type
            payload: Event payload
        """
        if self.log_daemon is None:
            return

        self.log_daemon.ingest_event(
            event_type=event_type,
            actor="approval_gate",
            correlation={"session_id": None, "message_id": None, "task_id": payload.get("task_id")},
            payload=payload
        )
