"""Tests for orchestration/replay.py.

Covers: ReplayRecord construction, to_dict() serialization, verify_replay()
success and failure paths (diff_identity_hash mismatch, gate decision flip,
workspace hash mismatch), and environment change detection.
"""

import tempfile
import pytest

from orchestration.code_gate import CodeGate, CodeGateResult, SandboxEnvironment
from orchestration.code_proposal import DiffEntry, CodeDiffProposal
from orchestration.replay import ReplayRecord, ReplayVerificationResult, verify_replay


FAKE_HASH = "a" * 64


def _make_proposal(entries, workspace=None):
    if workspace is None:
        workspace = tempfile.gettempdir()
    return CodeDiffProposal(
        diff_entries=tuple(entries),
        workspace_root=workspace,
        proposal_hash=FAKE_HASH,
    )


def _make_gate_result(passed=True, post_apply_hash="", failed_stage=None, failure_reason=None):
    env = SandboxEnvironment.capture()
    return CodeGateResult(
        passed=passed,
        sandbox_env=env,
        failed_stage=failed_stage,
        failure_reason=failure_reason,
        post_apply_workspace_hash=post_apply_hash,
    )


def _make_record(proposal, gate_result=None, decision="approved"):
    if gate_result is None:
        gate_result = _make_gate_result(passed=True, post_apply_hash="b" * 64)
    return ReplayRecord(
        run_id="test-run-001",
        prompt_hash="c" * 64,
        schema_version="dev.generate_code/1.0.0",
        diff_identity_hash=proposal.diff_identity_hash,
        structural_agreement_score=1.0,
        decision=decision,
        gate_result=gate_result,
    )


