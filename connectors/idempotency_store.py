"""Persistent SQLite-backed idempotency ledger.

Replaces the in-memory IdempotencyLedger with a durable store that
survives process restarts, so crash recovery actually works.
"""

import json
import sqlite3
import hashlib
import threading
from pathlib import Path
from typing import Optional

from connectors.results import ConnectorResult, ConnectorStatus
from connectors.idempotency import IdempotencyRecord


class PersistentIdempotencyLedger:
    """SQLite-backed idempotency ledger.

    Phase 5 Invariant: If idempotency_key seen again, return prior
    result without re-execution — even across process restarts.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS idempotency_records (
        idempotency_key TEXT PRIMARY KEY,
        status          TEXT NOT NULL,
        external_tx_id  TEXT,
        result_hash     TEXT NOT NULL,
        result_json     TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """

    def __init__(self, db_path: str | Path = "data/idempotency.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _result_to_dict(self, result: ConnectorResult) -> dict:
        return {
            "status": result.status.value,
            "connector_type": result.connector_type,
            "idempotency_key": result.idempotency_key,
            "external_transaction_id": result.external_transaction_id,
            "artifacts": result.artifacts,
            "side_effect_summary": result.side_effect_summary,
            "result_hash": result.result_hash,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }

    def _dict_to_result(self, data: dict) -> ConnectorResult:
        return ConnectorResult(
            status=ConnectorStatus(data["status"]),
            connector_type=data["connector_type"],
            idempotency_key=data["idempotency_key"],
            external_transaction_id=data.get("external_transaction_id"),
            artifacts=data.get("artifacts", {}),
            side_effect_summary=data.get("side_effect_summary", ""),
            result_hash=data.get("result_hash", ""),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
        )

    def check(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM idempotency_records WHERE idempotency_key = ?",
                    (idempotency_key,)
                ).fetchone()
                if row is None:
                    return None
                result_data = json.loads(row["result_json"])
                result = self._dict_to_result(result_data)
                return IdempotencyRecord(
                    idempotency_key=idempotency_key,
                    status=ConnectorStatus(row["status"]),
                    external_transaction_id=row["external_tx_id"],
                    result_hash=row["result_hash"],
                    result=result,
                )

    def record(self, idempotency_key: str, result: ConnectorResult) -> None:
        result_dict = self._result_to_dict(result)
        result_json = json.dumps(result_dict, sort_keys=True)
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO idempotency_records
                       (idempotency_key, status, external_tx_id, result_hash, result_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        idempotency_key,
                        result.status.value,
                        result.external_transaction_id,
                        result_hash,
                        result_json,
                    )
                )

    def has_executed(self, idempotency_key: str) -> bool:
        return self.check(idempotency_key) is not None

    def get_result(self, idempotency_key: str) -> Optional[ConnectorResult]:
        record = self.check(idempotency_key)
        return record.result if record else None
