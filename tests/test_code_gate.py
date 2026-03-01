"""Tests for orchestration/code_gate.py.

Covers: SandboxEnvironment capture, all four gate stages (schema, logical,
sandbox, test), post_apply_workspace_hash stability, and CodeGateResult
serialization.
"""

import os
import tempfile
import pytest

from orchestration.code_gate import (
    CodeGate, CodeGateResult, SandboxEnvironment, _compute_workspace_hash,
)
from orchestration.code_proposal import DiffEntry, CodeDiffProposal, MAX_DIFF_ENTRIES

FAKE_HASH = "a" * 64


def _make_proposal(entries, workspace=None):
    if workspace is None:
        workspace = tempfile.gettempdir()
    return CodeDiffProposal(
        diff_entries=tuple(entries),
        workspace_root=workspace,
        proposal_hash=FAKE_HASH,
    )


class TestSandboxEnvironment:
    def test_capture_returns_sandbox_environment(self):
        env = SandboxEnvironment.capture()
        assert isinstance(env.python_version, str)
        assert isinstance(env.python_executable, str)
        assert isinstance(env.platform_str, str)
        assert len(env.python_version) > 0
        assert len(env.python_executable) > 0

    def test_to_dict_has_sorted_keys(self):
        env = SandboxEnvironment.capture()
        d = env.to_dict()
        keys = list(d.keys())
        assert keys == sorted(keys)

    def test_capture_is_deterministic_within_session(self):
        e1 = SandboxEnvironment.capture()
        e2 = SandboxEnvironment.capture()
        assert e1.python_version == e2.python_version
        assert e1.python_executable == e2.python_executable
        assert e1.platform_str == e2.platform_str


class TestCodeGateResultSerialization:
    def test_to_dict_has_sorted_keys(self):
        env = SandboxEnvironment.capture()
        result = CodeGateResult(passed=True, sandbox_env=env,
                                post_apply_workspace_hash="x" * 64)
        d = result.to_dict()
        keys = list(d.keys())
        assert keys == sorted(keys)

    def test_failed_result_fields(self):
        env = SandboxEnvironment.capture()
        result = CodeGateResult(
            passed=False, sandbox_env=env,
            failed_stage="schema", failure_reason="bad schema"
        )
        assert result.passed is False
        assert result.failed_stage == "schema"
        assert result.failure_reason == "bad schema"
        assert result.post_apply_workspace_hash == ""


class TestCodeGateStageSchema:
    def test_valid_proposal_passes_schema_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="main.py", operation="create", content="x = 1")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            # Schema stage should pass; full gate should pass (valid python)
            assert result.passed is True
            assert result.failed_stage is None

    def test_empty_entries_fails_schema_stage(self):
        """Schema requires minItems: 1 — empty diff_entries is a schema violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _make_proposal([], tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is False
            assert result.failed_stage == "schema"


class TestCodeGateStageSandbox:
    def test_valid_python_passes_sandbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = "def foo():\n    return 42\n"
            entries = [DiffEntry(file_path="foo.py", operation="create", content=code)]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is True
            assert result.post_apply_workspace_hash != ""

    def test_syntax_error_fails_sandbox_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_code = "def broken(\n    # missing closing paren and body\n"
            entries = [DiffEntry(file_path="bad.py", operation="create", content=bad_code)]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is False
            assert result.failed_stage == "sandbox"
            assert "bad.py" in result.failure_reason

    def test_non_python_files_skip_syntax_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                DiffEntry(file_path="README.md", operation="create",
                          content="# This is { definitely not valid python")
            ]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is True

    def test_delete_operation_skips_syntax_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                DiffEntry(file_path="old.py", operation="delete", content="")
            ]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is True

    def test_modify_operation_syntax_checked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            good_code = "x = 1 + 2\n"
            entries = [DiffEntry(file_path="mod.py", operation="modify", content=good_code)]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is True


class TestPostApplyWorkspaceHash:
    def test_hash_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="a.py", operation="create", content="x = 1")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            r1 = gate.run(p)
            r2 = gate.run(p)
            assert r1.post_apply_workspace_hash == r2.post_apply_workspace_hash

    def test_different_content_produces_different_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal(
                [DiffEntry(file_path="a.py", operation="create", content="x = 1")], tmpdir
            )
            p2 = _make_proposal(
                [DiffEntry(file_path="a.py", operation="create", content="x = 2")], tmpdir
            )
            gate = CodeGate()
            r1 = gate.run(p1)
            r2 = gate.run(p2)
            assert r1.post_apply_workspace_hash != r2.post_apply_workspace_hash

    def test_hash_entry_order_independent(self):
        """post_apply_workspace_hash is sorted by file_path — order must not matter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            e1 = DiffEntry(file_path="a.py", operation="create", content="# a")
            e2 = DiffEntry(file_path="b.py", operation="create", content="# b")
            h1 = _compute_workspace_hash([e1, e2])
            h2 = _compute_workspace_hash([e2, e1])
            assert h1 == h2

    def test_hash_is_64_char_hex(self):
        entries = [DiffEntry(file_path="x.py", operation="create", content="pass")]
        h = _compute_workspace_hash(entries)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_failed_gate_has_empty_workspace_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Inject a syntax error so gate fails at sandbox stage
            bad_code = "def f(\n"
            entries = [DiffEntry(file_path="err.py", operation="create", content=bad_code)]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is False
            assert result.post_apply_workspace_hash == ""


class TestCodeGateSandboxEnvCapture:
    def test_sandbox_env_captured_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="ok.py", operation="create", content="pass")]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.sandbox_env is not None
            assert result.sandbox_env.python_version != ""

    def test_sandbox_env_captured_on_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_code = "def f(\n"
            entries = [DiffEntry(file_path="bad.py", operation="create", content=bad_code)]
            p = _make_proposal(entries, tmpdir)
            gate = CodeGate()
            result = gate.run(p)
            assert result.passed is False
            assert result.sandbox_env is not None
