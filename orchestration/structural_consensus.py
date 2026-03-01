"""Structural consensus for Layer 2 dev.generate_code proposals.

Layer 2 Invariant: For action proposals (intent_type == "action", specifically
dev.generate_code), embedding-based cosine similarity consensus is BYPASSED.
Structural agreement on (file_path, operation) pairs is used instead.

Rationale (from gap analysis):
- Embeddings do not understand AST equivalence or control-flow equivalence.
- Two semantically identical diffs may embed very differently.
- For code changes, (file, operation) structural agreement is a stronger and
  cheaper signal than cosine similarity on embedded text.

Minimum viable implementation: Jaccard similarity over (file_path, operation) pairs.
Future extension: AST-aware structural comparison.

SIMILARITY_EPSILON from consensus.py is NOT used here — structural agreement
is a count-based ratio [0.0, 1.0] with no floating-point threshold noise.
"""

from typing import List, Set, Tuple

from orchestration.code_proposal import CodeDiffProposal


# Minimum structural agreement score required for structural consensus.
# This is separate from the embedding-based consensus_threshold in ConsensusEngine.
STRUCTURAL_CONSENSUS_THRESHOLD: float = 0.80


def _entry_set(proposal: CodeDiffProposal) -> Set[Tuple[str, str]]:
    """Extract the (file_path, operation) set from a proposal."""
    return {(e.file_path, e.operation) for e in proposal.diff_entries}


def structural_agreement_score(proposals: List[CodeDiffProposal]) -> float:
    """Compute Jaccard similarity over (file_path, operation) pairs across all proposals.

    Jaccard = |intersection of all sets| / |union of all sets|

    Interpretation:
    - 1.0: all proposals touch exactly the same files with exactly the same operations
    - 0.0: no common (file, operation) pair across proposals
    - Empty proposals list: returns 0.0

    Args:
        proposals: List of CodeDiffProposal objects

    Returns:
        Jaccard similarity score in [0.0, 1.0]
    """
    if not proposals:
        return 0.0

    if len(proposals) == 1:
        return 1.0

    sets = [_entry_set(p) for p in proposals]

    intersection = sets[0]
    for s in sets[1:]:
        intersection = intersection & s

    union = sets[0]
    for s in sets[1:]:
        union = union | s

    if not union:
        # All proposals have empty diff_entries — no agreement possible
        return 0.0

    return len(intersection) / len(union)


def check_structural_consensus(
    proposals: List[CodeDiffProposal],
    threshold: float = STRUCTURAL_CONSENSUS_THRESHOLD,
) -> bool:
    """Return True if structural agreement score meets the threshold.

    Layer 2 Invariant: This function replaces check_consensus() from
    ConsensusEngine for proposals with intent_type == "action".

    Args:
        proposals: List of CodeDiffProposal objects
        threshold: Minimum Jaccard score for consensus (default: 0.80)

    Returns:
        True if structural_agreement_score(proposals) >= threshold
    """
    return structural_agreement_score(proposals) >= threshold


def select_structural_winner(proposals: List[CodeDiffProposal]) -> CodeDiffProposal:
    """Select the winning proposal from a structurally-agreeing set.

    Tie-breaking: lexicographic by diff_identity_hash (not proposal_hash),
    maintaining the same determinism guarantee as ConsensusEngine.select_proposal()
    but using the structural identity hash.

    Args:
        proposals: Non-empty list of CodeDiffProposal objects

    Returns:
        The proposal with the lexicographically smallest diff_identity_hash
    """
    if not proposals:
        raise ValueError("Cannot select winner from empty proposal list")

    return min(proposals, key=lambda p: p.diff_identity_hash)
