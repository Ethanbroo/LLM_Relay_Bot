# telegram_bot/pipeline_adapter.py
"""
Bridges the async Telegram bot layer to the synchronous relay pipeline.

This is the single most important integration file in Phase 1. Without it,
the bot can classify messages and present keyboards but cannot execute a
single build.

Design constraints:
  1. relay_orchestrator.py is synchronous. It blocks for 3-10 minutes.
  2. PTB's event loop is async. Blocking it freezes the entire bot.
  3. Progress updates must flow FROM the pipeline TO Telegram in real-time.
  4. Pause/cancel signals must flow FROM Telegram TO the pipeline.
  5. Only one build can run at a time (single-user system).

Solution: Run the orchestrator in a background thread via asyncio.to_thread().
Use a shared PipelineState object for bidirectional communication.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class PipelineSignal(Enum):
    """Signals sent from Telegram to the running pipeline."""

    NONE = auto()
    PAUSE = auto()
    RESUME = auto()
    CANCEL = auto()
    SKIP_PHASE = auto()


@dataclass
class PipelineRequest:
    """Everything the orchestrator needs to start a build.

    This dataclass is the contract between the Telegram layer and the
    relay pipeline. Every field is populated by handle_anchor_decision
    from context.user_data before the pipeline starts.
    """

    user_message: str
    intent: str                    # "NEW_BUILD" or "EDIT_FIX"
    semantic_anchor: str           # Approved anchor paragraph
    critical_answers: dict         # {question_index: answer_text}
    session_id: str                # UUID for this build session
    workspace_path: Path           # /workspace/{session_id}
    max_turns: int = 50
    model: str = "claude-sonnet-4-5-20250929"
    project_name: Optional[str] = None      # For EDIT_FIX: which project
    resume_session_id: Optional[str] = None  # For EDIT_FIX: prior session


@dataclass
class PipelineState:
    """Shared mutable state for bidirectional communication.

    The pipeline thread writes to the progress fields.
    The event loop writes to the signal field.
    Both sides read both sets of fields.

    Thread safety: Individual field writes are atomic in CPython due to
    the GIL. We don't need a lock for simple field assignments. If we
    later need compound updates (read-modify-write), add a threading.Lock.
    """

    # --- Written by pipeline thread, read by event loop ---
    current_phase: str = "Initializing"
    phase_number: int = 0
    total_phases: int = 0
    turn_count: int = 0
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    last_agent_message: str = ""
    is_complete: bool = False
    is_failed: bool = False
    error_message: str = ""
    result: Optional[dict] = None

    # --- Written by event loop, read by pipeline thread ---
    signal: PipelineSignal = PipelineSignal.NONE
    is_paused: bool = False

    # --- Timing ---
    start_time: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time == 0.0:
            return 0.0
        return time.monotonic() - self.start_time


class PipelineAdapter:
    """Runs the relay pipeline in a background thread.

    Usage from handle_anchor_decision:

        adapter = PipelineAdapter(orchestrator, reporter)
        request = PipelineRequest(...)
        await adapter.start(request)
        # adapter.state is now being updated by the background thread
        # ProgressReporter reads adapter.state and updates Telegram

    Usage for sending signals:

        await adapter.send_signal(PipelineSignal.PAUSE)
        await adapter.send_signal(PipelineSignal.CANCEL)

    Detecting completion:

        result = await adapter.wait()
        # or check adapter.is_running in a polling loop
    """

    def __init__(self, orchestrator, reporter, on_complete=None, on_failure=None):
        """
        Args:
            orchestrator: The relay orchestrator instance. Must have a run()
                method or be adapted via _run_pipeline_sync (see note below).
            reporter: A ProgressReporter instance for Telegram updates.
            on_complete: Optional async callback(result: dict) called when
                the pipeline finishes successfully.
            on_failure: Optional async callback(error: str) called when
                the pipeline fails.
        """
        self._orchestrator = orchestrator
        self._reporter = reporter
        self._on_complete = on_complete
        self._on_failure = on_failure
        self._task: Optional[asyncio.Task] = None
        self._update_task: Optional[asyncio.Task] = None
        self.state = PipelineState()

    async def start(self, request: PipelineRequest) -> None:
        """Launch the pipeline in a background thread. Returns immediately."""
        self.state.start_time = time.monotonic()
        self._update_task = asyncio.create_task(self._progress_loop())
        self._task = asyncio.create_task(self._run_and_handle(request))

    async def _run_and_handle(self, request: PipelineRequest) -> None:
        """Run the pipeline in a thread, then trigger completion callbacks.

        This is the wrapper that connects the background thread to the
        async completion/failure handlers. Without it, the pipeline
        finishes silently and no delivery message is ever sent.
        """
        # Run the synchronous pipeline in a background thread
        await asyncio.to_thread(self._run_pipeline_sync, request)

        # Stop the progress update loop
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        # Trigger the appropriate callback
        if self.state.is_complete and self._on_complete:
            try:
                await self._on_complete(self.state.result)
            except Exception as e:
                logger.error("on_complete callback failed: %s", e, exc_info=True)
        elif self.state.is_failed and self._on_failure:
            try:
                await self._on_failure(self.state.error_message)
            except Exception as e:
                logger.error("on_failure callback failed: %s", e, exc_info=True)

    def _run_pipeline_sync(self, request: PipelineRequest) -> None:
        """Runs in a background thread. Blocks for the build duration.

        IMPORTANT: This method must NOT call any async functions or touch
        the event loop. All communication happens through self.state.

        ORCHESTRATOR INTERFACE NOTE: The existing relay_orchestrator.py
        does not have a run() method matching this signature. There are
        two approaches:

        Option A (Phase 1 — mock): Use a MockOrchestrator for testing
        the Telegram layer end-to-end before Phase 3 wires the real
        pipeline. This is what the code below supports.

        Option B (Phase 3 — real): Build a thin translation layer inside
        this method that converts PipelineRequest into whatever the real
        orchestrator accepts, wraps stdout-based progress into the
        progress_callback, and provides the agent_invoke_fn. Phase 3
        will implement this when wiring Claude Code headless mode.
        """
        try:

            def progress_callback(event: dict) -> Optional[str]:
                """Called by the orchestrator at progress events.

                Reads new state from the orchestrator and checks for
                control signals from the Telegram event loop.

                Returns:
                    None to continue, "cancel" to abort, "skip" to
                    skip the current phase.
                """
                if "phase" in event:
                    self.state.current_phase = event["phase"]
                if "phase_number" in event:
                    self.state.phase_number = event["phase_number"]
                if "total_phases" in event:
                    self.state.total_phases = event["total_phases"]
                if "turn_count" in event:
                    self.state.turn_count = event["turn_count"]
                if "files_created" in event:
                    self.state.files_created = event["files_created"]
                if "files_modified" in event:
                    self.state.files_modified = event["files_modified"]
                if "cost_usd" in event:
                    self.state.cost_usd = event["cost_usd"]
                if "message" in event:
                    self.state.last_agent_message = event["message"][:200]

                # Check for signals from the event loop
                signal = self.state.signal
                if signal == PipelineSignal.CANCEL:
                    return "cancel"
                elif signal == PipelineSignal.PAUSE:
                    self.state.is_paused = True
                    # Block the pipeline thread while paused
                    while self.state.signal == PipelineSignal.PAUSE:
                        time.sleep(0.5)
                    if self.state.signal == PipelineSignal.CANCEL:
                        return "cancel"
                    self.state.is_paused = False
                    self.state.signal = PipelineSignal.NONE
                elif signal == PipelineSignal.SKIP_PHASE:
                    self.state.signal = PipelineSignal.NONE
                    return "skip"
                return None

            # Execute the pipeline. See ORCHESTRATOR INTERFACE NOTE above.
            result = self._orchestrator.run(
                request=request,
                progress_callback=progress_callback,
            )

            self.state.result = result
            self.state.is_complete = True

        except Exception as e:
            logger.error("Pipeline failed: %s", e, exc_info=True)
            self.state.is_failed = True
            self.state.error_message = str(e)

    async def _progress_loop(self) -> None:
        """Async loop that syncs pipeline state to the Telegram reporter.

        Runs every 2 seconds. Reads state from the background thread
        and pushes it to the ProgressReporter, which formats it and
        edits the Telegram progress message.
        """
        try:
            while not self.state.is_complete and not self.state.is_failed:
                self._reporter.progress.current_phase = self.state.current_phase
                self._reporter.progress.phase_number = self.state.phase_number
                self._reporter.progress.total_phases = self.state.total_phases
                self._reporter.progress.turn_count = self.state.turn_count
                self._reporter.progress.files_created = self.state.files_created
                self._reporter.progress.files_modified = self.state.files_modified
                self._reporter.progress.cost_usd = self.state.cost_usd
                self._reporter.progress.last_agent_message = (
                    self.state.last_agent_message
                )
                self._reporter.progress.is_paused = self.state.is_paused
                self._reporter.progress.elapsed_seconds = self.state.elapsed_seconds
                await self._reporter.update()
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass

    async def send_signal(self, signal: PipelineSignal) -> None:
        """Send a control signal to the running pipeline."""
        self.state.signal = signal

    async def wait(self) -> Optional[dict]:
        """Wait for pipeline completion and return the result."""
        if self._task:
            await self._task
        return self.state.result

    @property
    def is_running(self) -> bool:
        return (
            self._task is not None
            and not self._task.done()
            and not self.state.is_complete
            and not self.state.is_failed
        )
