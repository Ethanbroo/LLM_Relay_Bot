"""Connector lifecycle runner with audit integration.

Phase 5 Invariant: Fixed lifecycle order with Phase 3 audit events.
"""

from typing import Optional
import time
from datetime import datetime, timezone

from connectors.base import BaseConnector, ConnectorRequest, ConnectorContext
from connectors.results import ConnectorResult, ConnectorStatus, RollbackResult
from connectors.idempotency import IdempotencyLedger
from connectors.errors import ConnectorError


class ConnectorAuditEvent:
    """Connector audit event for Phase 3 integration.

    Phase 5 Invariant: All lifecycle transitions must emit audit events.
    """

    def __init__(
        self,
        event_type: str,
        task_id: str,
        attempt: int,
        connector_type: str,
        idempotency_key: str,
        metadata: Optional[dict] = None
    ):
        """Initialize connector audit event.

        Args:
            event_type: Event type (e.g., CONNECTOR_EXECUTE_STARTED)
            task_id: Task ID
            attempt: Attempt number
            connector_type: Connector type
            idempotency_key: Idempotency key
            metadata: Additional metadata
        """
        self.event_type = event_type
        self.task_id = task_id
        self.attempt = attempt
        self.connector_type = connector_type
        self.idempotency_key = idempotency_key
        self.metadata = metadata or {}
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class ConnectorLifecycleRunner:
    """Lifecycle runner for connector execution.

    Phase 5 Invariants:
    - Fixed order: connect → execute → (rollback if failed) → disconnect
    - All transitions emit Phase 3 audit events
    - Idempotency check before execution
    - Rollback called on failure
    """

    def __init__(
        self,
        connector: BaseConnector,
        idempotency_ledger: IdempotencyLedger,
        audit_callback: Optional[callable] = None
    ):
        """Initialize lifecycle runner.

        Args:
            connector: Connector instance
            idempotency_ledger: Idempotency ledger
            audit_callback: Callback for audit events (Phase 3 LogDaemon)
        """
        self.connector = connector
        self.idempotency_ledger = idempotency_ledger
        self.audit_callback = audit_callback
        self._connected = False
        self._executed = False
        self._result: Optional[ConnectorResult] = None

    def _emit_audit_event(
        self,
        event_type: str,
        task_id: str,
        attempt: int,
        idempotency_key: str,
        metadata: Optional[dict] = None
    ) -> None:
        """Emit audit event to Phase 3.

        Args:
            event_type: Event type
            task_id: Task ID
            attempt: Attempt number
            idempotency_key: Idempotency key
            metadata: Additional metadata
        """
        if self.audit_callback is None:
            return

        event = ConnectorAuditEvent(
            event_type=event_type,
            task_id=task_id,
            attempt=attempt,
            connector_type=self.connector.connector_type,
            idempotency_key=idempotency_key,
            metadata=metadata
        )

        self.audit_callback(event)

    def connect(self, ctx: ConnectorContext) -> None:
        """Execute connect phase with audit.

        Args:
            ctx: ConnectorContext

        Raises:
            ConnectorError: If connect fails
        """
        if self._connected:
            return

        # Emit CONNECTOR_CONNECT_STARTED
        self._emit_audit_event(
            event_type="CONNECTOR_CONNECT_STARTED",
            task_id=ctx.task_id,
            attempt=ctx.attempt,
            idempotency_key="",
            metadata={"workspace_root": ctx.workspace_root}
        )

        try:
            start_time = time.time()
            self.connector.connect(ctx)
            duration_ms = int((time.time() - start_time) * 1000)

            self._connected = True

            # Emit CONNECTOR_CONNECTED
            self._emit_audit_event(
                event_type="CONNECTOR_CONNECTED",
                task_id=ctx.task_id,
                attempt=ctx.attempt,
                idempotency_key="",
                metadata={"duration_ms": duration_ms}
            )
        except Exception as e:
            # Emit failure event
            self._emit_audit_event(
                event_type="CONNECTOR_CONNECT_FAILED",
                task_id=ctx.task_id,
                attempt=ctx.attempt,
                idempotency_key="",
                metadata={"error": str(e)[:200]}
            )
            raise

    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        """Execute connector with idempotency check and audit.

        Phase 5 Invariants:
        - Check idempotency ledger first
        - Return prior result if already executed
        - Emit audit events for all paths

        Args:
            req: ConnectorRequest

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If execution fails
            RuntimeError: If not connected
        """
        if not self._connected:
            raise RuntimeError("Connector not connected")

        # Check idempotency ledger
        prior_record = self.idempotency_ledger.check(req.idempotency_key)
        if prior_record is not None:
            # Emit CONNECTOR_IDEMPOTENCY_HIT
            self._emit_audit_event(
                event_type="CONNECTOR_IDEMPOTENCY_HIT",
                task_id=req.task_id,
                attempt=req.attempt,
                idempotency_key=req.idempotency_key,
                metadata={
                    "prior_status": prior_record.status.value,
                    "result_hash": prior_record.result_hash
                }
            )

            # Return prior result without re-execution
            return prior_record.result

        # Emit CONNECTOR_EXECUTE_STARTED
        self._emit_audit_event(
            event_type="CONNECTOR_EXECUTE_STARTED",
            task_id=req.task_id,
            attempt=req.attempt,
            idempotency_key=req.idempotency_key,
            metadata={
                "action": req.action,
                "action_version": req.action_version,
                "payload_hash": req.payload_hash,
                "coordination_id": req.coordination_proof.coordination_id
            }
        )

        try:
            start_time = time.time()
            result = self.connector.execute(req)
            duration_ms = int((time.time() - start_time) * 1000)

            self._executed = True
            self._result = result

            # Record in idempotency ledger
            self.idempotency_ledger.record(req.idempotency_key, result)

            # Emit CONNECTOR_EXECUTE_FINISHED
            self._emit_audit_event(
                event_type="CONNECTOR_EXECUTE_FINISHED",
                task_id=req.task_id,
                attempt=req.attempt,
                idempotency_key=req.idempotency_key,
                metadata={
                    "status": result.status.value,
                    "result_hash": result.result_hash,
                    "duration_ms": duration_ms,
                    "external_transaction_id": result.external_transaction_id
                }
            )

            return result
        except Exception as e:
            # Create failure result
            result = ConnectorResult(
                status=ConnectorStatus.FAILURE,
                connector_type=self.connector.connector_type,
                idempotency_key=req.idempotency_key,
                error_code=getattr(e, 'error_code', 'CONNECTOR_EXECUTION_FAILED'),
                error_message=str(e)[:200]
            )

            self._executed = True
            self._result = result

            # Record failure in ledger
            self.idempotency_ledger.record(req.idempotency_key, result)

            # Emit failure event
            self._emit_audit_event(
                event_type="CONNECTOR_EXECUTE_FAILED",
                task_id=req.task_id,
                attempt=req.attempt,
                idempotency_key=req.idempotency_key,
                metadata={
                    "error_code": result.error_code,
                    "error_message": result.error_message
                }
            )

            raise

    def rollback(
        self,
        req: ConnectorRequest,
        artifact: Optional[object] = None
    ) -> RollbackResult:
        """Execute rollback with audit.

        Phase 5 Invariant: Rollback must be verifiable.

        Args:
            req: Original ConnectorRequest
            artifact: ExecutionArtifact from execute (if any)

        Returns:
            RollbackResult

        Raises:
            ConnectorError: If rollback fails
        """
        # Emit CONNECTOR_ROLLBACK_STARTED
        self._emit_audit_event(
            event_type="CONNECTOR_ROLLBACK_STARTED",
            task_id=req.task_id,
            attempt=req.attempt,
            idempotency_key=req.idempotency_key,
            metadata={"has_artifact": artifact is not None}
        )

        try:
            start_time = time.time()
            rollback_result = self.connector.rollback(req, artifact)
            duration_ms = int((time.time() - start_time) * 1000)

            # Emit CONNECTOR_ROLLBACK_FINISHED
            self._emit_audit_event(
                event_type="CONNECTOR_ROLLBACK_FINISHED",
                task_id=req.task_id,
                attempt=req.attempt,
                idempotency_key=req.idempotency_key,
                metadata={
                    "rollback_status": rollback_result.rollback_status.value,
                    "verification_method": rollback_result.verification_method.value,
                    "duration_ms": duration_ms
                }
            )

            return rollback_result
        except Exception as e:
            # Emit failure event
            self._emit_audit_event(
                event_type="CONNECTOR_ROLLBACK_FAILED",
                task_id=req.task_id,
                attempt=req.attempt,
                idempotency_key=req.idempotency_key,
                metadata={"error": str(e)[:200]}
            )
            raise

    def disconnect(self, task_id: str, attempt: int) -> None:
        """Execute disconnect phase with audit.

        Args:
            task_id: Task ID
            attempt: Attempt number

        Raises:
            ConnectorError: If disconnect fails
        """
        if not self._connected:
            return

        # Emit CONNECTOR_DISCONNECT_STARTED
        self._emit_audit_event(
            event_type="CONNECTOR_DISCONNECT_STARTED",
            task_id=task_id,
            attempt=attempt,
            idempotency_key="",
            metadata={}
        )

        try:
            start_time = time.time()
            self.connector.disconnect()
            duration_ms = int((time.time() - start_time) * 1000)

            self._connected = False

            # Emit CONNECTOR_DISCONNECTED
            self._emit_audit_event(
                event_type="CONNECTOR_DISCONNECTED",
                task_id=task_id,
                attempt=attempt,
                idempotency_key="",
                metadata={"duration_ms": duration_ms}
            )
        except Exception as e:
            # Emit failure event
            self._emit_audit_event(
                event_type="CONNECTOR_DISCONNECT_FAILED",
                task_id=task_id,
                attempt=attempt,
                idempotency_key="",
                metadata={"error": str(e)[:200]}
            )
            raise

    def run_full_lifecycle(
        self,
        ctx: ConnectorContext,
        req: ConnectorRequest,
        rollback_on_failure: bool = True
    ) -> ConnectorResult:
        """Run full connector lifecycle: connect → execute → (rollback) → disconnect.

        Phase 5 Invariant: Fixed lifecycle order.

        Args:
            ctx: ConnectorContext
            req: ConnectorRequest
            rollback_on_failure: Whether to rollback on execution failure

        Returns:
            ConnectorResult

        Raises:
            ConnectorError: If execution fails and rollback disabled
        """
        try:
            # Phase 1: Connect
            self.connect(ctx)

            # Phase 2: Execute
            try:
                result = self.execute(req)

                # Check if execution failed
                if result.status == ConnectorStatus.FAILURE and rollback_on_failure:
                    # Phase 3a: Rollback on failure
                    self.rollback(req, None)

                return result
            except Exception as exec_error:
                # Phase 3b: Rollback on exception
                if rollback_on_failure:
                    try:
                        self.rollback(req, None)
                    except Exception as rollback_error:
                        # Log rollback failure but raise original error
                        self._emit_audit_event(
                            event_type="CONNECTOR_ROLLBACK_FAILED",
                            task_id=req.task_id,
                            attempt=req.attempt,
                            idempotency_key=req.idempotency_key,
                            metadata={"rollback_error": str(rollback_error)[:200]}
                        )

                raise exec_error
        finally:
            # Phase 4: Disconnect (always called)
            try:
                self.disconnect(ctx.task_id, ctx.attempt)
            except Exception as disconnect_error:
                # Log disconnect failure but don't mask execution result
                self._emit_audit_event(
                    event_type="CONNECTOR_DISCONNECT_FAILED",
                    task_id=ctx.task_id,
                    attempt=ctx.attempt,
                    idempotency_key="",
                    metadata={"error": str(disconnect_error)[:200]}
                )
