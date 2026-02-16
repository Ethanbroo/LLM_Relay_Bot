"""Phase 7: Monitoring & Recovery

Phase 7 Invariants:
- All metrics are from a closed enum
- Sampling is deterministic and tick-driven
- Rules evaluation is fixed-order and deterministic
- Recovery actions are closed and auditable
- Incidents are bounded and redacted
- Monitoring cannot expand scope without schema changes
- Recovery never bypasses Supervisor control plane
- All failure modes fail closed with audit evidence

Version: 1.0.0
"""

__version__ = "1.0.0"
