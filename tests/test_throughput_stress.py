"""Throughput stress tests — Layer 1.

Verifies that the orchestration pipeline sustains acceptable throughput under
repeated workloads without memory leaks, excessive escalation, or hash
instability.

Two sub-tests:
- 100-round (always runs in standard CI)
- 1000-round (@pytest.mark.slow — run with: pytest -m slow)

Pass criteria:
- Escalation rate < 10% of total rounds
- Average latency < 500ms per round
- Memory growth < 50MB over the full run
- All produced proposal hashes are correct SHA-256 of their normalized text

Usage:
    pytest tests/test_throughput_stress.py              # 100-round only
    pytest -m slow tests/test_throughput_stress.py      # all
"""

import hashlib
import math
import time
import pytest

from orchestration.response_parser import (
    ResponseParser,
    LLMProposal,
    normalize_text,
)
from orchestration.scoring import SimilarityScorer
from orchestration.consensus import ConsensusEngine
from orchestration.errors import ConsensusFailedError


# ── Shared helpers ────────────────────────────────────────────────────────────

_MODELS = ["chatgpt", "claude", "gemini", "deepseek"]

_STUB_RESPONSES = [
    (
        "PROPOSAL:\nImplement feature using modular architecture with clear separation\n"
        "RATIONALE:\nEnsures maintainability and testability\n"
        "CONFIDENCE:\n0.85"
    ),
    (
        "PROPOSAL:\nBuild solution with emphasis on performance optimization and caching\n"
        "RATIONALE:\nPrioritizes efficiency for production workloads\n"
        "CONFIDENCE:\n0.78"
    ),
    (
        "PROPOSAL:\nDesign system with focus on scalability and fault tolerance\n"
        "RATIONALE:\nProvides resilience and graceful degradation under load\n"
        "CONFIDENCE:\n0.91"
    ),
]


def _run_orchestration_round(parser, scorer, engine, round_idx: int) -> dict:
    """
    Simulate one orchestration round:
    1. Parse 3 model responses
    2. Compute pairwise similarities
    3. Attempt consensus
    4. Return stats dict
    """
    proposals = []
    parse_errors = 0

    for i, response in enumerate(_STUB_RESPONSES):
        model = _MODELS[i % len(_MODELS)]
        try:
            proposal = parser.parse_response(model, response)
            # Verify hash integrity inline
            expected_hash = hashlib.sha256(
                normalize_text(proposal.proposal_text).encode("utf-8")
            ).hexdigest()
            assert proposal.proposal_hash == expected_hash, (
                f"Hash mismatch at round {round_idx} proposal {i}"
            )
            proposals.append(proposal)
        except Exception:
            parse_errors += 1

    escalated = False
    if len(proposals) < 2:
        escalated = True
    else:
        sim_matrix = scorer.compute_pairwise_similarities(
            [p.proposal_text for p in proposals]
        )
        try:
            selected, score = engine.select_proposal(proposals, sim_matrix)
            # Winner's hash must still be valid
            expected = hashlib.sha256(
                normalize_text(selected.proposal_text).encode("utf-8")
            ).hexdigest()
            assert selected.proposal_hash == expected
        except ConsensusFailedError:
            escalated = True

    return {"parse_errors": parse_errors, "escalated": escalated}


def _stress_run(n_rounds: int):
    """Execute n_rounds of orchestration and return aggregate stats."""
    parser = ResponseParser()
    scorer = SimilarityScorer()
    engine = ConsensusEngine(consensus_threshold=0.80)

    try:
        import tracemalloc
        tracemalloc.start()
        has_tracemalloc = True
    except Exception:
        has_tracemalloc = False

    total_escalations = 0
    total_parse_errors = 0
    start = time.monotonic()

    for r in range(n_rounds):
        result = _run_orchestration_round(parser, scorer, engine, r)
        if result["escalated"]:
            total_escalations += 1
        total_parse_errors += result["parse_errors"]

    elapsed = time.monotonic() - start

    memory_growth_mb = 0.0
    if has_tracemalloc:
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory_growth_mb = peak / (1024 * 1024)

    avg_latency_ms = (elapsed / n_rounds) * 1000
    escalation_rate = total_escalations / n_rounds

    return {
        "n_rounds": n_rounds,
        "elapsed_s": elapsed,
        "avg_latency_ms": avg_latency_ms,
        "escalation_rate": escalation_rate,
        "total_escalations": total_escalations,
        "total_parse_errors": total_parse_errors,
        "memory_growth_mb": memory_growth_mb,
    }


# ── 100-round (always runs) ───────────────────────────────────────────────────

class TestThroughput100Round:
    """Standard CI throughput test: 100 orchestration rounds."""

    def test_100_rounds_pass_criteria(self):
        stats = _stress_run(100)

        assert stats["escalation_rate"] < 0.10, (
            f"Escalation rate {stats['escalation_rate']:.1%} exceeds 10% limit "
            f"({stats['total_escalations']}/{stats['n_rounds']} rounds escalated)"
        )

        assert stats["avg_latency_ms"] < 500, (
            f"Average latency {stats['avg_latency_ms']:.1f}ms exceeds 500ms limit"
        )

        assert stats["total_parse_errors"] == 0, (
            f"Parse errors detected: {stats['total_parse_errors']} — "
            f"stub responses must always parse cleanly"
        )

    def test_hash_integrity_preserved_across_100_rounds(self):
        """All proposal hashes must match their normalized proposal_text."""
        parser = ResponseParser()
        for i, response in enumerate(_STUB_RESPONSES * 34):  # ~100 total
            model = _MODELS[i % len(_MODELS)]
            proposal = parser.parse_response(model, response)
            expected = hashlib.sha256(
                normalize_text(proposal.proposal_text).encode("utf-8")
            ).hexdigest()
            assert proposal.proposal_hash == expected, (
                f"Hash integrity failure at iteration {i}"
            )


# ── 1000-round (slow) ─────────────────────────────────────────────────────────

@pytest.mark.slow
class TestThroughput1000Round:
    """Extended throughput test: 1000 rounds. Run with: pytest -m slow."""

    def test_1000_rounds_pass_criteria(self):
        stats = _stress_run(1000)

        assert stats["escalation_rate"] < 0.10, (
            f"Escalation rate {stats['escalation_rate']:.1%} exceeds 10% limit"
        )

        assert stats["avg_latency_ms"] < 500, (
            f"Average latency {stats['avg_latency_ms']:.1f}ms exceeds 500ms limit"
        )

        assert stats["total_parse_errors"] == 0, (
            f"Parse errors: {stats['total_parse_errors']}"
        )

        # Memory guard: stub runs must not leak
        if stats["memory_growth_mb"] > 0:
            assert stats["memory_growth_mb"] < 50, (
                f"Peak memory growth {stats['memory_growth_mb']:.1f}MB exceeds 50MB limit"
            )
