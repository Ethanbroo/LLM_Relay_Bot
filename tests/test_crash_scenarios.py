"""Crash scenario tests — Layer 1.

Verifies that the orchestration components degrade gracefully under simulated
crash conditions (IO errors, corrupt state, abrupt exceptions mid-execution).

Crash injection strategy:
- Patch at the highest boundary visible to the layer under test
  (e.g., builtins.open for file IO, Path.write_text for state persistence)
- Every mock that injects a crash asserts it was actually called (.called)
  so we detect if the code path changes and the patch silently stops firing
- Tests are self-contained; they do not depend on production data files

Scenarios:
1. Corrupt JSON on q-learner state load → fresh state returned, no crash
2. Mid-write IOError on q-learner state save → exception propagates (not silenced)
3. ResponseParser: malformed section → LLMResponseInvalidError, no partial state
4. Mid-orchestration crash in scoring (compute_embedding) → exception propagates
"""

import hashlib
import json
import tempfile
import os
import pytest
from unittest.mock import patch, MagicMock, call


# ── Scenario 1: Corrupt state file on q-learner load ────────────────────────

class TestQLearnerCorruptStateLoad:
    """BanditLearner must fall back to fresh state when the JSON file is corrupt."""

    def test_corrupt_json_produces_fresh_state(self, tmp_path):
        """Corrupt JSON → _fresh_state() is used, no unhandled exception."""
        state_file = tmp_path / "q_state.json"
        state_file.write_text("{ INVALID JSON !!!")

        # Patch STATE_PATH to point at our corrupt file
        import learning.q_learner as q_mod
        original_path = q_mod.STATE_PATH
        try:
            q_mod.STATE_PATH = state_file
            from learning.q_learner import BanditLearner
            learner = BanditLearner()
            # Must have fresh state: empty posteriors, episode=0
            assert learner._state.get("posteriors") == {}
            assert learner._state.get("episode") == 0
        finally:
            q_mod.STATE_PATH = original_path

    def test_missing_state_file_produces_fresh_state(self, tmp_path):
        """Non-existent state file → _fresh_state() is used, no exception."""
        import learning.q_learner as q_mod
        original_path = q_mod.STATE_PATH
        try:
            q_mod.STATE_PATH = tmp_path / "nonexistent.json"
            from learning.q_learner import BanditLearner
            learner = BanditLearner()
            assert learner._state["posteriors"] == {}
        finally:
            q_mod.STATE_PATH = original_path


# ── Scenario 2: IOError on q-learner state write ────────────────────────────

class TestQLearnerStateWriteFailure:
    """IOError during _save_state must propagate, not be silently swallowed."""

    def test_ioerror_on_save_propagates(self, tmp_path):
        """If Path.write_text raises IOError, the exception must bubble up."""
        import learning.q_learner as q_mod
        original_path = q_mod.STATE_PATH
        try:
            q_mod.STATE_PATH = tmp_path / "q_state.json"
            from learning.q_learner import BanditLearner
            learner = BanditLearner()

            error_raised = False
            with patch.object(
                q_mod.STATE_PATH.__class__, "write_text",
                side_effect=IOError("disk full")
            ) as mock_write:
                try:
                    q_mod._save_state(learner._state)
                except IOError:
                    error_raised = True
                assert mock_write.called, "write_text patch must have been triggered"

            assert error_raised, "IOError from write_text must propagate, not be swallowed"
        finally:
            q_mod.STATE_PATH = original_path


# ── Scenario 3: ResponseParser malformed sections ────────────────────────────

class TestResponseParserCrashIsolation:
    """Malformed LLM responses must raise LLMResponseInvalidError immediately."""

    def _make_valid_response(self) -> str:
        return (
            "PROPOSAL:\nImplement feature using modular architecture\n"
            "RATIONALE:\nEnsures maintainability\n"
            "CONFIDENCE:\n0.85"
        )

    def test_missing_proposal_section_raises(self):
        from orchestration.response_parser import ResponseParser
        from orchestration.errors import LLMResponseInvalidError
        parser = ResponseParser()
        bad = "RATIONALE:\nSome rationale\nCONFIDENCE:\n0.5"
        with pytest.raises(LLMResponseInvalidError, match="Missing section: PROPOSAL"):
            parser.parse_response("claude", bad)

    def test_missing_rationale_section_raises(self):
        from orchestration.response_parser import ResponseParser
        from orchestration.errors import LLMResponseInvalidError
        parser = ResponseParser()
        bad = "PROPOSAL:\nSome proposal\nCONFIDENCE:\n0.5"
        with pytest.raises(LLMResponseInvalidError, match="Missing section: RATIONALE"):
            parser.parse_response("claude", bad)

    def test_confidence_out_of_range_raises(self):
        from orchestration.response_parser import ResponseParser
        from orchestration.errors import LLMResponseInvalidError
        parser = ResponseParser()
        bad = "PROPOSAL:\nSome proposal\nRATIONALE:\nSome rationale\nCONFIDENCE:\n1.5"
        with pytest.raises(LLMResponseInvalidError, match="out of range"):
            parser.parse_response("claude", bad)

    def test_confidence_not_float_raises(self):
        from orchestration.response_parser import ResponseParser
        from orchestration.errors import LLMResponseInvalidError
        parser = ResponseParser()
        bad = "PROPOSAL:\nSome proposal\nRATIONALE:\nSome rationale\nCONFIDENCE:\nnot_a_float"
        with pytest.raises(LLMResponseInvalidError, match="not a valid float"):
            parser.parse_response("claude", bad)

    def test_valid_response_parses_cleanly(self):
        from orchestration.response_parser import ResponseParser
        parser = ResponseParser()
        proposal = parser.parse_response("claude", self._make_valid_response())
        assert proposal.model == "claude"
        assert proposal.confidence == 0.85
        assert proposal.intent_type == "analysis"  # default
        assert proposal.is_state_changing is False  # default


# ── Scenario 4: Mid-scoring crash in compute_embedding ───────────────────────

class TestScoringCrashIsolation:
    """RuntimeError during embedding must propagate, not return a NaN matrix."""

    def test_embedding_exception_propagates(self):
        """If compute_embedding raises, compute_pairwise_similarities must not swallow it."""
        from orchestration.scoring import SimilarityScorer

        scorer = SimilarityScorer()
        scorer._load_model()  # Prime the stub

        with patch.object(
            scorer, "compute_embedding",
            side_effect=RuntimeError("GPU OOM")
        ) as mock_embed:
            with pytest.raises(RuntimeError, match="GPU OOM"):
                scorer.compute_pairwise_similarities(["text a", "text b"])
            assert mock_embed.called, "compute_embedding patch must have been triggered"

    def test_similarity_returns_symmetric_matrix(self):
        """Sanity: stub produces a symmetric matrix for 3 proposals."""
        import numpy as np
        from orchestration.scoring import SimilarityScorer

        scorer = SimilarityScorer()
        proposals = [
            "Alpha: Implement feature using modular architecture",
            "Beta: Build solution with emphasis on performance optimization",
            "Gamma: Design system with focus on scalability",
        ]
        matrix = scorer.compute_pairwise_similarities(proposals)
        assert matrix.shape == (3, 3)
        # Diagonal must be 1.0
        for i in range(3):
            assert matrix[i, i] == 1.0
        # Symmetry
        for i in range(3):
            for j in range(3):
                assert matrix[i, j] == matrix[j, i], f"Matrix not symmetric at ({i},{j})"
