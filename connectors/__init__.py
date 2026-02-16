"""Phase 5: Connectors (Controlled Power)

This module implements:
- Connector base interface with strict lifecycle
- Closed connector registry
- Idempotency ledger for deterministic replay
- Secrets provider with opaque handles
- Audit integration for all connector operations
- LocalFS and stub connectors
"""

__version__ = "1.0.0"
