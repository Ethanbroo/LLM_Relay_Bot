"""Tests for orchestration/structural_consensus.py.

Covers: Jaccard score computation, consensus threshold check, structural
tie-breaking determinism, edge cases (empty sets, single proposal, identical
proposals, no overlap).
"""

import tempfile
import pytest

from orchestration.code_proposal import DiffEntry, CodeDiffProposal
from orchestration.structural_consensus import (
    structural_agreement_score,
    check_structural_consensus,
    select_structural_winner,
    STRUCTURAL_CONSENSUS_THRESHOLD,
)


FAKE_HASH = "a" * 64


def _make_proposal(file_ops, workspace=None):
    """Build a CodeDiffProposal from a list of (file_path, operation) tuples."""
    if workspace is None:
        workspace = tempfile.gettempdir()
    entries = [DiffEntry(file_path=fp, operation=op) for fp, op in file_ops]
    return CodeDiffProposal(
        diff_entries=tuple(entries),
        workspace_root=workspace,
        proposal_hash=FAKE_HASH,
    )


class TestStructuralAgreementScore:
    def test_empty_list_returns_zero(self):
        assert structural_agreement_score([]) == 0.0

    def test_single_proposal_returns_one(self):
        p = _make_proposal([("a.py", "create")])
        assert structural_agreement_score([p]) == 1.0

    def test_identical_proposals_score_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            p2 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            assert structural_agreement_score([p1, p2]) == 1.0

    def test_no_overlap_score_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create")], tmpdir)
            p2 = _make_proposal([("b.py", "modify")], tmpdir)
            assert structural_agreement_score([p1, p2]) == 0.0

    def test_partial_overlap_jaccard(self):
        """intersection=1, union=3 → 1/3."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            p2 = _make_proposal([("a.py", "create"), ("c.py", "delete")], tmpdir)
            score = structural_agreement_score([p1, p2])
            assert abs(score - 1 / 3) < 1e-9

    def test_all_empty_entries_score_zero(self):
        """All proposals with no entries → union is empty → 0.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([], tmpdir)
            p2 = _make_proposal([], tmpdir)
            assert structural_agreement_score([p1, p2]) == 0.0

    def test_score_is_commutative(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            p2 = _make_proposal([("a.py", "create"), ("c.py", "delete")], tmpdir)
            assert structural_agreement_score([p1, p2]) == structural_agreement_score([p2, p1])

    def test_three_proposals_full_agreement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [("a.py", "create"), ("b.py", "modify")]
            p1 = _make_proposal(entries, tmpdir)
            p2 = _make_proposal(entries, tmpdir)
            p3 = _make_proposal(entries, tmpdir)
            assert structural_agreement_score([p1, p2, p3]) == 1.0

    def test_three_proposals_one_diverges(self):
        """With 3 proposals where only 2 agree, intersection shrinks to shared items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            p2 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            p3 = _make_proposal([("a.py", "create"), ("c.py", "delete")], tmpdir)
            # intersection = {(a, create)}, union = {(a, create), (b, modify), (c, delete)}
            score = structural_agreement_score([p1, p2, p3])
            assert abs(score - 1 / 3) < 1e-9


class TestCheckStructuralConsensus:
    def test_above_threshold_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [("a.py", "create"), ("b.py", "modify")]
            p1 = _make_proposal(entries, tmpdir)
            p2 = _make_proposal(entries, tmpdir)
            assert check_structural_consensus([p1, p2]) is True

    def test_below_threshold_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create")], tmpdir)
            p2 = _make_proposal([("b.py", "modify")], tmpdir)
            assert check_structural_consensus([p1, p2]) is False

    def test_custom_threshold_respected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create"), ("b.py", "modify")], tmpdir)
            p2 = _make_proposal([("a.py", "create"), ("c.py", "delete")], tmpdir)
            # score = 1/3 ≈ 0.333
            assert check_structural_consensus([p1, p2], threshold=0.30) is True
            assert check_structural_consensus([p1, p2], threshold=0.40) is False

    def test_default_threshold_constant(self):
        assert STRUCTURAL_CONSENSUS_THRESHOLD == 0.80

    def test_single_proposal_always_passes(self):
        p = _make_proposal([("a.py", "create")])
        assert check_structural_consensus([p]) is True


class TestSelectStructuralWinner:
    def test_raises_on_empty_list(self):
        with pytest.raises(ValueError):
            select_structural_winner([])

    def test_single_proposal_wins(self):
        p = _make_proposal([("a.py", "create")])
        assert select_structural_winner([p]) is p

    def test_winner_is_lex_smallest_diff_identity_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Build two proposals with distinct diff_identity_hash values
            p1 = _make_proposal([("a.py", "create")], tmpdir)
            p2 = _make_proposal([("z.py", "modify")], tmpdir)
            winner = select_structural_winner([p1, p2])
            expected = min([p1, p2], key=lambda p: p.diff_identity_hash)
            assert winner is expected

    def test_winner_is_deterministic_across_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _make_proposal([("a.py", "create")], tmpdir)
            p2 = _make_proposal([("b.py", "modify")], tmpdir)
            p3 = _make_proposal([("c.py", "delete")], tmpdir)
            results = {
                select_structural_winner([p1, p2, p3]).diff_identity_hash
                for _ in range(20)
            }
            assert len(results) == 1, "Winner selection must be deterministic"
