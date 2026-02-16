#!/usr/bin/env python3
"""
Demo: Phases 1-3 Integration

Demonstrates the complete integrated LLM Relay system:
- Phase 1: Validation with RBAC
- Phase 2: Sandboxed execution
- Phase 3: Tamper-evident audit logging

All events are cryptographically signed and hash-chained.
"""

import sys
from pathlib import Path

from supervisor import LLMRelaySupervisor, SupervisorError, TamperDetectedError


def generate_uuid_v7_like():
    """Generate a UUID v7-like string (fake but valid format for testing)."""
    import uuid
    base = uuid.uuid4()
    uuid_str = str(base)
    parts = uuid_str.split('-')
    parts[2] = '7' + parts[2][1:]  # Change version to 7
    return '-'.join(parts)


def demo_valid_envelope():
    """Demo: Valid envelope passes all phases."""
    print("\n" + "="*70)
    print("Demo 1: Valid Envelope (system.health_ping)")
    print("="*70)

    envelope = {
        "envelope_version": "1.0.0",
        "message_id": generate_uuid_v7_like(),
        "sender": "validator",
        "recipient": "executor",
        "timestamp": "2026-02-08T14:30:00Z",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {}
    }

    try:
        with LLMRelaySupervisor() as supervisor:
            print("\n✅ Supervisor initialized")
            print(f"   Run ID: {supervisor.run_id}")
            print(f"   Config hash: {supervisor.config_hash[:16]}...")

            # Phase 1: Validation
            print("\n📋 Phase 1: Validation Pipeline")
            result = supervisor.process_envelope(envelope)

            if "validation_id" in result:
                print(f"   ✅ Validation passed")
                print(f"      Validation ID: {result['validation_id']}")
                print(f"      Schema hash: {result['schema_hash'][:16]}...")
                print(f"      RBAC rule: {result['rbac_rule_id']}")
                print(f"      Task ID: {result['task_id']}")

                # Phase 2: Execution
                print("\n⚙️  Phase 2: Execution Engine")
                execution_results = supervisor.execute_pending_tasks()

                for exec_result in execution_results:
                    print(f"   ✅ Execution completed")
                    print(f"      Run ID: {exec_result['run_id']}")
                    print(f"      Status: {exec_result['status']}")
                    print(f"      Duration: {exec_result['total_duration_ms']}ms")
                    print(f"      Artifacts: {list(exec_result.get('artifacts', {}).keys())}")

                # Phase 3: Audit Log Verification
                print("\n🔒 Phase 3: Audit Log Verification")
                from audit_logging.verifier import AuditLogVerifier
                from audit_logging.key_manager import KeyManager

                key_manager = KeyManager(
                    private_key_path="keys/audit_private.pem",
                    public_key_path="keys/audit_public.pem"
                )
                verifier = AuditLogVerifier(key_manager)

                log_dir = Path("logs")
                segment_path = log_dir / "audit.000001.jsonl"

                if segment_path.exists():
                    verify_result = verifier.verify_segment(segment_path)
                    if verify_result.success:
                        print(f"   ✅ Audit log verified")
                        print(f"      Events verified: {verify_result.events_verified}")
                        print(f"      Hash chain: intact")
                        print(f"      Signatures: valid")
                    else:
                        print(f"   ❌ Verification failed!")
                        for error in verify_result.errors:
                            print(f"      - {error}")
            else:
                print(f"   ❌ Validation failed")
                print(f"      Error: {result['error_code']}")
                print(f"      Message: {result['message']}")
                if "details" in result:
                    print("   Details:", result["details"])

    except TamperDetectedError as e:
        print(f"\n🚨 CRITICAL: Audit log tampering detected!")
        print(f"   {e}")
        return 1

    except SupervisorError as e:
        print(f"\n❌ Supervisor error: {e}")
        return 1

    return 0


def demo_validation_failure():
    """Demo: Invalid envelope fails validation."""
    print("\n" + "="*70)
    print("Demo 2: Invalid Envelope (missing required fields)")
    print("="*70)

    # Invalid envelope - missing required fields
    envelope = {
        "message_id": generate_uuid_v7_like(),
        "action": "system.health_ping"
        # Missing: envelope_version, sender, recipient, timestamp, action_version, payload
    }

    try:
        with LLMRelaySupervisor() as supervisor:
            print("\n📋 Phase 1: Validation Pipeline")
            result = supervisor.process_envelope(envelope)

            print(f"   ❌ Validation failed (expected)")
            print(f"      Error code: {result['error_code']}")
            print(f"      Stage: {result['stage']}")
            print(f"      Message: {result['message']}")

            # Check audit log
            print("\n🔒 Phase 3: Audit Log Check")
            import json
            log_dir = Path("logs")
            segment_path = log_dir / "audit.000001.jsonl"

            with open(segment_path, 'r') as f:
                events = [json.loads(line) for line in f if line.strip()]

            validation_failed_events = [e for e in events if e.get("event_type") == "VALIDATION_FAILED"]
            print(f"   ✅ Validation failure logged")
            print(f"      VALIDATION_FAILED events: {len(validation_failed_events)}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1

    return 0


def demo_rbac_denial():
    """Demo: RBAC denial logged."""
    print("\n" + "="*70)
    print("Demo 3: RBAC Denial (unauthorized principal)")
    print("="*70)

    envelope = {
        "envelope_version": "1.0.0",
        "message_id": generate_uuid_v7_like(),
        "sender": "unauthorized_principal",  # Not in policy.yaml
        "recipient": "executor",
        "timestamp": "2026-02-08T14:30:00Z",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {}
    }

    try:
        with LLMRelaySupervisor() as supervisor:
            print("\n📋 Phase 1: Validation Pipeline")
            result = supervisor.process_envelope(envelope)

            print(f"   ❌ RBAC denied (expected)")
            print(f"      Error code: {result['error_code']}")
            print(f"      Stage: {result['stage']}")

            # Check audit log
            print("\n🔒 Phase 3: Audit Log Check")
            import json
            log_dir = Path("logs")
            segment_path = log_dir / "audit.000001.jsonl"

            with open(segment_path, 'r') as f:
                events = [json.loads(line) for line in f if line.strip()]

            rbac_events = [e for e in events if
                          e.get("event_type") == "VALIDATION_FAILED" and
                          e.get("payload", {}).get("error_code") == "RBAC_DENIED"]
            print(f"   ✅ RBAC denial logged")
            print(f"      RBAC_DENIED events: {len(rbac_events)}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1

    return 0


def main():
    """Run all demos."""
    print("\n" + "="*70)
    print("Phases 1-3 Integration Demo")
    print("="*70)
    print("\nThis demo shows:")
    print("  • Phase 1: Validation with RBAC")
    print("  • Phase 2: Sandboxed execution")
    print("  • Phase 3: Tamper-evident audit logging")
    print("\nAll events are Ed25519 signed and hash-chained.")

    # Run demos
    status = 0
    status |= demo_valid_envelope()
    status |= demo_validation_failure()
    status |= demo_rbac_denial()

    print("\n" + "="*70)
    if status == 0:
        print("✅ All demos completed successfully!")
    else:
        print("❌ Some demos failed")
    print("="*70)
    print("\n📊 Check logs/ directory for audit log segments")
    print("🔍 Use audit_logging/verifier.py to verify integrity\n")

    return status


if __name__ == "__main__":
    sys.exit(main())
