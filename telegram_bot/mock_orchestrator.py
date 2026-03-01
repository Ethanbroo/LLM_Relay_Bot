# telegram_bot/mock_orchestrator.py
"""
Mock orchestrator for Phase 1 testing.

Simulates a 30-second build with progress updates, producing a fake
result that exercises every part of the delivery flow. Replace with
the real orchestrator adapter in Phase 3.

Usage:
    orchestrator = MockOrchestrator()
    # Pass to PipelineAdapter as the orchestrator argument
"""

import time
import logging
from typing import Callable, Optional

from telegram_bot.pipeline_adapter_legacy import PipelineRequest

logger = logging.getLogger(__name__)

# Simulated phases with durations (seconds)
MOCK_PHASES = [
    ("Intent Clarification", 2),
    ("Research", 5),
    ("Architecture", 5),
    ("Code Generation", 12),
    ("Code Review", 4),
    ("Documentation", 2),
]


class MockOrchestrator:
    """Simulates the relay pipeline for Telegram layer testing.

    Walks through fake phases, sends progress events, respects
    cancel/skip signals, and returns a mock result dict that matches
    the shape the delivery handler expects.
    """

    def run(
        self,
        request: PipelineRequest,
        progress_callback: Callable[[dict], Optional[str]],
    ) -> dict:
        """Simulate a pipeline run. Blocks for ~30 seconds."""

        total_phases = len(MOCK_PHASES)
        total_turns = 0
        files_created = []
        cost = 0.0

        for phase_idx, (phase_name, duration) in enumerate(MOCK_PHASES, start=1):
            # Send phase-start event
            signal = progress_callback(
                {
                    "phase": phase_name,
                    "phase_number": phase_idx,
                    "total_phases": total_phases,
                    "message": f"Starting {phase_name}...",
                }
            )
            if signal == "cancel":
                logger.info("Mock pipeline cancelled at phase %s", phase_name)
                return {"status": "cancelled", "phase": phase_name}
            if signal == "skip":
                logger.info("Mock pipeline skipping phase %s", phase_name)
                continue

            # Simulate work within the phase
            steps = max(1, duration // 2)
            for step in range(steps):
                time.sleep(2)
                total_turns += 3
                cost += 0.002

                # Simulate file creation during Code Generation
                if phase_name == "Code Generation" and step < 4:
                    files_created.append(f"src/file_{len(files_created) + 1}.py")

                signal = progress_callback(
                    {
                        "turn_count": total_turns,
                        "files_created": list(files_created),
                        "cost_usd": cost,
                        "message": f"{phase_name}: step {step + 1}/{steps}",
                    }
                )
                if signal == "cancel":
                    return {"status": "cancelled", "phase": phase_name}
                if signal == "skip":
                    break

        # Return a result dict that matches what delivery handlers expect
        return {
            "status": "success",
            "session_id": request.session_id,
            "semantic_anchor": request.semantic_anchor,
            "files_created": files_created,
            "files_modified": [],
            "total_turns": total_turns,
            "total_cost_usd": cost,
            "test_results": {
                "passed": 14,
                "failed": 0,
                "coverage_pct": 84.2,
            },
            "lint_results": {
                "errors": 0,
                "warnings": 0,
            },
            "security_results": {
                "vulnerabilities": 0,
            },
            "git_branch": f"feature/{request.session_id[:8]}",
            "github_pr_url": None,  # No real PR in mock mode
            "documentation_path": f"{request.workspace_path}/README.md",
        }
