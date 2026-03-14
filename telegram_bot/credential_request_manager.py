"""
Credential request manager for browser agent credential-on-demand flow.

When the browser agent hits a login page and no credentials are available,
it creates a PendingCredentialRequest with an asyncio.Event, sends an
inline button to Telegram, and waits. When the user saves a credential
via the Mini App, the webapp API calls resolve_for_domain() to unblock
the agent.

Follows the same pattern as approval_manager.py.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CREDENTIAL_REQUEST_TIMEOUT_SECONDS = 300  # 5 minutes


class PendingCredentialRequest:
    """A single pending credential request from the browser agent."""

    __slots__ = (
        "request_id", "user_id", "domain", "task_id",
        "event", "fulfilled", "cancelled", "created_at",
    )

    def __init__(self, user_id: int, domain: str, task_id: str):
        self.request_id: str = secrets.token_hex(4)
        self.user_id = user_id
        self.domain = domain  # normalized domain
        self.task_id = task_id
        self.event = asyncio.Event()
        self.fulfilled: bool = False
        self.cancelled: bool = False
        self.created_at = datetime.now(timezone.utc)

    def resolve(self, fulfilled: bool = True):
        """Unblock the waiting coroutine."""
        self.fulfilled = fulfilled
        self.event.set()

    def cancel(self):
        """Cancel the request."""
        self.cancelled = True
        self.event.set()


class CredentialRequestManager:
    """Tracks pending credential requests across active browser tasks."""

    def __init__(self):
        # Key: "{user_id}:{normalized_domain}"
        self._pending: dict[str, PendingCredentialRequest] = {}

    def create(
        self, user_id: int, domain: str, task_id: str
    ) -> PendingCredentialRequest:
        """Create a new pending credential request."""
        key = f"{user_id}:{domain}"
        req = PendingCredentialRequest(user_id, domain, task_id)
        self._pending[key] = req
        return req

    def get_pending(
        self, user_id: int, domain: str
    ) -> PendingCredentialRequest | None:
        """Get a pending request for a specific user + domain."""
        key = f"{user_id}:{domain}"
        return self._pending.get(key)

    def remove(self, user_id: int, domain: str):
        """Remove a pending request (cleanup after resolution/timeout)."""
        key = f"{user_id}:{domain}"
        self._pending.pop(key, None)

    def resolve_for_domain(self, user_id: int, domain: str):
        """Called by webapp API when a credential is saved.

        Resolves the pending request if one exists for user_id + domain.
        """
        key = f"{user_id}:{domain}"
        pending = self._pending.get(key)
        if pending:
            logger.info(
                "Resolving credential request for user=%d domain=%s",
                user_id, domain,
            )
            pending.resolve(fulfilled=True)

    def cancel_all_for_task(self, task_id: str):
        """Cancel all pending credential requests for a given task."""
        to_remove = []
        for key, req in self._pending.items():
            if req.task_id == task_id:
                req.cancel()
                to_remove.append(key)
        for key in to_remove:
            del self._pending[key]
