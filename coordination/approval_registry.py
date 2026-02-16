"""Approval registry for single-use token tracking.

Phase 4 Invariants:
- Tokens are single-use only
- Expired tokens are automatically rejected
- Used tokens cannot be reused
"""

from typing import Optional
from dataclasses import dataclass, field
from coordination.approval_tokens import ApprovalToken


@dataclass
class ApprovalRecord:
    """Record of approval token usage."""
    approval_id: str
    token: ApprovalToken
    used: bool = False
    used_by_task_id: Optional[str] = None
    used_at_event_seq: Optional[int] = None


class TokenAlreadyUsedError(Exception):
    """Raised when attempting to use already-consumed token."""
    pass


class TokenExpiredError(Exception):
    """Raised when token has expired."""
    pass


class TokenNotFoundError(Exception):
    """Raised when token not found in registry."""
    pass


class ApprovalRegistry:
    """Registry for approval tokens.

    Phase 4 Invariants:
    - Single-use enforcement
    - Expiry checking
    - In-memory state, rebuilt from Phase 3 audit ledger
    """

    def __init__(self):
        """Initialize approval registry."""
        # approval_id -> ApprovalRecord
        self.approvals: dict[str, ApprovalRecord] = {}

        # Current event_seq (updated from LogDaemon)
        self.current_event_seq = 0

    def update_event_seq(self, event_seq: int):
        """Update current event_seq from LogDaemon.

        Args:
            event_seq: Current event_seq
        """
        self.current_event_seq = event_seq

    def register_token(self, token: ApprovalToken):
        """Register approval token.

        Args:
            token: ApprovalToken to register

        Raises:
            ValueError: If token already registered
        """
        if token.approval_id in self.approvals:
            raise ValueError(f"Token {token.approval_id} already registered")

        record = ApprovalRecord(
            approval_id=token.approval_id,
            token=token,
            used=False
        )

        self.approvals[token.approval_id] = record

    def consume_token(
        self,
        approval_id: str,
        task_id: str
    ) -> ApprovalToken:
        """Consume approval token (single-use).

        Args:
            approval_id: Approval ID
            task_id: Task consuming the token

        Returns:
            ApprovalToken

        Raises:
            TokenNotFoundError: If token not found
            TokenAlreadyUsedError: If token already used
            TokenExpiredError: If token expired
        """
        if approval_id not in self.approvals:
            raise TokenNotFoundError(f"Token {approval_id} not found")

        record = self.approvals[approval_id]

        # Check if already used
        if record.used:
            raise TokenAlreadyUsedError(
                f"Token {approval_id} already used by task {record.used_by_task_id} "
                f"at event_seq {record.used_at_event_seq}"
            )

        # Check expiry
        if self.is_expired(record.token):
            raise TokenExpiredError(
                f"Token {approval_id} expired at event_seq {record.token.expires_event_seq} "
                f"(current: {self.current_event_seq})"
            )

        # Mark as used
        record.used = True
        record.used_by_task_id = task_id
        record.used_at_event_seq = self.current_event_seq

        return record.token

    def is_expired(self, token: ApprovalToken) -> bool:
        """Check if token has expired.

        Args:
            token: ApprovalToken

        Returns:
            True if expired, False otherwise
        """
        return self.current_event_seq >= token.expires_event_seq

    def is_token_available(self, approval_id: str) -> bool:
        """Check if token is available (exists, not used, not expired).

        Args:
            approval_id: Approval ID

        Returns:
            True if available, False otherwise
        """
        if approval_id not in self.approvals:
            return False

        record = self.approvals[approval_id]

        # Check if used
        if record.used:
            return False

        # Check if expired
        if self.is_expired(record.token):
            return False

        return True

    def get_token(self, approval_id: str) -> Optional[ApprovalToken]:
        """Get token without consuming.

        Args:
            approval_id: Approval ID

        Returns:
            ApprovalToken if found, None otherwise
        """
        if approval_id not in self.approvals:
            return None

        return self.approvals[approval_id].token

    def get_token_status(self, approval_id: str) -> Optional[dict]:
        """Get status of token.

        Args:
            approval_id: Approval ID

        Returns:
            Status dict if found, None otherwise
        """
        if approval_id not in self.approvals:
            return None

        record = self.approvals[approval_id]

        return {
            "approval_id": approval_id,
            "action": record.token.action,
            "approver_principal": record.token.approver_principal,
            "issued_event_seq": record.token.issued_event_seq,
            "expires_event_seq": record.token.expires_event_seq,
            "used": record.used,
            "used_by_task_id": record.used_by_task_id,
            "used_at_event_seq": record.used_at_event_seq,
            "expired": self.is_expired(record.token),
            "available": self.is_token_available(approval_id)
        }

    def get_all_tokens_for_action(self, action: str) -> list[ApprovalToken]:
        """Get all available tokens for action.

        Args:
            action: Action identifier

        Returns:
            List of available ApprovalTokens
        """
        tokens = []

        for record in self.approvals.values():
            if record.token.action != action:
                continue

            if not self.is_token_available(record.approval_id):
                continue

            tokens.append(record.token)

        return tokens

    def cleanup_expired_tokens(self) -> int:
        """Remove expired and used tokens from registry.

        Returns:
            Number of tokens removed
        """
        to_remove = []

        for approval_id, record in self.approvals.items():
            # Remove if used or expired
            if record.used or self.is_expired(record.token):
                to_remove.append(approval_id)

        for approval_id in to_remove:
            del self.approvals[approval_id]

        return len(to_remove)

    def get_registry_snapshot(self) -> dict[str, dict]:
        """Get snapshot of registry for debugging/audit.

        Returns:
            Dict of approval_id -> status dict
        """
        snapshot = {}

        for approval_id in self.approvals:
            snapshot[approval_id] = self.get_token_status(approval_id)

        return snapshot
