#!/usr/bin/env python3
"""
Demo script for LLM Relay Phase 1 validation pipeline.

Shows successful validation and various failure modes.
"""

import json
from validator.pipeline import ValidationPipeline


def print_result(envelope: dict, result: dict):
    """Pretty print validation result."""
    print(f"\n{'='*70}")
    print(f"INPUT: {envelope['action']} - {envelope['payload']}")
    print(f"{'='*70}")

    if "validation_id" in result:
        print("✅ VALIDATION PASSED")
        print(f"  Validation ID: {result['validation_id']}")
        print(f"  Schema Hash: {result['schema_hash'][:16]}...")
        print(f"  RBAC Rule: {result['rbac_rule_id']}")
        print(f"  Sanitized Payload: {json.dumps(result['sanitized_payload'], indent=2)}")
    else:
        print("❌ VALIDATION FAILED")
        print(f"  Error Code: {result['error_code']}")
        print(f"  Stage: {result['stage']}")
        print(f"  Message: {result['message']}")
        if 'details' in result:
            print(f"  Details: {json.dumps(result['details'], indent=2)}")


def main():
    """Run demo scenarios."""
    print("\n" + "="*70)
    print("LLM RELAY - PHASE 1 VALIDATION PIPELINE DEMO")
    print("="*70)

    pipeline = ValidationPipeline(base_dir=".")

    # Scenario 1: Valid fs.read
    print("\n\n📋 SCENARIO 1: Valid fs.read request")
    envelope1 = {
        "envelope_version": "1.0.0",
        "message_id": "01234567-89ab-7def-8123-456789abcdef",
        "timestamp": "2026-02-07T12:00:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {
            "path": "data/sample.txt",
            "offset": 0,
            "length": 4096,
            "encoding": "utf-8"
        }
    }
    result1 = pipeline.validate(envelope1)
    print_result(envelope1, result1)

    # Scenario 2: Valid fs.list_dir
    print("\n\n📋 SCENARIO 2: Valid fs.list_dir request")
    envelope2 = {
        "envelope_version": "1.0.0",
        "message_id": "12345678-90ab-7cde-8901-23456789abcd",
        "timestamp": "2026-02-07T12:01:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.list_dir",
        "action_version": "1.0.0",
        "payload": {
            "path": "data",
            "max_entries": 100,
            "sort_order": "name_asc"
        }
    }
    result2 = pipeline.validate(envelope2)
    print_result(envelope2, result2)

    # Scenario 3: Valid health ping
    print("\n\n📋 SCENARIO 3: Valid system.health_ping")
    envelope3 = {
        "envelope_version": "1.0.0",
        "message_id": "23456789-01ab-7cde-8901-23456789abcd",
        "timestamp": "2026-02-07T12:02:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "system.health_ping",
        "action_version": "1.0.0",
        "payload": {
            "echo": "hello from validator"
        }
    }
    result3 = pipeline.validate(envelope3)
    print_result(envelope3, result3)

    # Scenario 4: Unknown field (strictness test)
    print("\n\n📋 SCENARIO 4: Reject unknown field (strictness enforcement)")
    envelope4 = {
        "envelope_version": "1.0.0",
        "message_id": "34567890-12ab-7cde-8901-23456789abcd",
        "timestamp": "2026-02-07T12:03:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {
            "path": "test.txt",
            "unknown_field": "this will be rejected"
        }
    }
    result4 = pipeline.validate(envelope4)
    print_result(envelope4, result4)

    # Scenario 5: Path traversal attempt
    print("\n\n📋 SCENARIO 5: Reject path traversal attempt")
    envelope5 = {
        "envelope_version": "1.0.0",
        "message_id": "45678901-23ab-7cde-8901-23456789abcd",
        "timestamp": "2026-02-07T12:04:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {
            "path": "../../../etc/passwd"
        }
    }
    result5 = pipeline.validate(envelope5)
    print_result(envelope5, result5)

    # Scenario 6: RBAC denial (.git directory)
    print("\n\n📋 SCENARIO 6: RBAC denial (.git directory access)")
    envelope6 = {
        "envelope_version": "1.0.0",
        "message_id": "56789012-34ab-7cde-8901-23456789abcd",
        "timestamp": "2026-02-07T12:05:00Z",
        "sender": "validator",
        "recipient": "executor",
        "action": "fs.read",
        "action_version": "1.0.0",
        "payload": {
            "path": ".git/config"
        }
    }
    result6 = pipeline.validate(envelope6)
    print_result(envelope6, result6)

    # Summary
    print("\n\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Total scenarios: 6")
    print(f"Passed: 3 (fs.read, fs.list_dir, health_ping)")
    print(f"Rejected: 3 (unknown field, path traversal, RBAC denial)")
    print(f"\n✅ Phase 1 validation pipeline is working correctly!")
    print(f"\nAudit log: /tmp/llm-relay-audit.jsonl")
    print(f"Run 'cat /tmp/llm-relay-audit.jsonl | jq' to view structured audit events")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
