"""Connector result dataclasses.

Phase 5 Invariant: Results are strictly typed and include
deterministic hashes for verification.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class ConnectorStatus(str, Enum):
    """Connector execution status."""
    SUCCESS = "success"
    FAILURE = "failure"


class RollbackStatus(str, Enum):
    """Rollback execution status."""
    SUCCESS = "success"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class VerificationMethod(str, Enum):
    """Rollback verification method."""
    FILE_HASH = "file_hash"
    DIRECTORY_LISTING = "directory_listing"
    EXTERNAL_VERIFICATION = "external_verification"
    NOT_APPLICABLE = "not_applicable"


class ArtifactType(str, Enum):
    """Execution artifact type."""
    FILE_CONTENTS = "file_contents"
    DIRECTORY_LISTING = "directory_listing"
    EXTERNAL_REFERENCE = "external_reference"


@dataclass
class ExecutionArtifact:
    """Artifact produced by connector execution.

    Phase 5 Invariant: Contains only hashes and opaque handles,
    never raw content.
    """
    artifact_type: ArtifactType
    artifact_hash: str  # SHA-256 hex
    artifact_ref: str  # Opaque handle only


@dataclass
class ConnectorResult:
    """Result of connector execution.

    Phase 5 Invariant: Result must be deterministic and include
    result_hash for verification.
    """
    status: ConnectorStatus
    connector_type: str
    idempotency_key: str
    external_transaction_id: Optional[str] = None
    artifacts: dict[str, str] = field(default_factory=dict)  # artifact_name -> artifact_hash
    side_effect_summary: str = ""  # Bounded to 500 chars
    result_hash: str = ""  # SHA-256 hex (computed excluding this field)
    error_code: Optional[str] = None
    error_message: Optional[str] = None  # Bounded to 200 chars


@dataclass
class RollbackResult:
    """Result of rollback operation.

    Phase 5 Invariant: Rollback must be verifiable.
    """
    rollback_status: RollbackStatus
    verification_method: VerificationMethod
    verification_artifact_hash: Optional[str] = None
    notes: str = ""  # Bounded to 300 chars
