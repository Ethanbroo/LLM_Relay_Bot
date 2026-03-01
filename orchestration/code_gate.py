"""Four-stage execution gate for Layer 2 dev.generate_code proposals.

Layer 2 Invariants:
- Stages execute in order: schema → logical → sandbox → test
- Fail-fast: first failing stage halts evaluation, later stages are skipped
- Gate *failure* (proposal rejected) → CodeGateResult.passed == False
- Gate *error* (unexpected internal failure) → CodeGateError raised
- Sandbox syntax check uses sys.executable (pinned to current interpreter)
- SandboxEnvironment captures python_version + executable for replay determinism

Intent safety is explicitly OUT OF SCOPE. A structurally valid, gated diff may
still contain malicious content. This gate verifies execution mechanics only.
"""

import os
import sys
import json
import hashlib
import platform
import tempfile
import subprocess
from dataclasses import dataclass, field
from typing import Optional, List

import jsonschema

from orchestration.code_proposal import CodeDiffProposal, DiffEntry
from orchestration.workspace_guard import validate_all_paths
from orchestration.errors import CodeGateError, CodeProposalInvalidError, PathEscapeError
from orchestration.canonical import canonical_hash


# Path to the JSON schema for dev.generate_code payloads
_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "schemas", "actions", "dev.generate_code", "1.0.0.schema.json"
)


@dataclass(frozen=True)
class SandboxEnvironment:
    """Captured execution environment for replay determinism.

    Invariant: All fields are captured at gate execution time, not at import.
    This means re-running the gate in a different Python version will produce
    a different SandboxEnvironment, which replay verification uses to detect
    environment drift (not tampering).
    """
    python_version: str
    python_executable: str
    platform_str: str

    def to_dict(self) -> dict:
        return {
            "platform": self.platform_str,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
        }

    @classmethod
    def capture(cls) -> "SandboxEnvironment":
        return cls(
            python_version=sys.version,
            python_executable=sys.executable,
            platform_str=platform.platform(),
        )


@dataclass(frozen=True)
class CodeGateResult:
    """Result of running the four-stage code gate.

    Fields:
        passed: True iff all stages passed
        failed_stage: Name of the stage that rejected the proposal, or None
        failure_reason: Human-readable reason for rejection, or None
        sandbox_env: Environment captured during gate execution
        post_apply_workspace_hash: SHA-256 of sorted (file_path, content_hash)
            pairs after applying the diff in the sandbox. Empty string if gate
            failed before the sandbox stage.
    """
    passed: bool
    sandbox_env: SandboxEnvironment
    failed_stage: Optional[str] = field(default=None)
    failure_reason: Optional[str] = field(default=None)
    post_apply_workspace_hash: str = field(default="")

    def to_dict(self) -> dict:
        return {
            "failed_stage": self.failed_stage,
            "failure_reason": self.failure_reason,
            "passed": self.passed,
            "post_apply_workspace_hash": self.post_apply_workspace_hash,
            "sandbox_env": self.sandbox_env.to_dict(),
        }


