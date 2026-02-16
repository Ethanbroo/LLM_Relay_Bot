"""Unified supervisor for Phases 1-8 integration.

Coordinates:
- Phase 1: Validation pipeline (validator/)
- Phase 2: Execution engine (executor/)
- Phase 3: Audit logging (audit_logging/)
- Phase 4: Coordination & Safety (coordination/)
- Phase 5: Connectors (Controlled Power) (connectors/)
- Phase 6: Multi-LLM Orchestration (orchestration/)
- Phase 7: Monitoring & Recovery (monitoring/)
- Phase 8: Claude LLM Integration (llm_integration/)

The supervisor:
1. Initializes Phase 3 LogDaemon with Ed25519 keys
2. Initializes Phase 4 coordination pipeline (locks, deadlock, approvals)
3. Initializes Phase 5 connector registry and idempotency ledger
4. Initializes Phase 6 orchestration pipeline with model registry
5. Initializes Phase 7 monitor daemon with metrics collection and rules
6. Initializes Phase 8 Claude client for stateless text transformation
7. Injects LogDaemon into all phases
8. Coordinates the full message flow: Validation → Coordination → Orchestration → Execution → Connectors → Audit → Monitoring → Claude
"""

import os
import uuid
from uuid6 import uuid7
import hashlib
import yaml
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from audit_logging.log_daemon import LogDaemon
from audit_logging.key_manager import KeyManager
from audit_logging.recovery import CrashRecoveryManager, TamperDetectedError
from validator.pipeline import ValidationPipeline
from validator.audit import AuditLogger
from validator.canonicalize import canonicalize_json
from executor.engine import ExecutionEngine
from executor.events import ExecutionEventLogger
from executor.task_queue import TaskQueue
# Phase 4 imports
from coordination.phase4_pipeline import CoordinationPipeline, ValidatedAction, CoordinatedAction, CoordinationError
from coordination.lock_registry import LockRegistry
from coordination.approval_registry import ApprovalRegistry
from coordination.approval_tokens import ApprovalTokenVerifier
# Phase 5 imports
from connectors.registry import ConnectorRegistry, register_connector, register_action
from connectors.idempotency import IdempotencyLedger
from connectors.secrets import SecretsProvider
from connectors.local_fs import LocalFSConnector
from connectors.google_docs_stub import GoogleDocsStubConnector
from connectors.wordpress import WordPressConnector
from connectors.unsplash import UnsplashConnector
# Phase 6 imports
from orchestration.models import ModelRegistry, ChatGPTModel, ClaudeModel, GeminiModel, DeepSeekModel
from orchestration.orchestration_pipeline import OrchestrationPipeline
# Phase 7 imports
from monitoring.monitor_daemon import MonitorDaemon
# Phase 8 imports
from llm_integration.claude_client import ClaudeClient


class SupervisorError(Exception):
    """Base exception for supervisor errors."""
    pass


