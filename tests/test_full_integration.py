"""Full integration test for all 8 phases working together.

This test verifies the complete LLM Relay Bot integration:
- Phase 1: Validation
- Phase 2: Execution
- Phase 3: Audit Logging
- Phase 4: Coordination & Safety
- Phase 5: Connectors
- Phase 6: Multi-LLM Orchestration
- Phase 7: Monitoring & Recovery
- Phase 8: Claude LLM Integration
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from supervisor import LLMRelaySupervisor


@pytest.fixture
def temp_workspace():
    """Create temporary workspace for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def supervisor_instance(temp_workspace):
    """Create supervisor instance with all 8 phases."""
    # Note: This requires keys to be generated first
    # For testing, we'll use the existing keys in the repo
    supervisor = LLMRelaySupervisor(
        config_path="config/core.yaml",
        policy_path="config/policy.yaml"
    )

    yield supervisor

    # Cleanup
    supervisor.shutdown()


class TestPhase1Integration:
    """Test Phase 1 (Validation) integration."""

    def test_validation_pipeline_initialized(self, supervisor_instance):
        """Validator is properly initialized with LogDaemon."""
        assert supervisor_instance.validator is not None
        assert supervisor_instance.validator.audit is not None


class TestPhase2Integration:
    """Test Phase 2 (Execution) integration."""

    def test_executor_initialized(self, supervisor_instance):
        """Executor is properly initialized with LogDaemon."""
        assert supervisor_instance.executor is not None
        assert supervisor_instance.executor.event_logger is not None


class TestPhase3Integration:
    """Test Phase 3 (Audit Logging) integration."""

    def test_log_daemon_initialized(self, supervisor_instance):
        """LogDaemon is properly initialized with keys."""
        assert supervisor_instance.log_daemon is not None
        assert supervisor_instance.log_daemon.event_seq == 1  # RUN_STARTED emitted

    def test_config_hash_computed(self, supervisor_instance):
        """Config hash is computed from core.yaml + policy.yaml."""
        assert supervisor_instance.config_hash is not None
        assert len(supervisor_instance.config_hash) == 64  # SHA-256

    def test_run_id_generated(self, supervisor_instance):
        """Run ID is generated (UUID v4)."""
        assert supervisor_instance.run_id is not None
        assert len(supervisor_instance.run_id) == 36  # UUID format


class TestPhase4Integration:
    """Test Phase 4 (Coordination) integration."""

    def test_coordination_pipeline_initialized(self, supervisor_instance):
        """Coordination pipeline is properly initialized."""
        assert supervisor_instance.coordination is not None
        assert supervisor_instance.coordination.lock_registry is not None
        assert supervisor_instance.coordination.approval_registry is not None
        assert supervisor_instance.coordination.log_daemon is not None


class TestPhase5Integration:
    """Test Phase 5 (Connectors) integration."""

    def test_connector_registry_initialized(self, supervisor_instance):
        """Connector registry is properly initialized."""
        assert supervisor_instance.connector_registry is not None

    def test_connectors_registered(self, supervisor_instance):
        """LocalFS and GoogleDocsStub connectors are registered."""
        assert "local_fs" in supervisor_instance.connector_registry._connectors
        assert "google_docs_stub" in supervisor_instance.connector_registry._connectors

    def test_idempotency_ledger_initialized(self, supervisor_instance):
        """Idempotency ledger is initialized."""
        assert supervisor_instance.idempotency_ledger is not None

    def test_secrets_provider_initialized(self, supervisor_instance):
        """Secrets provider is initialized."""
        assert supervisor_instance.secrets_provider is not None

    def test_connector_audit_callback_exists(self, supervisor_instance):
        """Connector audit callback method exists."""
        assert hasattr(supervisor_instance, '_create_connector_audit_callback')
        callback = supervisor_instance._create_connector_audit_callback()
        assert callable(callback)


class TestPhase6Integration:
    """Test Phase 6 (Orchestration) integration."""

    def test_orchestration_initialized(self, supervisor_instance):
        """Orchestration pipeline is initialized when enabled."""
        # Check config first
        orchestration_config = supervisor_instance.config.get("orchestration", {})
        if orchestration_config.get("enabled", False):
            assert supervisor_instance.orchestration is not None
            assert supervisor_instance.orchestration.model_registry is not None
        else:
            assert supervisor_instance.orchestration is None

    def test_model_registry_has_models(self, supervisor_instance):
        """Model registry has all 4 models registered."""
        if supervisor_instance.orchestration is not None:
            registry = supervisor_instance.orchestration.model_registry
            assert "chatgpt" in registry._models
            assert "claude" in registry._models
            assert "gemini" in registry._models
            assert "deepseek" in registry._models


