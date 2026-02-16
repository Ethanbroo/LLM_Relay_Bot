"""Execution engine for Phase 2.

13-stage execution lifecycle:
1. Dequeue task from queue
2. Generate run_id (UUID v7)
3. Create sandbox
4. Emit TASK_STARTED event
5. Create snapshot
6. Emit SNAPSHOT_CREATED event
7. Lookup handler
8. Execute handler
9. Emit HANDLER_FINISHED event
10. Validate artifacts
11. Destroy sandbox
12. Build ExecutionResult
13. Emit TASK_FINISHED event

Error handling:
- Handler failure → Rollback → Retry or Dead
- Rollback failure → Dead (terminal)
- Max retries → Dead

Invariants:
- One task at a time (single-consumer)
- Snapshot-before-execute (always)
- Rollback-before-retry (always)
- Deterministic task_id and run_id
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
import time

from executor.task_id import compute_task_id
from executor.task_queue import TaskQueue
from executor.sandbox import Sandbox, SandboxError
from executor.rollback import SnapshotManager, RollbackError
from executor.handlers.registry import HandlerRegistry
from executor.handlers import HandlerError
from executor.events import ExecutionEventLogger
from executor.retry_policy import RetryPolicy
from executor.models import ExecutionResult
from validator.time_policy import TimePolicy
from validator.canonicalize import canonicalize_json


class ExecutionEngine:
    """Deterministic execution engine for ValidatedActions."""

    def __init__(
        self,
        queue: Optional[TaskQueue] = None,
        snapshot_manager: Optional[SnapshotManager] = None,
        handler_registry: Optional[HandlerRegistry] = None,
        event_logger: Optional[ExecutionEventLogger] = None,
        retry_policy: Optional[RetryPolicy] = None,
        workspace_root: str = "/tmp/llm-relay/sandboxes",
        snapshot_root: str = "/tmp/llm-relay/snapshots"
    ):
        """Initialize execution engine.

        Args:
            queue: Task queue (creates new if None)
            snapshot_manager: Snapshot manager (creates new if None)
            handler_registry: Handler registry (creates new if None)
            event_logger: Event logger (creates new if None)
            retry_policy: Retry policy (creates new if None)
            workspace_root: Root directory for sandboxes
            snapshot_root: Root directory for snapshots
        """
        self.queue = queue or TaskQueue()
        self.snapshot_manager = snapshot_manager or SnapshotManager(snapshot_root)
        self.handler_registry = handler_registry or HandlerRegistry()
        self.event_logger = event_logger or ExecutionEventLogger()
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=3)
        self.workspace_root = workspace_root
        self.time_policy = TimePolicy.from_config()

        # Session tracking (for retries)
        self._sessions: dict[str, str] = {}  # task_id → session_id

    def _generate_run_id(self) -> str:
        """Generate UUID v4 for execution attempt.

        Returns:
            UUID v4 string (random)

        Note: Phase 3 will use UUID v7 (time-ordered) when available
        """
        # UUID v4 (random) - Phase 2
        # TODO Phase 3: Use UUID v7 when available in Python 3.14+
        return str(uuid.uuid4())

    def _get_or_create_session_id(self, task_id: str) -> str:
        """Get existing session_id or create new one.

        Args:
            task_id: Task identifier

        Returns:
            Session ID (UUID v4)
        """
        if task_id not in self._sessions:
            self._sessions[task_id] = str(uuid.uuid4())

        return self._sessions[task_id]

    def execute_one(self) -> Optional[dict]:
        """Execute one task from queue (single-consumer).

        Returns:
            ExecutionResult dict or None if queue empty

        This implements the full 13-stage lifecycle.
        """
        # Stage 1: Dequeue task
        task_entry = self.queue.dequeue()
        if task_entry is None:
            return None

        task_id = task_entry["task_id"]
        validated_action = task_entry["validated_action"]

        # Extract envelope fields
        envelope = validated_action["original_envelope"]
        action = envelope["action"]
        action_version = envelope["action_version"]
        message_id = envelope["message_id"]

        # Get or create session_id (groups all attempts)
        session_id = self._get_or_create_session_id(task_id)

        # Determine attempt number
        # Phase 2: Simple implementation - track in memory
        # TODO Phase 3: Persist attempt count
        attempt = 1  # Default to first attempt for now

        # Stage 2: Generate run_id
        run_id = self._generate_run_id()

        # Initialize result fields
        sandbox_id = None
        snapshot_id = None
        rollback_id = None
        error_code = None
        error_message = None
        error_details = None
        artifacts = None
        handler_duration_ms = None
        started_at = self.time_policy.get_timestamp()
        start_time_ms = time.time() * 1000

        try:
            # Stage 3: Create sandbox
            sandbox = Sandbox(task_id, run_id, self.workspace_root)
            self.event_logger.log_event(
                "SANDBOX_CREATING",
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                action=action,
                session_id=session_id,
                message_id=message_id
            )

            try:
                sandbox.create()
                sandbox_id = sandbox.sandbox_id

                self.event_logger.log_event(
                    "SANDBOX_CREATED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    event_data={"sandbox_id": sandbox_id}
                )

            except SandboxError as e:
                error_code = "SANDBOX_CREATION_FAILED"
                error_message = str(e)
                raise

            # Stage 4: Emit TASK_STARTED
            self.event_logger.log_event(
                "TASK_STARTED",
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                action=action,
                session_id=session_id,
                message_id=message_id,
                event_data={"sandbox_id": sandbox_id}
            )

            # Stage 5: Create snapshot
            self.event_logger.log_event(
                "SNAPSHOT_CREATING",
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                action=action,
                session_id=session_id,
                message_id=message_id
            )

            try:
                snapshot_metadata = self.snapshot_manager.create_snapshot(
                    task_id, run_id, attempt, sandbox.workspace_path
                )
                snapshot_id = snapshot_metadata["snapshot_id"]

                # Stage 6: Emit SNAPSHOT_CREATED
                self.event_logger.log_event(
                    "SNAPSHOT_CREATED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    event_data={"snapshot_id": snapshot_id}
                )

            except RollbackError as e:
                error_code = "SNAPSHOT_FAILED"
                error_message = str(e)

                self.event_logger.log_event(
                    "SNAPSHOT_FAILED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    error_code=error_code,
                    error_message=error_message
                )
                raise

            # Stage 7: Lookup handler
            try:
                handler = self.handler_registry.get_handler(action)
            except HandlerError as e:
                error_code = "HANDLER_NOT_FOUND"
                error_message = str(e)
                raise

            # Stage 8: Execute handler
            self.event_logger.log_event(
                "HANDLER_STARTED",
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                action=action,
                session_id=session_id,
                message_id=message_id
            )

            handler_start_ms = time.time() * 1000

            try:
                artifacts = handler.execute(validated_action, sandbox)
                handler_duration_ms = int(time.time() * 1000 - handler_start_ms)

                # Stage 9: Emit HANDLER_FINISHED
                self.event_logger.log_event(
                    "HANDLER_FINISHED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    event_data={
                        "handler_duration_ms": handler_duration_ms
                    }
                )

            except HandlerError as e:
                handler_duration_ms = int(time.time() * 1000 - handler_start_ms)
                error_code = "HANDLER_EXCEPTION"
                error_message = str(e)
                error_details = {"exception_type": type(e).__name__}

                self.event_logger.log_event(
                    "HANDLER_FAILED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    error_code=error_code,
                    error_message=error_message
                )
                raise

            # Stage 10: Validate artifacts (Phase 3 - for now, just check dict)
            if not isinstance(artifacts, dict):
                error_code = "ARTIFACT_VALIDATION_FAILED"
                error_message = f"Artifacts must be dict, got {type(artifacts)}"
                raise HandlerError(error_message)

            # Stage 11: Destroy sandbox
            try:
                sandbox.destroy()

                self.event_logger.log_event(
                    "SANDBOX_DESTROYED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    event_data={"sandbox_id": sandbox_id}
                )

            except SandboxError:
                # Log but don't fail on cleanup error
                pass

            # Success! Clean up snapshot
            if snapshot_id:
                try:
                    self.snapshot_manager.delete_snapshot(snapshot_id)
                except RollbackError:
                    # Log but don't fail
                    pass

            # Stage 12: Build ExecutionResult (success)
            finished_at = self.time_policy.get_timestamp()
            total_duration_ms = int(time.time() * 1000 - start_time_ms)

            result = {
                "run_id": run_id,
                "session_id": session_id,
                "message_id": message_id,
                "task_id": task_id,
                "attempt": attempt,
                "action": action,
                "action_version": action_version,
                "status": "success",
                "started_at": started_at,
                "finished_at": finished_at,
                "retryable": False,
                "artifacts": artifacts,
                "sandbox_id": sandbox_id,
                "snapshot_id": snapshot_id,
                "handler_duration_ms": handler_duration_ms,
                "total_duration_ms": total_duration_ms,
                "signature": None  # Phase 3
            }

            # Stage 13: Emit TASK_FINISHED
            self.event_logger.log_event(
                "TASK_FINISHED",
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                action=action,
                session_id=session_id,
                message_id=message_id,
                event_data={"status": "success"}
            )

            return result

        except Exception as e:
            # Execution failed - handle rollback and retry

            finished_at = self.time_policy.get_timestamp()
            total_duration_ms = int(time.time() * 1000 - start_time_ms)

            # Attempt rollback
            rollback_success = False

            if snapshot_id:
                try:
                    self.event_logger.log_event(
                        "ROLLBACK_STARTED",
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        action=action,
                        session_id=session_id,
                        message_id=message_id,
                        event_data={"snapshot_id": snapshot_id}
                    )

                    rollback_metadata = self.snapshot_manager.rollback(
                        snapshot_id,
                        run_id,
                        sandbox.workspace_path if sandbox else None,
                        verify=True
                    )

                    rollback_id = rollback_metadata["rollback_id"]
                    rollback_success = True

                    self.event_logger.log_event(
                        "ROLLBACK_FINISHED",
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        action=action,
                        session_id=session_id,
                        message_id=message_id,
                        event_data={"rollback_id": rollback_id, "success": True}
                    )

                except RollbackError as rollback_err:
                    error_code = "ROLLBACK_FAILED"
                    error_message = f"Original error: {e}. Rollback failed: {rollback_err}"

                    self.event_logger.log_event(
                        "ROLLBACK_FAILED",
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        action=action,
                        session_id=session_id,
                        message_id=message_id,
                        error_code=error_code,
                        error_message=str(rollback_err)
                    )

            # Determine if retryable
            should_retry, retry_reason = self.retry_policy.should_retry(
                error_code or "HANDLER_EXCEPTION",
                attempt,
                rollback_success
            )

            status = "failure" if should_retry else "dead"

            # Build ExecutionResult (failure/dead)
            result = {
                "run_id": run_id,
                "session_id": session_id,
                "message_id": message_id,
                "task_id": task_id,
                "attempt": attempt,
                "action": action,
                "action_version": action_version,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "retryable": should_retry,
                "error_code": error_code or "HANDLER_EXCEPTION",
                "error_message": error_message or str(e),
                "error_details": error_details,
                "sandbox_id": sandbox_id,
                "snapshot_id": snapshot_id,
                "rollback_id": rollback_id,
                "handler_duration_ms": handler_duration_ms,
                "total_duration_ms": total_duration_ms,
                "signature": None
            }

            # Emit event
            if status == "dead":
                self.event_logger.log_event(
                    "TASK_DEAD",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    error_code=error_code,
                    error_message=retry_reason
                )
            else:
                # Re-enqueue for retry
                self.queue.requeue(validated_action, task_id)

                self.event_logger.log_event(
                    "TASK_REQUEUED",
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    action=action,
                    session_id=session_id,
                    message_id=message_id,
                    event_data={"next_attempt": attempt + 1}
                )

            # Clean up sandbox
            if sandbox and sandbox.is_active:
                try:
                    sandbox.destroy()
                except SandboxError:
                    pass

            return result

    def execute_all(self) -> list[dict]:
        """Execute all tasks in queue until empty.

        Returns:
            List of ExecutionResult dicts
        """
        results = []

        while not self.queue.is_empty():
            result = self.execute_one()
            if result:
                results.append(result)

        return results

    def enqueue_validated_action(self, validated_action: dict) -> str:
        """Enqueue ValidatedAction for execution.

        Args:
            validated_action: ValidatedAction from Phase 1

        Returns:
            Computed task_id

        Raises:
            ValueError: If ValidatedAction is invalid
        """
        # Compute task_id
        task_id = compute_task_id(validated_action)

        # Enqueue
        enqueued = self.queue.enqueue(validated_action, task_id)

        # Log event
        envelope = validated_action["original_envelope"]
        run_id = self._generate_run_id()  # Temporary run_id for event

        self.event_logger.log_event(
            "TASK_ENQUEUED",
            run_id=run_id,
            task_id=task_id,
            attempt=1,
            action=envelope["action"],
            message_id=envelope["message_id"],
            event_data={"enqueued": enqueued}
        )

        return task_id