class CodeGate:
    """Four-stage execution gate for CodeDiffProposal objects.

    Stage order (fail-fast, cheapest first):
    1. schema    — validate payload against dev.generate_code/1.0.0.schema.json
    2. logical   — workspace boundary re-check + size limits (defense in depth)
    3. sandbox   — py_compile syntax check in subprocess with pinned interpreter
    4. test      — pytest in sandbox (stub: skipped when no tests exist)
    """

    def __init__(self) -> None:
        self._schema = self._load_schema()

    def _load_schema(self) -> dict:
        schema_path = os.path.normpath(_SCHEMA_PATH)
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise CodeGateError("schema_load", str(e))

    def run(self, proposal: CodeDiffProposal) -> CodeGateResult:
        """Run all four stages against the proposal.

        Args:
            proposal: A CodeDiffProposal that has already passed construction
                      validation (size limits, workspace guard).

        Returns:
            CodeGateResult with passed=True iff all stages pass.

        Raises:
            CodeGateError: On unexpected internal failures (not gate rejections).
        """
        env = SandboxEnvironment.capture()

        # Stage 1: Schema
        result = self._stage_schema(proposal)
        if result is not None:
            return CodeGateResult(passed=False, sandbox_env=env,
                                  failed_stage="schema", failure_reason=result)

        # Stage 2: Logical validity (defense-in-depth re-check)
        result = self._stage_logical(proposal)
        if result is not None:
            return CodeGateResult(passed=False, sandbox_env=env,
                                  failed_stage="logical", failure_reason=result)

        # Stage 3: Sandbox syntax check + workspace hash
        # _stage_sandbox returns (None, hash) on success or (error_str, "") on failure
        sandbox_error, post_apply_hash = self._stage_sandbox(proposal)
        if sandbox_error is not None:
            return CodeGateResult(passed=False, sandbox_env=env,
                                  failed_stage="sandbox", failure_reason=sandbox_error)

        # Stage 4: Test gate (stub — passes if no test runner configured)
        result = self._stage_test(proposal)
        if result is not None:
            return CodeGateResult(passed=False, sandbox_env=env,
                                  failed_stage="test", failure_reason=result,
                                  post_apply_workspace_hash=post_apply_hash)

        return CodeGateResult(passed=True, sandbox_env=env,
                              post_apply_workspace_hash=post_apply_hash)

    def _stage_schema(self, proposal: CodeDiffProposal) -> Optional[str]:
        """Stage 1: Validate proposal.to_dict() against JSON schema.

        Returns None on success, error string on failure.
        """
        try:
            jsonschema.validate(proposal.to_dict(), self._schema)
            return None
        except jsonschema.ValidationError as e:
            return f"JSON schema validation failed: {e.message}"

    def _stage_logical(self, proposal: CodeDiffProposal) -> Optional[str]:
        """Stage 2: Logical validity — workspace boundary re-check.

        Returns None on success, error string on failure.
        """
        try:
            validate_all_paths(proposal.diff_entries, proposal.workspace_root)
            return None
        except PathEscapeError as e:
            return str(e)
        except CodeProposalInvalidError as e:
            return str(e)

    def _stage_sandbox(self, proposal: CodeDiffProposal):
        """Stage 3: Apply diff to temp sandbox, run py_compile on .py files.

        Returns (None, post_apply_workspace_hash) on success, or (error_str, "") on failure.
        Uses sys.executable (pinned interpreter) for py_compile subprocess.
        """
        with tempfile.TemporaryDirectory(prefix="llm_relay_gate_") as tmpdir:
            # Apply the diff inside the temp sandbox
            for entry in proposal.diff_entries:
                target = os.path.join(tmpdir, entry.file_path.lstrip("/"))
                os.makedirs(os.path.dirname(target) if os.path.dirname(target) else tmpdir,
                            exist_ok=True)

                if entry.operation in ("create", "modify"):
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(entry.content)
                elif entry.operation == "delete":
                    # Mark as deleted — just don't create it
                    pass

            # Syntax-check all .py files using the pinned interpreter
            for entry in proposal.diff_entries:
                if entry.operation == "delete":
                    continue
                if not entry.file_path.endswith(".py"):
                    continue
                target = os.path.join(tmpdir, entry.file_path.lstrip("/"))
                if not os.path.exists(target):
                    continue
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "py_compile", target],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode != 0:
                        return f"Syntax error in {entry.file_path}: {result.stderr.strip()}", ""
                except subprocess.TimeoutExpired:
                    return f"py_compile timed out for {entry.file_path}", ""
                except OSError as e:
                    raise CodeGateError("sandbox", f"subprocess failed: {e}")

            # Compute post-apply workspace hash
            return None, _compute_workspace_hash(proposal.diff_entries)

    def _stage_test(self, proposal: CodeDiffProposal) -> Optional[str]:
        """Stage 4: Test gate (stub — always passes in Layer 2).

        In a real implementation, this would run pytest in the sandbox against
        the diff's test files. Stubbed to always pass here.
        Returns None on success, error string on failure.
        """
        return None


def _compute_workspace_hash(entries) -> str:
    """Compute SHA-256 over sorted (file_path, content_hash) pairs.

    This hash detects invisible behavioral drift between runs with nominally
    identical diffs. Sorted by file_path for determinism.
    """
    pairs = sorted(
        [
            {
                "content_hash": hashlib.sha256(e.content.encode("utf-8")).hexdigest(),
                "file_path": e.file_path,
                "operation": e.operation,
            }
            for e in entries
        ],
        key=lambda d: d["file_path"]
    )
    return canonical_hash({"workspace_state": pairs})
