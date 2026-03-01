"""Tests for orchestration/code_proposal.py and orchestration/workspace_guard.py.

Covers: size limits, workspace boundary, path escape (including symlink bypass
simulation), invalid operations, and diff_identity_hash stability.
"""

import os
import tempfile
import pytest

from orchestration.code_proposal import (
    DiffEntry, CodeDiffProposal,
    MAX_DIFF_ENTRIES, MAX_DIFF_TOTAL_BYTES, MAX_FILE_PATH_LENGTH,
    VALID_OPERATIONS, _compute_diff_identity_hash,
)
from orchestration.workspace_guard import assert_within_workspace, validate_all_paths
from orchestration.errors import CodeProposalInvalidError, PathEscapeError


FAKE_HASH = "a" * 64


def _make_proposal(entries, workspace=None):
    if workspace is None:
        workspace = tempfile.gettempdir()
    return CodeDiffProposal(
        diff_entries=tuple(entries),
        workspace_root=workspace,
        proposal_hash=FAKE_HASH,
    )


class TestDiffEntry:
    def test_valid_operations_accepted(self):
        for op in VALID_OPERATIONS:
            e = DiffEntry(file_path="foo.py", operation=op, content="")
            assert e.operation == op

    def test_invalid_operation_raises(self):
        with pytest.raises(CodeProposalInvalidError, match="Invalid operation"):
            DiffEntry(file_path="foo.py", operation="overwrite")

    def test_file_path_too_long_raises(self):
        long_path = "a" * (MAX_FILE_PATH_LENGTH + 1)
        with pytest.raises(CodeProposalInvalidError, match="MAX_FILE_PATH_LENGTH"):
            DiffEntry(file_path=long_path, operation="create")

    def test_to_dict_has_sorted_keys(self):
        e = DiffEntry(file_path="main.py", operation="modify", content="print()")
        d = e.to_dict()
        keys = list(d.keys())
        assert keys == sorted(keys), "to_dict() keys must be alphabetically sorted"


class TestCodeDiffProposalSizeLimits:
    def test_too_many_entries_raises(self):
        entries = [DiffEntry(file_path=f"f{i}.py", operation="create") for i in range(MAX_DIFF_ENTRIES + 1)]
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(CodeProposalInvalidError, match="Too many diff entries"):
                _make_proposal(entries, tmpdir)

    def test_exactly_max_entries_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path=f"f{i}.py", operation="create") for i in range(MAX_DIFF_ENTRIES)]
            p = _make_proposal(entries, tmpdir)
            assert len(p.diff_entries) == MAX_DIFF_ENTRIES

    def test_total_bytes_exceeded_raises(self):
        big_content = "x" * (MAX_DIFF_TOTAL_BYTES + 1)
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="big.txt", operation="create", content=big_content)]
            with pytest.raises(CodeProposalInvalidError, match="too large"):
                _make_proposal(entries, tmpdir)

    def test_exactly_max_bytes_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = "x" * MAX_DIFF_TOTAL_BYTES
            entries = [DiffEntry(file_path="exact.txt", operation="create", content=content)]
            p = _make_proposal(entries, tmpdir)
            assert p.diff_entries[0].content == content


class TestWorkspaceGuard:
    def test_path_within_workspace_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert_within_workspace("src/main.py", tmpdir)  # no exception

    def test_path_traversal_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(PathEscapeError):
                assert_within_workspace("../../etc/passwd", tmpdir)

    def test_absolute_path_outside_workspace_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(PathEscapeError):
                assert_within_workspace("/etc/passwd", tmpdir)

    def test_symlink_traversal_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a symlink inside tmpdir that points outside
            link_path = os.path.join(tmpdir, "escape_link")
            os.symlink("/tmp", link_path)
            with pytest.raises(PathEscapeError):
                assert_within_workspace("escape_link/malicious.py", tmpdir)

    def test_validate_all_paths_stops_at_first_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                DiffEntry(file_path="ok.py", operation="create"),
                DiffEntry(file_path="../../evil.py", operation="create"),
                DiffEntry(file_path="also_ok.py", operation="create"),
            ]
            with pytest.raises(PathEscapeError):
                validate_all_paths(entries, tmpdir)

    def test_path_escape_in_proposal_construction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="../../etc/passwd", operation="create")]
            with pytest.raises(PathEscapeError):
                _make_proposal(entries, tmpdir)


class TestDiffIdentityHash:
    def test_hash_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="a.py", operation="create", content="x")]
            h1 = _make_proposal(entries, tmpdir).diff_identity_hash
            h2 = _make_proposal(entries, tmpdir).diff_identity_hash
            assert h1 == h2

    def test_entry_order_does_not_change_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            e1 = DiffEntry(file_path="a.py", operation="create")
            e2 = DiffEntry(file_path="b.py", operation="modify")
            h1 = _make_proposal([e1, e2], tmpdir).diff_identity_hash
            h2 = _make_proposal([e2, e1], tmpdir).diff_identity_hash
            assert h1 == h2, "Entry order must not affect diff_identity_hash"

    def test_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            e1 = DiffEntry(file_path="a.py", operation="create", content="foo")
            e2 = DiffEntry(file_path="a.py", operation="create", content="bar")
            h1 = _make_proposal([e1], tmpdir).diff_identity_hash
            h2 = _make_proposal([e2], tmpdir).diff_identity_hash
            assert h1 != h2

    def test_workspace_root_is_realpath_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [DiffEntry(file_path="x.py", operation="create")]
            p = _make_proposal(entries, tmpdir)
            assert p.workspace_root == os.path.realpath(tmpdir)
