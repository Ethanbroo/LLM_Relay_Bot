"""Idempotency ledger for deterministic replay control.

Phase 5 Invariant: Centralized idempotency enforcement,
not per-connector logic.
"""

from dataclasses import dataclass
from typing import Optional
from connectors.results import ConnectorResult, ConnectorStatus


@dataclass
class IdempotencyRecord:
    """Record of connector execution."""
    idempotency_key: str
    status: ConnectorStatus
    external_transaction_id: Optional[str]
    result_hash: str
    result: Optional[ConnectorResult] = None


class IdempotencyLedger:
    """In-memory idempotency ledger.

    Phase 5 Invariant: If idempotency_key seen again,
    return prior result without re-execution.
    """

    def __init__(self):
        """Initialize idempotency ledger."""
        # idempotency_key -> IdempotencyRecord
        self.records: dict[str, IdempotencyRecord] = {}

    def check(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        """Check if idempotency key has been seen.

        Args:
            idempotency_key: Idempotency key to check

        Returns:
            IdempotencyRecord if found, None otherwise
        """
        return self.records.get(idempotency_key)

    def record(
        self,
        idempotency_key: str,
        result: ConnectorResult
    ) -> None:
        """Record connector execution result.

        Args:
            idempotency_key: Idempotency key
            result: ConnectorResult
        """
        record = IdempotencyRecord(
            idempotency_key=idempotency_key,
            status=result.status,
            external_transaction_id=result.external_transaction_id,
            result_hash=result.result_hash,
            result=result
        )

        self.records[idempotency_key] = record

    def has_executed(self, idempotency_key: str) -> bool:
        """Check if operation has been executed.

        Args:
            idempotency_key: Idempotency key

        Returns:
            True if executed, False otherwise
        """
        return idempotency_key in self.records

    def get_result(self, idempotency_key: str) -> Optional[ConnectorResult]:
        """Get prior result for idempotency key.

        Args:
            idempotency_key: Idempotency key

        Returns:
            ConnectorResult if found, None otherwise
        """
        record = self.records.get(idempotency_key)
        return record.result if record else None