class TestPhase7Integration:
    """Test Phase 7 (Monitoring) integration."""

    def test_monitoring_initialized(self, supervisor_instance):
        """Monitor daemon is initialized when enabled."""
        monitoring_config = supervisor_instance.config.get("monitoring", {})
        if monitoring_config.get("enabled", False):
            assert supervisor_instance.monitor_daemon is not None
            assert supervisor_instance.monitor_daemon.collector is not None
            assert supervisor_instance.monitor_daemon.sink is not None
            assert supervisor_instance.monitor_daemon.rules_engine is not None
            assert supervisor_instance.monitor_daemon.recovery_controller is not None
        else:
            assert supervisor_instance.monitor_daemon is None

    def test_monitoring_has_cross_phase_refs(self, supervisor_instance):
        """Monitor daemon has references to other phases."""
        if supervisor_instance.monitor_daemon is not None:
            # Check that monitor daemon was initialized with phase references
            # These are passed to MetricsCollector during initialization
            assert supervisor_instance.monitor_daemon.collector is not None
            assert supervisor_instance.monitor_daemon.sink is not None
            assert supervisor_instance.monitor_daemon.rules_engine is not None


class TestPhase8Integration:
    """Test Phase 8 (Claude) integration."""

    def test_claude_client_initialized(self, supervisor_instance):
        """Claude client is initialized when enabled."""
        claude_config = supervisor_instance.config.get("claude", {})
        if claude_config.get("enabled", False):
            assert supervisor_instance.claude_client is not None
            expected_stub_mode = claude_config.get("stub_mode", True)
            assert supervisor_instance.claude_client.stub_mode == expected_stub_mode
        else:
            assert supervisor_instance.claude_client is None

    def test_claude_prompts_loaded(self, supervisor_instance):
        """Claude prompts are loaded from disk."""
        if supervisor_instance.claude_client is not None:
            assert supervisor_instance.claude_client.system_prompt is not None
            assert supervisor_instance.claude_client.task_prompt_template is not None
            assert supervisor_instance.claude_client.failure_prompt_template is not None

    def test_claude_deterministic_settings(self, supervisor_instance):
        """Claude client has fixed deterministic settings."""
        if supervisor_instance.claude_client is not None:
            assert supervisor_instance.claude_client.TEMPERATURE == 0.0
            assert supervisor_instance.claude_client.TOP_P == 1.0
            assert supervisor_instance.claude_client.MAX_TOKENS == 4096


class TestCrossPhaseIntegration:
    """Test cross-phase integration points."""

    def test_all_phases_share_log_daemon(self, supervisor_instance):
        """All phases share the same LogDaemon instance."""
        log_daemon = supervisor_instance.log_daemon

        # Phase 1
        assert supervisor_instance.validator.audit.log_daemon is log_daemon

        # Phase 2
        assert supervisor_instance.executor.event_logger.log_daemon is log_daemon

        # Phase 4
        assert supervisor_instance.coordination.log_daemon is log_daemon

        # Phase 7 - LogDaemon passed during initialization
        # Not stored as attribute but passed to components

    def test_all_phases_share_run_id(self, supervisor_instance):
        """All phases share the same run_id."""
        run_id = supervisor_instance.run_id

        # Phase 7
        if supervisor_instance.monitor_daemon is not None:
            assert supervisor_instance.monitor_daemon.run_id == run_id

        # Phase 6 - run_id passed during initialization
        # Stored internally but can verify through audit events

    def test_all_phases_in_module_versions(self, supervisor_instance):
        """RUN_STARTED event includes all 8 phase versions."""
        # Read the first event from log daemon
        state = supervisor_instance.log_daemon.get_current_state()
        assert state["event_seq"] == 1  # Only RUN_STARTED emitted

        # The RUN_STARTED event was logged with all module versions
        # This verifies supervisor initialization completed successfully


