"""Stochastic chaos tests — Layer 1.

Verify that the core deterministic invariants hold across repeated runs and
under simulated noise (Unicode variants, whitespace drift, boundary-value
floats).

These tests are tagged @pytest.mark.stochastic so they can be excluded from
standard CI if desired:

    pytest -m "not stochastic"

Run them explicitly with:

    pytest -m stochastic tests/test_stochastic_chaos.py -v
"""

import hashlib
import math
import pytest

from orchestration.response_parser import (
    LLMProposal,
    ResponseParser,
    normalize_text,
    VALID_INTENT_TYPES,
)
from orchestration.scoring import SimilarityScorer
from orchestration.consensus import ConsensusEngine, SIMILARITY_EPSILON


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_proposal(
    proposal_text: str = "Implement feature using modular architecture",
    rationale_text: str = "Ensures maintainability",
    confidence: float = 0.85,
    model: str = "claude",
    intent_type: str = "analysis",
    is_state_changing: bool = False,
    purpose: str = "",
) -> LLMProposal:
    """Test factory — builds an LLMProposal without calling ResponseParser."""
    normalized = normalize_text(proposal_text)
    proposal_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return LLMProposal(
        model=model,
        proposal_text=normalized,
        rationale_text=normalize_text(rationale_text),
        confidence=confidence,
        proposal_hash=proposal_hash,
        intent_type=intent_type,
        is_state_changing=is_state_changing,
        purpose=purpose,
    )


# ── Test 1: Hash stability across Unicode variants ────────────────────────────

@pytest.mark.stochastic
class TestHashStability:
    """Proposal hash must be identical for semantically identical text."""

    def test_nfc_variants_produce_same_hash(self):
        """NFC-normalized variants of the same string hash identically."""
        import unicodedata
        base = "caf\u00e9"            # NFC: é as single code point
        composed = unicodedata.normalize("NFC", base)
        decomposed = unicodedata.normalize("NFD", base)  # é as two code points

        hash_nfc = hashlib.sha256(normalize_text(composed).encode("utf-8")).hexdigest()
        hash_nfd = hashlib.sha256(normalize_text(decomposed).encode("utf-8")).hexdigest()

        assert hash_nfc == hash_nfd, (
            "NFC normalization must collapse NFD variant before hashing"
        )

    def test_trailing_whitespace_stripped(self):
        """Trailing whitespace is stripped by normalize_text."""
        text_a = "Build solution"
        text_b = "Build solution   \t\n"
        assert normalize_text(text_a) == normalize_text(text_b)

    def test_hash_is_deterministic_across_30_calls(self):
        """Calling _make_proposal 30 times with the same args → same hash each time."""
        hashes = {_make_proposal().proposal_hash for _ in range(30)}
        assert len(hashes) == 1, "Hash must be deterministic; got multiple values"


# ── Test 2: Consensus stability under boundary values ─────────────────────────

