"""Phase 2: Deterministic Execution Core.

This module implements the execution engine that:
1. Accepts ValidatedAction from Phase 1
2. Executes actions in sandboxed environments
3. Produces deterministic ExecutionResult outputs
4. Implements snapshot/rollback for retry safety
5. Emits structured audit events

Non-negotiable invariants:
- One task = one ValidatedAction (atomic execution unit)
- Single-consumer FIFO queue (deterministic ordering)
- Deterministic task identity (same ValidatedAction → same task_id)
- Per-task sandbox (isolated, no network, no shell)
- Snapshot-before-execute (mandatory)
- Rollback-before-retry (mandatory)
- Rollback failure is terminal (task becomes dead)
- Phase boundaries enforced (executor sees only ValidatedAction)
"""
