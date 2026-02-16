"""Google Docs stub connector.

Phase 5 Invariant: Stub only - logs operations without real execution.
"""

import hashlib
import json
from typing import Optional

from connectors.base import BaseConnector, ConnectorRequest, ConnectorContext
from connectors.results import (
    ConnectorResult,
    ConnectorStatus,
    RollbackResult,
    RollbackStatus,
    ExecutionArtifact,
    VerificationMethod
)
from connectors.errors import ConnectorError


class GoogleDocsStubConnector(BaseConnector):
    """Google Docs stub connector for testing.

    Phase 5 Invariants:
    - Does not make real API calls
    - Simulates deterministic responses
    - Logs all operations for verification
    """

    connector_type = "google_docs_stub"

    def __init__(self):
        """Initialize Google Docs stub connector."""
        self._connected = False
        self._operations_log = []

    def connect(self, ctx: ConnectorContext) -> None:
        """Simulate connection to Google Docs API.

        Args:
            ctx: ConnectorContext
        """
        self._connected = True
        self._operations_log.append({
            "operation": "connect",
            "task_id": ctx.task_id,
            "attempt": ctx.attempt
        })

    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        """Execute simulated Google Docs operation.

        Supported actions:
        - gdocs.create_document: Create document (stub)
        - gdocs.update_document: Update document (stub)
        - gdocs.read_document: Read document (stub)
        - gdocs.share_document: Share document (stub)

        Args:
            req: ConnectorRequest

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If execution fails
        """
        if not self._connected:
            raise ConnectorError(
                "Connector not connected",
                error_code="NOT_CONNECTED"
            )

        # Parse payload
        payload = json.loads(req.payload_canonical)

        # Route to handler
        if req.action == "gdocs.create_document":
            return self._create_document(req, payload)
        elif req.action == "gdocs.update_document":
            return self._update_document(req, payload)
        elif req.action == "gdocs.read_document":
            return self._read_document(req, payload)
        elif req.action == "gdocs.share_document":
            return self._share_document(req, payload)
        else:
            raise ConnectorError(
                f"Unknown action: {req.action}",
                error_code="UNKNOWN_ACTION"
            )

    def rollback(
        self,
        req: ConnectorRequest,
        artifact: Optional[ExecutionArtifact]
    ) -> RollbackResult:
        """Simulate rollback of Google Docs operation.

        Args:
            req: Original ConnectorRequest
            artifact: ExecutionArtifact from execute

        Returns:
            RollbackResult
        """
        # Log rollback
        self._operations_log.append({
            "operation": "rollback",
            "action": req.action,
            "idempotency_key": req.idempotency_key,
            "has_artifact": artifact is not None
        })

        # Simulate successful rollback
        return RollbackResult(
            rollback_status=RollbackStatus.SUCCESS,
            verification_method=VerificationMethod.EXTERNAL_VERIFICATION,
            verification_artifact_hash=hashlib.sha256(
                f"rollback:{req.idempotency_key}".encode('utf-8')
            ).hexdigest(),
            notes="Stub rollback completed"
        )

    def disconnect(self) -> None:
        """Simulate disconnection from Google Docs API."""
        self._connected = False
        self._operations_log.append({
            "operation": "disconnect"
        })

    def _create_document(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Simulate document creation.

        Payload:
            title: str - Document title
            content: str - Initial content (optional)

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If creation fails
        """
        try:
            # Extract parameters
            title = payload.get("title")

            if not title:
                raise ConnectorError(
                    "Missing required field: title",
                    error_code="INVALID_PAYLOAD"
                )

            # Simulate document ID generation
            doc_id = hashlib.sha256(
                f"{req.idempotency_key}:{title}".encode('utf-8')
            ).hexdigest()[:16]

            # Log operation
            self._operations_log.append({
                "operation": "create_document",
                "title": title,
                "doc_id": doc_id,
                "idempotency_key": req.idempotency_key
            })

            # Compute result hash
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{doc_id}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                external_transaction_id=doc_id,
                artifacts={"doc_id": doc_id},
                side_effect_summary=f"Created document '{title}' (stub) with ID {doc_id}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Create document failed: {str(e)}",
                error_code="CREATE_FAILED"
            )

    def _update_document(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Simulate document update.

        Payload:
            doc_id: str - Document ID
            content: str - New content

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If update fails
        """
        try:
            # Extract parameters
            doc_id = payload.get("doc_id")
            content = payload.get("content")

            if not doc_id or content is None:
                raise ConnectorError(
                    "Missing required fields: doc_id, content",
                    error_code="INVALID_PAYLOAD"
                )

            # Log operation
            self._operations_log.append({
                "operation": "update_document",
                "doc_id": doc_id,
                "content_length": len(content),
                "idempotency_key": req.idempotency_key
            })

            # Compute result hash
            content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{content_hash}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                external_transaction_id=doc_id,
                artifacts={"content_hash": content_hash},
                side_effect_summary=f"Updated document {doc_id} (stub) with {len(content)} chars",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Update document failed: {str(e)}",
                error_code="UPDATE_FAILED"
            )

    def _read_document(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Simulate document read.

        Payload:
            doc_id: str - Document ID

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If read fails
        """
        try:
            # Extract parameters
            doc_id = payload.get("doc_id")

            if not doc_id:
                raise ConnectorError(
                    "Missing required field: doc_id",
                    error_code="INVALID_PAYLOAD"
                )

            # Simulate content
            stub_content = f"[STUB] Content of document {doc_id}"

            # Log operation
            self._operations_log.append({
                "operation": "read_document",
                "doc_id": doc_id,
                "idempotency_key": req.idempotency_key
            })

            # Compute result hash
            content_hash = hashlib.sha256(stub_content.encode('utf-8')).hexdigest()
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{content_hash}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                external_transaction_id=doc_id,
                artifacts={"content_hash": content_hash},
                side_effect_summary=f"Read document {doc_id} (stub)",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Read document failed: {str(e)}",
                error_code="READ_FAILED"
            )

    def _share_document(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Simulate document sharing.

        Payload:
            doc_id: str - Document ID
            email: str - User email
            role: str - Access role (viewer, commenter, editor)

        Args:
            req: ConnectorRequest
            payload: Parsed payload

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If sharing fails
        """
        try:
            # Extract parameters
            doc_id = payload.get("doc_id")
            email = payload.get("email")
            role = payload.get("role", "viewer")

            if not doc_id or not email:
                raise ConnectorError(
                    "Missing required fields: doc_id, email",
                    error_code="INVALID_PAYLOAD"
                )

            # Validate role
            valid_roles = ["viewer", "commenter", "editor"]
            if role not in valid_roles:
                raise ConnectorError(
                    f"Invalid role: {role}. Must be one of {valid_roles}",
                    error_code="INVALID_ROLE"
                )

            # Log operation
            self._operations_log.append({
                "operation": "share_document",
                "doc_id": doc_id,
                "email": email,
                "role": role,
                "idempotency_key": req.idempotency_key
            })

            # Compute result hash
            share_spec = f"{doc_id}:{email}:{role}"
            result_hash = hashlib.sha256(
                f"{req.idempotency_key}:{share_spec}".encode('utf-8')
            ).hexdigest()

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                connector_type=self.connector_type,
                idempotency_key=req.idempotency_key,
                external_transaction_id=doc_id,
                side_effect_summary=f"Shared document {doc_id} (stub) with {email} as {role}",
                result_hash=result_hash
            )

        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(
                f"Share document failed: {str(e)}",
                error_code="SHARE_FAILED"
            )

    def get_operations_log(self) -> list:
        """Get operations log for testing.

        Returns:
            List of operation records
        """
        return self._operations_log.copy()