class LLMRelaySupervisor:
    """Unified supervisor coordinating Phases 1-8."""

    def __init__(
        self,
        config_path: str = "config/core.yaml",
        policy_path: str = "config/policy.yaml",
        base_dir: Optional[str] = None
    ):
        """Initialize supervisor.

        Args:
            config_path: Path to core.yaml configuration
            policy_path: Path to policy.yaml RBAC policy
            base_dir: Base directory for resolving paths (defaults to current dir)

        Raises:
            SupervisorError: If initialization fails
            TamperDetectedError: If audit log tampering detected during recovery
        """
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.config_path = self.base_dir / config_path
        self.policy_path = self.base_dir / policy_path

        # Load configuration
        self.config = self._load_config()
        self.policy = self._load_policy()

        # Compute config_hash (SHA-256 of core.yaml + policy.yaml)
        self.config_hash = self._compute_config_hash()

        # Generate run_id for this supervisor instance
        self.run_id = str(uuid.uuid4())

        # Initialize Phase 3 LogDaemon
        self.log_daemon = self._initialize_log_daemon()

        # Initialize Phase 4 Coordination Pipeline
        self.coordination = self._initialize_coordination()

        # Initialize Phase 5 Connector Registry & Idempotency
        self.connector_registry, self.idempotency_ledger, self.secrets_provider = self._initialize_connectors()

        # Initialize Phase 6 Orchestration Pipeline (optional)
        orchestration_config = self.config.get("orchestration", {})
        if orchestration_config.get("enabled", False):
            self.orchestration = self._initialize_orchestration()
        else:
            self.orchestration = None

        # Initialize Phase 1 Validator with LogDaemon
        self.validator = self._initialize_validator()

        # Initialize Phase 2 Executor with LogDaemon
        self.executor = self._initialize_executor()

        # Initialize Phase 7 Monitor Daemon (optional)
        monitoring_config = self.config.get("monitoring", {})
        if monitoring_config.get("enabled", False):
            self.monitor_daemon = self._initialize_monitoring()
        else:
            self.monitor_daemon = None

        # Initialize Phase 8 Claude Client (optional)
        claude_config = self.config.get("claude", {})
        if claude_config.get("enabled", False):
            self.claude_client = self._initialize_claude()
        else:
            self.claude_client = None

        # Log RUN_STARTED event
        self.log_daemon.ingest_event(
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "run_id": self.run_id,
                "config_hash": self.config_hash,
                "time_policy": self.config.get("time_policy", {}).get("mode", "recorded"),
                "module_versions": {
                    "validator": "1.0.0",
                    "executor": "1.0.0",
                    "audit_logging": "1.0.0",
                    "coordination": "1.0.0",  # Phase 4
                    "connectors": "1.0.0",  # Phase 5
                    "orchestration": "1.0.0",  # Phase 6
                    "monitoring": "1.0.0",  # Phase 7
                    "claude": "1.0.0"  # Phase 8
                }
            }
        )

    def _load_config(self) -> dict:
        """Load core.yaml configuration.

        Returns:
            Configuration dict

        Raises:
            SupervisorError: If config file not found or invalid
        """
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise SupervisorError(f"Failed to load config: {e}")

    def _load_policy(self) -> dict:
        """Load policy.yaml configuration.

        Returns:
            Policy dict

        Raises:
            SupervisorError: If policy file not found or invalid
        """
        try:
            with open(self.policy_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise SupervisorError(f"Failed to load policy: {e}")

    def _compute_config_hash(self) -> str:
        """Compute SHA-256 hash of core.yaml + policy.yaml.

        Returns:
            Hex-encoded SHA-256 hash
        """
        # Read both files
        core_content = self.config_path.read_bytes()
        policy_content = self.policy_path.read_bytes()

        # Canonical concatenation
        combined = core_content + b"|" + policy_content

        # SHA-256 hash
        return hashlib.sha256(combined).hexdigest()

    def _initialize_log_daemon(self) -> LogDaemon:
        """Initialize Phase 3 LogDaemon with crash recovery.

        Returns:
            Initialized LogDaemon instance

        Raises:
            SupervisorError: If LogDaemon initialization fails
            TamperDetectedError: If tampering detected during recovery
        """
        audit_config = self.config.get("audit", {})

        # Extract audit logging configuration
        log_directory = self.base_dir / audit_config.get("log_directory", "logs")
        private_key_path = self.base_dir / audit_config.get(
            "ed25519_private_key_path", "keys/audit_private.pem"
        )
        public_key_path = self.base_dir / audit_config.get(
            "ed25519_public_key_path", "keys/audit_public.pem"
        )
        max_segment_bytes = audit_config.get("max_segment_bytes", 10485760)
        fsync_every_n_events = audit_config.get("fsync_every_n_events", 100)

        # Ensure keys exist
        if not private_key_path.exists():
            raise SupervisorError(
                f"Ed25519 private key not found at {private_key_path}. "
                f"Generate keys using: openssl genpkey -algorithm ED25519 -out {private_key_path}"
            )
        if not public_key_path.exists():
            raise SupervisorError(
                f"Ed25519 public key not found at {public_key_path}"
            )

        # Initialize KeyManager
        key_manager = KeyManager(
            private_key_path=str(private_key_path),
            public_key_path=str(public_key_path),
            enforce_permissions=True
        )

        # Perform crash recovery
        recovery_mgr = CrashRecoveryManager(
            log_directory=str(log_directory),
            key_manager=key_manager
        )

        try:
            result = recovery_mgr.recover()

            if not result.success:
                # Check if this is a "first run" (no manifest) vs actual failure
                if "Manifest not found" not in result.error_message:
                    raise SupervisorError(f"Crash recovery failed: {result.error_message}")
                # Otherwise it's a first run - no logs to recover, which is fine

            if result.tamper_detected:
                # CRITICAL: Tampering detected - HALT IMMEDIATELY
                raise TamperDetectedError(
                    f"Audit log tampering detected during recovery. System halted."
                )

            if result.corruption_detected:
                # Log corruption detected (truncated lines)
                print(f"⚠️  Corruption detected: {result.truncated_lines} lines truncated")

        except TamperDetectedError:
            # Re-raise tamper detection (must halt)
            raise

        # Create LogDaemon
        time_policy = self.config.get("time_policy", {}).get("mode", "recorded")

        log_daemon = LogDaemon(
            run_id=self.run_id,
            config_hash=self.config_hash,
            time_policy=time_policy,
            key_manager=key_manager,
            log_directory=str(log_directory),
            fsync_every_n_events=fsync_every_n_events
        )

        return log_daemon

    def _initialize_validator(self) -> ValidationPipeline:
        """Initialize Phase 1 validator with LogDaemon integration.

        Returns:
            ValidationPipeline instance
        """
        # Create AuditLogger with LogDaemon
        audit_logger = AuditLogger(log_daemon=self.log_daemon)

        # Create ValidationPipeline with custom audit logger
        validator = ValidationPipeline(
            base_dir=str(self.base_dir)
        )

        # Replace validator's audit logger with Phase 3 integrated one
        validator.audit = audit_logger

        return validator

    def _initialize_executor(self) -> ExecutionEngine:
        """Initialize Phase 2 executor with LogDaemon integration.

        Returns:
            ExecutionEngine instance
        """
        # Create ExecutionEventLogger with LogDaemon
        event_logger = ExecutionEventLogger(log_daemon=self.log_daemon)

        # Create ExecutionEngine with custom event logger
        executor = ExecutionEngine(
            event_logger=event_logger
        )

        return executor

    def _initialize_coordination(self) -> CoordinationPipeline:
        """Initialize Phase 4 coordination pipeline.

        Returns:
            CoordinationPipeline instance

        Raises:
            SupervisorError: If coordination initialization fails
        """
        coord_config = self.config.get("coordination", {})

        # Initialize lock registry
        lock_ttl_events = coord_config.get("lock_ttl_events", 1000)
        lock_registry = LockRegistry(lock_ttl_events=lock_ttl_events)

        # Initialize approval registry
        approval_registry = ApprovalRegistry()

        # Load approval public key
        approval_public_key_path = self.base_dir / coord_config.get(
            "approval_public_key_path", "keys/approval_public.pem"
        )

        if not approval_public_key_path.exists():
            raise SupervisorError(
                f"Approval public key not found at {approval_public_key_path}. "
                f"Generate keys using: openssl genpkey -algorithm ED25519"
            )

        approval_verifier = ApprovalTokenVerifier.from_pem_file(
            str(approval_public_key_path)
        )

        # Create requires_approval function from policy
        approval_required_actions = set()
        for rule in self.policy.get("approval_required", []):
            approval_required_actions.add(rule["action"])

        def requires_approval_fn(action: str, payload: dict) -> bool:
            """Check if action requires approval based on policy."""
            return action in approval_required_actions

        # Create coordination pipeline
        coordination = CoordinationPipeline(
            lock_registry=lock_registry,
            approval_registry=approval_registry,
            approval_verifier=approval_verifier,
            log_daemon=self.log_daemon,
            requires_approval_fn=requires_approval_fn
        )

        return coordination

    def _create_connector_audit_callback(self):
        """Create audit callback for Phase 5 connectors.

        The callback bridges ConnectorAuditEvent to Phase 3 LogDaemon.

        Returns:
            Callable that accepts ConnectorAuditEvent
        """
        def connector_audit_callback(event):
            """Bridge connector audit events to Phase 3 LogDaemon.

            Args:
                event: ConnectorAuditEvent instance
            """
            # Convert ConnectorAuditEvent to Phase 3 audit event
            self.log_daemon.ingest_event(
                event_type=event.event_type,
                actor="connectors",
                correlation={
                    "session_id": None,
                    "message_id": None,
                    "task_id": event.task_id
                },
                payload={
                    "connector_type": event.connector_type,
                    "attempt": event.attempt,
                    "idempotency_key": event.idempotency_key,
                    **event.metadata
                }
            )

        return connector_audit_callback

    def _initialize_connectors(self) -> tuple[ConnectorRegistry, IdempotencyLedger, SecretsProvider]:
        """Initialize Phase 5 connector registry and supporting infrastructure.

        Returns:
            Tuple of (ConnectorRegistry, IdempotencyLedger, SecretsProvider)

        Raises:
            SupervisorError: If connector initialization fails
        """
        connector_config = self.config.get("connectors", {})

        # Initialize connector registry
        connector_registry = ConnectorRegistry()

        # Register LocalFS connector
        connector_registry.register("local_fs", LocalFSConnector)

        # Register Google Docs stub connector
        connector_registry.register("google_docs_stub", GoogleDocsStubConnector)

        # Register WordPress connector
        connector_registry.register("wordpress", WordPressConnector)

        # Register Unsplash connector
        connector_registry.register("unsplash", UnsplashConnector)

        # Register action mappings from policy
        connector_mappings = self.policy.get("connector_mappings", [])
        for mapping in connector_mappings:
            action = mapping.get("action")
            connector_type = mapping.get("connector_type")
            method = mapping.get("method", "execute")

            if action and connector_type:
                connector_registry.register_action_mapping(action, connector_type, method)

        # Initialize idempotency ledger
        idempotency_ledger = IdempotencyLedger()

        # Initialize secrets provider
        secrets_env_prefix = connector_config.get("secrets_env_prefix", "LLM_RELAY_SECRET_")
        secrets_provider = SecretsProvider(env_prefix=secrets_env_prefix)

        return connector_registry, idempotency_ledger, secrets_provider

    def _initialize_orchestration(self) -> OrchestrationPipeline:
        """Initialize Phase 6 orchestration pipeline.

        Phase 6 Invariant: Orchestration is deterministic and auditable.

        Returns:
            OrchestrationPipeline instance

        Raises:
            SupervisorError: If orchestration initialization fails
        """
        # Read configuration
        orchestration_config = self.config.get("orchestration", {})
        consensus_threshold = orchestration_config.get("consensus_threshold", 0.80)
        similarity_rounding = orchestration_config.get("similarity_rounding", 3)
        escalation_model_id = orchestration_config.get("escalation_model", "chatgpt")

        # Initialize model registry
        model_registry = ModelRegistry()

        # Read API keys from environment (fallback to None for stubs)
        openai_api_key = os.environ.get("OPENAI_API_KEY")
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        google_api_key = os.environ.get("GOOGLE_API_KEY")
        deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY")

        # Register all 4 models (stubs for now)
        model_registry.register(ChatGPTModel(api_key=openai_api_key))
        model_registry.register(ClaudeModel(api_key=anthropic_api_key))
        model_registry.register(GeminiModel(api_key=google_api_key))
        model_registry.register(DeepSeekModel(api_key=deepseek_api_key))

        # Create audit callback linked to Phase 3
        def audit_callback(event_type: str, metadata: dict) -> None:
            """Emit orchestration audit event to Phase 3."""
            self.log_daemon.emit_event(event_type, metadata)

        # Create orchestration pipeline
        pipeline = OrchestrationPipeline(
            model_registry=model_registry,
            consensus_threshold=consensus_threshold,
            similarity_rounding=similarity_rounding,
            escalation_model_id=escalation_model_id,
            audit_callback=audit_callback,
            run_id=self.run_id
        )

        return pipeline

    def _initialize_monitoring(self) -> MonitorDaemon:
        """Initialize Phase 7 monitor daemon.

        Phase 7 Invariant: Monitoring is deterministic, tick-driven, and auditable.

        Returns:
            MonitorDaemon instance

        Raises:
            SupervisorError: If monitoring initialization fails
        """
        # Read configuration
        monitoring_config = self.config.get("monitoring", {})
        time_policy = self.config.get("time_policy", {}).get("mode", "recorded")

        # Create audit callback linked to Phase 3
        def audit_callback(event_type: str, metadata: dict) -> None:
            """Emit monitoring audit event to Phase 3."""
            self.log_daemon.emit_event(event_type, metadata)

        # Create supervisor control callback
        def supervisor_control_callback(control_type: str, payload: dict) -> None:
            """Handle supervisor control signals from recovery controller."""
            # TODO: Implement supervisor control signal handling
            # For now, just audit the signal
            self.log_daemon.emit_event("SUPERVISOR_CONTROL_SIGNAL", {
                "control_type": control_type,
                "payload": payload
            })

        # Create monitor daemon
        try:
            daemon = MonitorDaemon(
                run_id=self.run_id,
                config=monitoring_config,
                config_hash=self.config_hash,
                time_policy=time_policy,
                task_queue=getattr(self, 'task_queue', None),
                engine=getattr(self, 'engine', None),
                log_daemon=self.log_daemon,
                coordination=self.coordination,
                connector_registry=self.connector_registry,
                orchestration=self.orchestration,
                audit_callback=audit_callback,
                supervisor_control_callback=supervisor_control_callback
            )

            return daemon

        except Exception as e:
            raise SupervisorError(f"Failed to initialize monitoring: {e}")

    def _initialize_claude(self) -> ClaudeClient:
        """Initialize Phase 8 Claude client.

        Phase 8 Invariant: Claude is a stateless text transformation system.

        Returns:
            ClaudeClient instance

        Raises:
            SupervisorError: If Claude client initialization fails
        """
        # Read configuration
        claude_config = self.config.get("claude", {})
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        prompts_dir = claude_config.get("prompts_dir", "prompts/claude")
        stub_mode = claude_config.get("stub_mode", True)

        # Create audit callback linked to Phase 3
        def audit_callback(event_type: str, metadata: dict) -> None:
            """Emit Claude audit event to Phase 3."""
            self.log_daemon.emit_event(event_type, metadata)

        # Create Claude client
        try:
            client = ClaudeClient(
                api_key=api_key,
                prompts_dir=prompts_dir,
                audit_callback=audit_callback,
                stub_mode=stub_mode
            )

            return client

        except Exception as e:
            raise SupervisorError(f"Failed to initialize Claude client: {e}")

    def process_envelope(self, envelope: dict) -> dict:
        """Process an envelope through the full pipeline.

        Flow:
        1. Phase 1: Validate envelope → ValidatedAction or Error
        2. If ValidatedAction: Phase 4: Coordinate (locks, approval) → CoordinatedAction or Error
        3. If CoordinatedAction: Phase 2: Enqueue for execution
        4. Return result

        Args:
            envelope: Envelope dict to process

        Returns:
            ValidatedAction/CoordinatedAction dict or Error dict
        """
        # Phase 1: Validation
        result = self.validator.validate(envelope)

        # Check if validation succeeded
        if "validation_id" not in result:
            # Validation failed - return error
            return result

        # Validation passed - create ValidatedAction for Phase 4
        # Note: We need to add enqueue_seq from task queue
        validated_action = ValidatedAction(
            validation_id=result["validation_id"],
            task_id=result.get("task_id", str(uuid.uuid4())),
            action=envelope["action"],  # Get from original envelope
            action_version=envelope["action_version"],  # Get from original envelope
            payload=envelope["payload"],  # Get from original envelope
            schema_hash=result["schema_hash"],
            rbac_rule_id=result["rbac_rule_id"],
            sender=envelope.get("sender", "unknown"),
            recipient=envelope.get("recipient", "unknown"),
            message_id=envelope.get("message_id", ""),
            enqueue_seq=0,  # Will be assigned by task queue
            attempt=0,
            approval_id=envelope.get("approval_id")  # If provided
        )

        # Phase 4: Coordination
        coordinated_action, coordination_error = self.coordination.coordinate_action(validated_action)

        if coordination_error:
            # Coordination failed - return error
            return {
                "error_id": coordination_error.error_id,
                "error_code": coordination_error.error_code,
                "message": coordination_error.message,
                "stage": coordination_error.stage,
                "task_id": coordination_error.task_id,
                "action": coordination_error.action
            }

        # Coordination passed - enqueue for execution
        task_id = self.executor.enqueue_validated_action(result)

        # Return coordinated action info
        return {
            "validation_id": result["validation_id"],
            "coordination_id": coordinated_action.coordination_id,
            "task_id": task_id,
            "action": envelope["action"],
            "action_version": envelope["action_version"],
            "schema_hash": result["schema_hash"],
            "rbac_rule_id": result["rbac_rule_id"],
            "lock_set_id": coordinated_action.lock_set_id,
            "acquired_locks": coordinated_action.acquired_locks,
            "approval_verified": coordinated_action.approval_verified
        }

    def execute_pending_tasks(self) -> list[dict]:
        """Execute all pending tasks in the queue.

        Returns:
            List of ExecutionResult dicts
        """
        return self.executor.execute_all()

    def shutdown(self):
        """Gracefully shutdown supervisor and flush all logs."""
        # Close LogDaemon (flushes and finalizes)
        if self.log_daemon:
            self.log_daemon.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()


def main():
    """Example usage of LLMRelaySupervisor."""
    try:
        # Initialize supervisor (performs crash recovery, initializes LogDaemon)
        with LLMRelaySupervisor() as supervisor:
            # Example envelope
            envelope = {
                "envelope_version": "1.0.0",
                "message_id": str(uuid7()),
                "sender": "supervisor",
                "recipient": "executor",
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "action": "system.health_ping",
                "action_version": "1.0.0",
                "payload": {}

            }

            # Process envelope (validate)
            result = supervisor.process_envelope(envelope)

            if "validation_id" in result:
                print(f"✅ Validation passed: {result['validation_id']}")
                print(f"   Task enqueued: {result['task_id']}")

                # Execute pending tasks
                execution_results = supervisor.execute_pending_tasks()

                for exec_result in execution_results:
                    print(f"✅ Execution completed:")
                    print(f"   Status: {exec_result['status']}")
                    print(f"   Run ID: {exec_result['run_id']}")
            else:
                print(f"❌ Validation failed: {result['error_code']}")
                print(f"   Message: {result['message']}")
                if "details" in result:
                    print("   Details:", result["details"])

    except TamperDetectedError as e:
        print(f"🚨 CRITICAL: Audit log tampering detected!")
        print(f"   {e}")
        print(f"   System halted for security investigation.")
        return 1

    except SupervisorError as e:
        print(f"❌ Supervisor error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