class TestReplayRecordConstruction:
    def test_fields_accessible(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _make_proposal([DiffEntry(file_path="a.py", operation="create")], tmpdir)
            record = _make_record(p)
            assert record.run_id == "test-run-001"
            assert record.schema_version == "dev.generate_code/1.0.0"
            assert record.decision == "approved"
            assert record.structural_agreement_score == 1.0

    def test_sandbox_env_captured_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _make_proposal([DiffEntry(file_path="a.py", operation="create")], tmpdir)
            record = _make_record(p)
            assert record.sandbox_env is not None
            assert record.sandbox_env.python_version != ""

    def test_to_dict_has_sorted_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _make_proposal([DiffEntry(file_path="a.py", operation="create")], tmpdir)
            record = _make_record(p)
            d = record.to_dict()
            keys = list(d.keys())
            assert keys == sorted(keys)

    def test_to_dict_contains_expected_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _make_proposal([DiffEntry(file_path="a.py", operation="create")], tmpdir)
            gate_result = _make_gate_result(passed=True, post_apply_hash="d" * 64)
            record = ReplayRecord(
                run_id="r1",
                prompt_hash="e" * 64,
                schema_version="dev.generate_code/1.0.0",
                diff_identity_hash=p.diff_identity_hash,
                structural_agreement_score=0.95,
                decision="approved",
                gate_result=gate_result,
            )
            d = record.to_dict()
            assert d["run_id"] == "r1"
            assert d["schema_version"] == "dev.generate_code/1.0.0"
            assert d["decision"] == "approved"
            assert d["structural_agreement_score"] == 0.95
            assert "gate_result" in d
            assert "sandbox_env" in d


class TestReplayVerificationResultSerialization:
    def test_to_dict_has_sorted_keys(self):
        result = ReplayVerificationResult(
            matched=True,
            original_hash="x" * 64,
            replayed_hash="x" * 64,
            environment_changed=False,
        )
        d = result.to_dict()
        keys = list(d.keys())
        assert keys == sorted(keys)

    def test_mismatch_reason_none_when_matched(self):
        result = ReplayVerificationResult(
            matched=True,
            original_hash="x" * 64,
            replayed_hash="x" * 64,
            environment_changed=False,
        )
        assert result.mismatch_reason is None


class TestVerifyReplay:
    def test_replay_matches_on_same_proposal(self):
        """Running gate on same proposal should produce a match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="ok.py", operation="create", content="x = 1\n")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            gate_result = gate.run(p)
            record = ReplayRecord(
                run_id="r1",
                prompt_hash="f" * 64,
                schema_version="dev.generate_code/1.0.0",
                diff_identity_hash=p.diff_identity_hash,
                structural_agreement_score=1.0,
                decision="approved",
                gate_result=gate_result,
            )
            result = verify_replay(record, p)
            assert result.matched is True
            assert result.mismatch_reason is None

    def test_replay_detects_diff_identity_hash_mismatch(self):
        """If proposal's diff_identity_hash doesn't match the record, return mismatch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p_original = _make_proposal(
                [DiffEntry(file_path="a.py", operation="create", content="x = 1\n")], tmpdir
            )
            p_mutated = _make_proposal(
                [DiffEntry(file_path="b.py", operation="create", content="x = 2\n")], tmpdir
            )
            gate = CodeGate()
            gate_result = gate.run(p_original)
            record = ReplayRecord(
                run_id="r1",
                prompt_hash="g" * 64,
                schema_version="dev.generate_code/1.0.0",
                diff_identity_hash=p_original.diff_identity_hash,
                structural_agreement_score=1.0,
                decision="approved",
                gate_result=gate_result,
            )
            # Pass mutated proposal — hash won't match
            result = verify_replay(record, p_mutated)
            assert result.matched is False
            assert "mismatch" in result.mismatch_reason.lower()
            assert result.replayed_hash == ""

    def test_replay_detects_workspace_hash_mismatch(self):
        """If post_apply_workspace_hash differs after replay, return mismatch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="ok.py", operation="create", content="x = 1\n")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            real_gate_result = gate.run(p)

            # Fabricate a stored record with a different post_apply_workspace_hash
            env = SandboxEnvironment.capture()
            fake_gate_result = CodeGateResult(
                passed=True,
                sandbox_env=env,
                post_apply_workspace_hash="0" * 64,  # deliberately wrong
            )
            record = ReplayRecord(
                run_id="r2",
                prompt_hash="h" * 64,
                schema_version="dev.generate_code/1.0.0",
                diff_identity_hash=p.diff_identity_hash,
                structural_agreement_score=1.0,
                decision="approved",
                gate_result=fake_gate_result,
            )
            result = verify_replay(record, p)
            assert result.matched is False
            assert "mutated" in result.mismatch_reason.lower() or "mismatch" in result.mismatch_reason.lower()

    def test_replay_detects_gate_decision_flip(self):
        """If gate outcome flips (pass→fail or fail→pass), return mismatch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="ok.py", operation="create", content="x = 1\n")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            real_result = gate.run(p)
            assert real_result.passed is True

            # Store a record that claims the gate *failed*
            env = SandboxEnvironment.capture()
            fake_failed_result = CodeGateResult(
                passed=False,
                sandbox_env=env,
                failed_stage="sandbox",
                failure_reason="fabricated failure",
                post_apply_workspace_hash="",
            )
            record = ReplayRecord(
                run_id="r3",
                prompt_hash="i" * 64,
                schema_version="dev.generate_code/1.0.0",
                diff_identity_hash=p.diff_identity_hash,
                structural_agreement_score=1.0,
                decision="rejected",
                gate_result=fake_failed_result,
            )
            result = verify_replay(record, p)
            assert result.matched is False
            assert "flipped" in result.mismatch_reason.lower()

    def test_environment_changed_flag_present_in_result(self):
        """verify_replay always populates environment_changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="ok.py", operation="create", content="pass\n")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            gate_result = gate.run(p)
            record = ReplayRecord(
                run_id="r4",
                prompt_hash="j" * 64,
                schema_version="dev.generate_code/1.0.0",
                diff_identity_hash=p.diff_identity_hash,
                structural_agreement_score=1.0,
                decision="approved",
                gate_result=gate_result,
            )
            result = verify_replay(record, p)
            # environment_changed is a bool — must be set regardless of match outcome
            assert isinstance(result.environment_changed, bool)