@pytest.mark.stochastic
class TestConsensusStability:
    """Consensus decisions must be stable at and around the threshold boundary."""

    def setup_method(self):
        self.engine = ConsensusEngine(consensus_threshold=0.80)
        self.scorer = SimilarityScorer()

    def _sim_matrix(self, sim: float):
        import numpy as np
        n = 2
        m = np.zeros((n, n))
        m[0, 0] = 1.0
        m[1, 1] = 1.0
        m[0, 1] = sim
        m[1, 0] = sim
        return m

    def test_above_threshold_always_passes(self):
        """Similarities clearly above threshold always produce consensus."""
        proposals = [_make_proposal(), _make_proposal(proposal_text="Different text here")]
        for sim in [0.82, 0.90, 0.99, 1.0]:
            assert self.engine.check_consensus(proposals, self._sim_matrix(sim)), (
                f"Expected consensus at sim={sim}"
            )

    def test_below_epsilon_of_threshold_passes_due_to_epsilon(self):
        """Similarity within SIMILARITY_EPSILON below threshold still passes."""
        proposals = [_make_proposal(), _make_proposal(proposal_text="Different text here")]
        # Exactly at threshold - epsilon/2 should pass (within epsilon guard)
        borderline = 0.80 - SIMILARITY_EPSILON / 2
        assert self.engine.check_consensus(proposals, self._sim_matrix(borderline)), (
            f"Similarity {borderline} is within SIMILARITY_EPSILON of 0.80 — "
            f"should pass due to epsilon guard"
        )

    def test_clearly_below_threshold_fails(self):
        """Similarity well below threshold correctly fails."""
        proposals = [_make_proposal(), _make_proposal(proposal_text="Different text here")]
        low = 0.80 - SIMILARITY_EPSILON * 3
        assert not self.engine.check_consensus(proposals, self._sim_matrix(low)), (
            f"Similarity {low} should NOT produce consensus"
        )

    def test_tie_breaking_is_deterministic(self):
        """Two equally-similar proposals must always resolve to the same winner."""
        import numpy as np
        p1 = _make_proposal("Alpha proposal text for tie-break test")
        p2 = _make_proposal("Beta proposal text for tie-break test")
        proposals = [p1, p2]
        # Perfect tie: both have average similarity 1.0 to themselves and same
        # cross-similarity
        sim = 0.95
        matrix = np.array([[1.0, sim], [sim, 1.0]])

        winners = set()
        for _ in range(20):
            selected, _ = self.engine.select_proposal(proposals, matrix)
            winners.add(selected.proposal_hash)

        assert len(winners) == 1, (
            "Tie-breaking must be deterministic — got multiple winners across 20 runs"
        )


# ── Test 3: Escalation correctness under noisy input ─────────────────────────

@pytest.mark.stochastic
class TestEscalationCorrectness:
    """Escalation decisions are rule-based and must not vary under input noise."""

    def test_empty_proposals_always_escalates(self):
        from orchestration.escalation import should_escalate, EscalationReason
        for _ in range(10):
            escalate, reason = should_escalate(
                proposals=[],
                consensus_exists=False,
                max_similarity=0.0,
                consensus_threshold=0.80,
            )
            assert escalate is True
            assert reason == EscalationReason.ALL_INVALID

    def test_consensus_exists_never_escalates(self):
        from orchestration.escalation import should_escalate
        proposals = [_make_proposal()]
        for _ in range(10):
            escalate, reason = should_escalate(
                proposals=proposals,
                consensus_exists=True,
                max_similarity=0.95,
                consensus_threshold=0.80,
            )
            assert escalate is False
            assert reason is None

    def test_low_similarity_escalation_reason(self):
        from orchestration.escalation import should_escalate, EscalationReason
        proposals = [_make_proposal(), _make_proposal("Other text")]
        for _ in range(10):
            escalate, reason = should_escalate(
                proposals=proposals,
                consensus_exists=False,
                max_similarity=0.40,
                consensus_threshold=0.80,
            )
            assert escalate is True
            assert reason == EscalationReason.LOW_SIMILARITY


# ── Test 4: Intent metadata validation ───────────────────────────────────────

@pytest.mark.stochastic
class TestIntentMetadata:
    """Intent type validation: hallucinated types must reset to safe defaults."""

    def test_valid_intent_types_accepted(self):
        for intent in VALID_INTENT_TYPES:
            p = _make_proposal(intent_type=intent)
            assert p.intent_type == intent

    def test_invalid_intent_type_resets_to_analysis(self):
        """Hallucinated intent type silently resets to 'analysis'."""
        for bad_type in ["hack", "ANALYSIS", "execute", "", "action_override"]:
            p = _make_proposal(intent_type=bad_type)
            assert p.intent_type == "analysis", (
                f"Bad intent_type {bad_type!r} should reset to 'analysis', "
                f"got {p.intent_type!r}"
            )
            assert p.is_state_changing is False

    def test_action_intent_with_is_state_changing(self):
        p = _make_proposal(intent_type="action", is_state_changing=True)
        assert p.intent_type == "action"
        assert p.is_state_changing is True

    def test_to_dict_includes_intent_fields(self):
        p = _make_proposal(intent_type="action", is_state_changing=True, purpose="deploy")
        d = p.to_dict()
        assert d["intent_type"] == "action"
        assert d["is_state_changing"] is True
        assert d["purpose"] == "deploy"
