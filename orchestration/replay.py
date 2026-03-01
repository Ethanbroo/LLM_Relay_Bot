"""Replay harness for Layer 2 dev.generate_code decisions.

Layer 2 Invariant: A ReplayRecord captures sufficient context to re-run the
four-stage gate and determine whether a decision mismatch is due to:
  (a) environment drift (Python version, platform, prompt hash changed) — expected
  (b) data tampering (diff_identity_hash or gate_result changed) — anomaly

Replay verification DOES NOT re-run LLM calls. It re-runs the gate stages
against the original proposal data using the current environment, then compares
post_apply_workspace_hash. A mismatch after controlling for environment drift
indicates the diff was mutated after gating.

Fields captured for determinism (from gap analysis):
  - python_version, python_executable, platform — from SandboxEnvironment
  - prompt_hash — from PromptVersion
  - schema_version — identifies which JSON schema governed validation
  - diff_identity_hash — structural fingerprint of the diff
  - post_apply_workspace_hash — hash of workspace state after applying diff
"""

from dataclasses import dataclass, field
from typing import Optional

from orchestration.code_gate import CodeGateResult, SandboxEnvironment, CodeGate
from orchestration.code_proposal import CodeDiffProposal


@dataclass(frozen=True)
class ReplayRecord:
    """Complete decision context for deterministic replay verification.

    All fields are captured at decision time. The record is intended to be
    serialized (via to_dict()) and stored alongside the audit log entry.
    """
    run_id: str
    prompt_hash: str
    schema_version: str          # e.g. "dev.generate_code/1.0.0"
    diff_identity_hash: str
    structural_agreement_score: float
    decision: str                # "approved" | "rejected" | "escalated"
    gate_result: CodeGateResult
    sandbox_env: SandboxEnvironment = field(default_factory=SandboxEnvironment.capture)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "diff_identity_hash": self.diff_identity_hash,
            "gate_result": self.gate_result.to_dict(),
            "prompt_hash": self.prompt_hash,
            "run_id": self.run_id,
            "sandbox_env": self.sandbox_env.to_dict(),
            "schema_version": self.schema_version,
            "structural_agreement_score": self.structural_agreement_score,
        }


@dataclass(frozen=True)
class ReplayVerificationResult:
    """Result of replaying a stored ReplayRecord.

    Fields:
        matched: True iff the replayed gate decision matches the original
        original_hash: post_apply_workspace_hash from the stored record
        replayed_hash: post_apply_workspace_hash from the replay run
        environment_changed: True iff sandbox_env differs between runs
        mismatch_reason: Human-readable explanation when matched == False
    """
    matched: bool
    original_hash: str
    replayed_hash: str
    environment_changed: bool
    mismatch_reason: Optional[str] = field(default=None)

    def to_dict(self) -> dict:
        return {
            "environment_changed": self.environment_changed,
            "matched": self.matched,
            "mismatch_reason": self.mismatch_reason,
            "original_hash": self.original_hash,
            "replayed_hash": self.replayed_hash,
        }


def verify_replay(
    record: ReplayRecord,
    proposal: CodeDiffProposal,
) -> ReplayVerificationResult:
    """Re-run the code gate against the original proposal and compare results.

    Args:
        record: The stored ReplayRecord from the original decision
        proposal: The original CodeDiffProposal (must match record.diff_identity_hash)

    Returns:
        ReplayVerificationResult describing whether the replay matched
    """
    # Verify the proposal matches the record's diff_identity_hash
    if proposal.diff_identity_hash != record.diff_identity_hash:
        current_env = SandboxEnvironment.capture()
        env_changed = current_env.to_dict() != record.sandbox_env.to_dict()
        return ReplayVerificationResult(
            matched=False,
            original_hash=record.gate_result.post_apply_workspace_hash,
            replayed_hash="",
            environment_changed=env_changed,
            mismatch_reason=(
                f"Proposal diff_identity_hash mismatch: "
                f"record={record.diff_identity_hash[:16]}..., "
                f"proposal={proposal.diff_identity_hash[:16]}..."
            ),
        )

    # Re-run the gate
    gate = CodeGate()
    replayed_result = gate.run(proposal)
    current_env = SandboxEnvironment.capture()

    original_hash = record.gate_result.post_apply_workspace_hash
    replayed_hash = replayed_result.post_apply_workspace_hash
    env_changed = current_env.to_dict() != record.sandbox_env.to_dict()

    # Decision match: both passed or both failed the same stage
    original_passed = record.gate_result.passed
    replayed_passed = replayed_result.passed
    original_stage = record.gate_result.failed_stage
    replayed_stage = replayed_result.failed_stage

    if original_passed != replayed_passed:
        return ReplayVerificationResult(
            matched=False,
            original_hash=original_hash,
            replayed_hash=replayed_hash,
            environment_changed=env_changed,
            mismatch_reason=(
                f"Gate decision flipped: original={'passed' if original_passed else 'failed'}, "
                f"replay={'passed' if replayed_passed else 'failed'} "
                f"(failed_stage={replayed_stage!r})"
            ),
        )

    if original_hash and replayed_hash and original_hash != replayed_hash:
        return ReplayVerificationResult(
            matched=False,
            original_hash=original_hash,
            replayed_hash=replayed_hash,
            environment_changed=env_changed,
            mismatch_reason=(
                f"post_apply_workspace_hash mismatch — diff may have been mutated. "
                f"Environment drift: {env_changed}"
            ),
        )

    return ReplayVerificationResult(
        matched=True,
        original_hash=original_hash,
        replayed_hash=replayed_hash,
        environment_changed=env_changed,
    )
