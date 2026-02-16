"""
Validation Pipeline - Phase 1 Core

Fixed 8-stage order (NO deviation):
1. Envelope validation
2. Schema selection
3. JSON Schema validate payload
4. Pydantic parse + validators
5. RBAC check
6. Sanitization
7. Audit log write
8. Emit ValidatedAction OR emit error

All outputs are envelope-shaped (prepare for future IPC).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Union, Optional
from pathlib import Path

from . import jsonschema_validate
from . import pydantic_validate
from .schema_registry import SchemaRegistry, SchemaRegistryError
from .rbac import RBACPolicy, RBACDeniedError
from .sanitize import sanitize_payload, SanitizationError
from .audit import AuditLogger


class ValidationPipeline:
    """
    Validation pipeline with strict stage ordering.

    Emits ValidatedAction or Error in canonical envelope format.
    """

    def __init__(
        self,
        registry_path: str = "config/schema_registry_index.json",
        policy_path: str = "config/policy.yaml",
        audit_path: str = "/tmp/llm-relay-audit.jsonl",
        envelope_schema_path: str = "schemas/envelope.schema.json",
        base_dir: Optional[str] = None,
    ):
        """
        Initialize validation pipeline.

        Args:
            registry_path: Path to schema registry index
            policy_path: Path to RBAC policy
            audit_path: Path to audit log
            envelope_schema_path: Path to envelope schema
            base_dir: Base directory for resolving paths
        """
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()

        # Load envelope schema
        envelope_path = self.base_dir / envelope_schema_path
        with open(envelope_path, 'r') as f:
            self.envelope_schema = json.load(f)

        # Initialize components
        self.registry = SchemaRegistry(
            registry_path=str(self.base_dir / registry_path),
            base_dir=str(self.base_dir)
        )
        self.rbac = RBACPolicy(policy_path=str(self.base_dir / policy_path))
        self.audit = AuditLogger(audit_path=audit_path)

    def validate(self, envelope: dict) -> dict:
        """
        Execute validation pipeline.

        Args:
            envelope: Envelope dict to validate

        Returns:
            ValidatedAction dict OR Error dict (both in envelope format)
        """
        message_id = envelope.get('message_id')
        principal = envelope.get('actor')
        action = envelope.get('action')

        try:
            # Stage 1: Envelope validation
            self.audit.log_validation_started("envelope_validation", message_id=message_id)
            self._validate_envelope(envelope)
            self.audit.log_validation_passed("envelope_validation", message_id=message_id)

            # Extract envelope fields
            action = envelope['action']
            action_version = envelope['action_version']
            payload = envelope['payload']
            principal = envelope['sender']

            # Stage 2: Schema selection
            self.audit.log_validation_started("schema_selection", message_id=message_id, action=action)
            schema_selection = self.registry.select_schema(action, action_version)
            self.audit.log_validation_passed(
                "schema_selection",
                message_id=message_id,
                action=action,
                details={"schema_hash": schema_selection.schema_hash}
            )

            # Stage 3: JSON Schema validation
            self.audit.log_validation_started("json_schema_validation", message_id=message_id, action=action)
            jsonschema_validate.validate_payload(payload, schema_selection.schema, action)
            self.audit.log_validation_passed("json_schema_validation", message_id=message_id, action=action)

            # Stage 4: Pydantic validation
            self.audit.log_validation_started("pydantic_validation", message_id=message_id, action=action)
            pydantic_validate.validate_payload(payload, action)
            self.audit.log_validation_passed("pydantic_validation", message_id=message_id, action=action)

            # Stage 5: RBAC check
            self.audit.log_validation_started("rbac_check", message_id=message_id, action=action, principal=principal)
            # For RBAC, we need a resource path - extract from payload if it's a fs.* action
            resource = self._extract_resource(action, payload)
            rule_id = self.rbac.check_access(principal, action, resource)
            self.audit.log_validation_passed(
                "rbac_check",
                message_id=message_id,
                action=action,
                principal=principal,
                details={"rule_id": rule_id, "resource": resource}
            )

            # Stage 6: Sanitization
            self.audit.log_validation_started("sanitization", message_id=message_id, action=action)
            sanitized_payload = sanitize_payload(payload, action)
            self.audit.log_validation_passed("sanitization", message_id=message_id, action=action)

            # Stage 7: Audit log (implicit - already logging)
            # Stage 8: Emit ValidatedAction
            validated_action = self._create_validated_action(
                envelope=envelope,
                schema_hash=schema_selection.schema_hash,
                rbac_rule_id=rule_id,
                sanitized_payload=sanitized_payload,
            )

            return validated_action

        except Exception as e:
            # Validation failed - emit error
            error = self._create_error(
                exception=e,
                envelope=envelope,
                message_id=message_id,
                principal=principal,
                action=action,
            )

            return error

    def _validate_envelope(self, envelope: dict):
        """
        Validate envelope structure.

        Raises:
            jsonschema_validate.JSONSchemaValidationError: If JSON Schema validation fails
            pydantic_validate.PydanticValidationError: If Pydantic validation fails
        """
        # JSON Schema validation
        jsonschema_validate.validate_envelope(envelope, self.envelope_schema)

        # Pydantic validation
        pydantic_validate.validate_envelope(envelope)

    def _extract_resource(self, action: str, payload: dict) -> str:
        """
        Extract resource path from payload for RBAC check.

        Args:
            action: Action identifier
            payload: Action payload

        Returns:
            Resource path (workspace-relative for fs.* actions, "*" for others)
        """
        if action.startswith('fs.'):
            # Filesystem actions have a 'path' field
            path = payload.get('path', '')
            # Convert to workspace-absolute path for RBAC
            return f"/workspace/{path}"
        else:
            # Non-filesystem actions use wildcard resource
            return "*"

    def _create_validated_action(
        self,
        envelope: dict,
        schema_hash: str,
        rbac_rule_id: str,
        sanitized_payload: dict,
    ) -> dict:
        """Create ValidatedAction output."""
        return {
            "validation_id": str(uuid.uuid4()),
            "original_envelope": envelope,
            "validated_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "schema_hash": schema_hash,
            "rbac_rule_id": rbac_rule_id,
            "sanitized_payload": sanitized_payload,
        }

    def _create_error(
        self,
        exception: Exception,
        envelope: dict,
        message_id: Optional[str],
        principal: Optional[str],
        action: Optional[str],
    ) -> dict:
        """
        Create Error output from exception.

        Args:
            exception: Exception that occurred
            envelope: Original envelope (if parseable)
            message_id: Message ID (if available)
            principal: Principal (if available)
            action: Action (if available)

        Returns:
            Error dict
        """
        # Map exception type to error code and stage
        error_code, stage = self._map_exception_to_error(exception)

        # Extract error message and details
        error_message = str(exception)
        details = {}

        if hasattr(exception, 'details'):
            details['validation_errors'] = exception.details

        if hasattr(exception, 'reason'):
            details['reason'] = exception.reason

        # Log failure
        self.audit.log_validation_failed(
            stage=stage,
            error_code=error_code,
            error_message=error_message,
            message_id=message_id,
            principal=principal,
            action=action,
            details=details if details else None,
        )

        # Create error output
        error = {
            "error_id": str(uuid.uuid4()),
            "error_code": error_code,
            "stage": stage,
            "message": error_message,
            "occurred_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }

        if details:
            error["details"] = details

        if envelope:
            error["original_envelope"] = envelope

        return error

    def _map_exception_to_error(self, exception: Exception) -> tuple[str, str]:
        """
        Map exception to (error_code, stage).

        Returns:
            Tuple of (error_code, stage)
        """
        if isinstance(exception, (
            jsonschema_validate.JSONSchemaValidationError,
            pydantic_validate.PydanticValidationError
        )):
            # Determine stage from exception context
            # Check if it's envelope or payload validation
            if 'Envelope' in str(exception) or 'envelope' in str(exception):
                return ("ENVELOPE_INVALID", "envelope_validation")
            else:
                # Try to determine if JSON Schema or Pydantic
                if isinstance(exception, jsonschema_validate.JSONSchemaValidationError):
                    return ("JSON_SCHEMA_FAILED", "json_schema_validation")
                else:
                    return ("PYDANTIC_FAILED", "pydantic_validation")

        elif isinstance(exception, SchemaRegistryError):
            return ("SCHEMA_NOT_FOUND", "schema_selection")

        elif isinstance(exception, RBACDeniedError):
            return ("RBAC_DENIED", "rbac_check")

        elif isinstance(exception, SanitizationError):
            return ("SANITIZATION_FAILED", "sanitization")

        else:
            return ("UNKNOWN_ERROR", "output_emit")