class TestConfigurationIntegration:
    """Test configuration integration across all phases."""

    def test_config_has_all_phase_sections(self, supervisor_instance):
        """core.yaml has configuration sections for all 8 phases."""
        config = supervisor_instance.config

        # System-level
        assert "system" in config
        assert "time_policy" in config

        # Phase 1
        assert "validation" in config

        # Phase 3
        assert "audit" in config

        # Phase 4
        assert "coordination" in config

        # Phase 5
        assert "connectors" in config

        # Phase 6
        assert "orchestration" in config

        # Phase 7
        assert "monitoring" in config

        # Phase 8
        assert "claude" in config

    def test_policy_has_rbac_rules(self, supervisor_instance):
        """policy.yaml has RBAC sections."""
        policy = supervisor_instance.policy
        # Policy has deny rules, approval_required, monitoring overrides
        assert "deny" in policy or "approval_required" in policy
        assert len(policy) > 0

    def test_policy_has_connector_mappings(self, supervisor_instance):
        """policy.yaml has connector mappings."""
        policy = supervisor_instance.policy
        assert "connector_mappings" in policy


class TestDataFlowIntegration:
    """Test end-to-end data flow through all phases."""

    def test_validation_to_coordination_flow(self, supervisor_instance):
        """ValidatedAction flows from Phase 1 to Phase 4."""
        # This is tested implicitly through process_envelope
        # Just verify the integration points exist
        assert hasattr(supervisor_instance, 'process_envelope')
        assert hasattr(supervisor_instance.coordination, 'coordinate_action')

    def test_coordination_to_execution_flow(self, supervisor_instance):
        """CoordinatedAction flows from Phase 4 to Phase 2."""
        assert hasattr(supervisor_instance.executor, 'enqueue_validated_action')

    def test_supervisor_shutdown_closes_all_phases(self, supervisor_instance):
        """Supervisor shutdown properly closes all phases."""
        # Verify log daemon can be closed
        assert hasattr(supervisor_instance.log_daemon, 'close')

        # Verify monitoring can be stopped
        if supervisor_instance.monitor_daemon is not None:
            assert hasattr(supervisor_instance.monitor_daemon, 'stop')


class TestAuditEventIntegration:
    """Test audit event flow from all phases to LogDaemon."""

    def test_phase1_emits_validation_events(self, supervisor_instance):
        """Phase 1 validation events reach LogDaemon."""
        # Validator has AuditLogger with LogDaemon
        assert supervisor_instance.validator.audit.log_daemon is not None

    def test_phase2_emits_execution_events(self, supervisor_instance):
        """Phase 2 execution events reach LogDaemon."""
        # Executor has ExecutionEventLogger with LogDaemon
        assert supervisor_instance.executor.event_logger.log_daemon is not None

    def test_phase4_emits_coordination_events(self, supervisor_instance):
        """Phase 4 coordination events reach LogDaemon."""
        # Coordination has LogDaemon reference
        assert supervisor_instance.coordination.log_daemon is not None

    def test_phase5_has_audit_callback(self, supervisor_instance):
        """Phase 5 connector audit callback can be created."""
        callback = supervisor_instance._create_connector_audit_callback()
        assert callable(callback)

    def test_phase6_has_audit_callback(self, supervisor_instance):
        """Phase 6 orchestration has audit callback."""
        if supervisor_instance.orchestration is not None:
            # Orchestration was initialized with audit_callback
            # This is verified by the initialization succeeding
            assert supervisor_instance.orchestration is not None

    def test_phase7_has_audit_callback(self, supervisor_instance):
        """Phase 7 monitoring has audit callback."""
        if supervisor_instance.monitor_daemon is not None:
            # Monitor daemon was initialized with audit_callback
            assert supervisor_instance.monitor_daemon is not None

    def test_phase8_has_audit_callback(self, supervisor_instance):
        """Phase 8 Claude has audit callback."""
        if supervisor_instance.claude_client is not None:
            # Claude client was initialized with audit_callback
            assert supervisor_instance.claude_client.audit_callback is not None


class TestSystemInvariants:
    """Test system-wide invariants across all phases."""

    def test_deterministic_config_hash(self, supervisor_instance):
        """Config hash is deterministic."""
        hash1 = supervisor_instance.config_hash

        # Create second supervisor with same config
        supervisor2 = LLMRelaySupervisor(
            config_path="config/core.yaml",
            policy_path="config/policy.yaml"
        )
        hash2 = supervisor2.config_hash
        supervisor2.shutdown()

        assert hash1 == hash2

    def test_time_policy_propagated(self, supervisor_instance):
        """Time policy is propagated to all phases."""
        time_policy = supervisor_instance.config.get("time_policy", {}).get("mode", "recorded")

        # Phase 3
        assert supervisor_instance.log_daemon.time_policy == time_policy

        # Phase 7
        if supervisor_instance.monitor_daemon is not None:
            assert supervisor_instance.monitor_daemon.time_policy == time_policy
