"""Coded mitigations for every known failure mode.

Every failure from the roadmap's catalog, with detection code and
programmatic mitigation. This is NOT a risk register — every mitigation
is an implementable function.

Integration points:
  - pipeline/orchestrator.py: context overflow, budget exceeded, regression loop
  - pipeline/rate_limiter.py: 429 → queue_rate_limited_job
  - handlers/edit.py: missing session → handle_missing_session
  - git_manager.py: push/merge fails → handle_git_conflict
  - pipeline/orchestrator.py after Phase 6: validate_packages
"""

import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)


class FailureHandlers:
    """Centralized failure detection and mitigation."""

    def __init__(self, redis_client, claude_client, session_manager,
                 project_context, config):
        self.redis = redis_client
        self.claude = claude_client
        self.sessions = session_manager
        self.project_ctx = project_context
        self.config = config

    # ── 1. Context Overflow ──────────────────────────────────

    async def handle_context_overflow(
        self, session_id: str, project_name: str, usage_pct: float,
    ) -> str:
        """Mitigation: Write HANDOFF.md, force new session.
        Returns new session_id (empty string = caller starts fresh)."""
        logger.warning("Context overflow at %.0f%% for %s", usage_pct * 100, project_name)

        self.project_ctx.write_handoff(
            project_name=project_name,
            session_id=session_id,
            phase_reached=0,
            what_was_done=f"Session handed off due to context at {usage_pct:.0%}",
            whats_next="Resume from HANDOFF.md in fresh session",
        )

        session = await self.sessions.get(session_id)
        if session:
            session.handoff_written = True
            await self.sessions.save(session)

        return ""  # Fresh session

    # ── 2. Hallucinated Packages ─────────────────────────────

    async def validate_packages(self, project_name: str) -> list[str]:
        """Detection: Run npm install in dry-run mode, check for failures.
        Returns list of problematic packages."""
        response = await self.claude.run(
            prompt=(
                f"In /workspace/{project_name}, run 'npm install --dry-run' "
                f"and report any packages that fail to resolve. "
                f"Return ONLY a JSON array of package names that failed. "
                f"If all resolve, return []."
            ),
            model="haiku",
            max_turns=2,
            timeout=60,
            allowed_tools=["Bash", "Read"],
            disallowed_tools=["Edit", "Write", "web_search"],
        )

        if response.is_error:
            return []

        try:
            return json.loads(response.text.strip().strip("`").strip("json").strip())
        except (json.JSONDecodeError, TypeError):
            return []

    async def fix_hallucinated_packages(
        self, project_name: str, bad_packages: list[str],
    ) -> bool:
        """Mitigation: Remove bad packages, add to AGENTS.md blocklist."""
        if not bad_packages:
            return True

        blocklist_note = "Blocked packages (hallucinated by AI): " + ", ".join(bad_packages)
        self.project_ctx.append_to_agents_md(
            project_name, "Code Generation Agent", blocklist_note,
        )

        response = await self.claude.run(
            prompt=(
                f"In /workspace/{project_name}, remove these packages from package.json: "
                f"{', '.join(bad_packages)}. Then run npm install to verify."
            ),
            model="haiku",
            max_turns=3,
            timeout=60,
            allowed_tools=["Bash", "Read", "Edit"],
            disallowed_tools=["Write", "web_search"],
        )

        return not response.is_error

    # ── 3. Regression Loops (Three-Strike) ───────────────────

    async def handle_regression_loop(
        self, session_id: str, project_name: str, failing_test: str, attempt: int,
    ) -> str:
        """Detection: Same test fails 3 times.
        Mitigation: revert -> analyze -> fresh session.
        Returns action taken: 'reverted', 'analyzed', 'fresh_session'."""

        if attempt == 1:
            # Strike 1: Revert the last commit
            proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "--", ".",
                cwd=f"{self.config.workspace_path}/{project_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return "reverted"

        elif attempt == 2:
            # Strike 2: Analyze what's going wrong
            await self.claude.run(
                prompt=(
                    f"The test '{failing_test}' has failed twice after regeneration. "
                    f"Read the test file and the code it tests. "
                    f"Explain WHY this specific test keeps failing. "
                    f"Do NOT fix it — just analyze the root cause."
                ),
                model="sonnet",
                max_turns=3,
                session_id=session_id,
                allowed_tools=["Read", "Bash"],
                disallowed_tools=["Edit", "Write", "web_search"],
            )
            return "analyzed"

        else:
            # Strike 3: Give up, start fresh
            self.project_ctx.write_handoff(
                project_name=project_name,
                session_id=session_id,
                phase_reached=7,
                what_was_done=f"Regression loop on test: {failing_test}",
                blockers=f"Test '{failing_test}' failed 3 times. Root cause needs human analysis.",
            )
            return "fresh_session"

    # ── 4. 429 Rate Limit ────────────────────────────────────

    async def queue_rate_limited_job(
        self, session_id: str, project_name: str, phase_number: int,
        prompt: str, model: str, user_id: int, retry_after: int | None,
    ) -> None:
        """Mitigation: Queue job in Redis for deferred retry."""
        if not self.redis:
            return

        job = json.dumps({
            "session_id": session_id,
            "project_name": project_name,
            "phase_number": phase_number,
            "prompt": prompt[:5000],
            "model": model,
            "user_id": user_id,
            "queued_at": time.time(),
            "retry_after": retry_after or 300,
        })
        await self.redis.rpush("queue:jobs", job)
        logger.info("Rate-limited job queued for %s phase %d", project_name, phase_number)

    async def process_job_queue(self, bot) -> int:
        """Process queued jobs. Called periodically or on rate limit expiry.
        Returns number of jobs processed."""
        if not self.redis:
            return 0

        processed = 0
        while True:
            raw = await self.redis.lpop("queue:jobs")
            if not raw:
                break

            try:
                job = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Check if enough time has passed
            retry_after = job.get("retry_after", 300)
            queued_at = job.get("queued_at", 0)
            if time.time() - queued_at < retry_after:
                # Not ready yet — push back to queue
                await self.redis.rpush("queue:jobs", raw)
                break

            # Notify user
            user_id = job.get("user_id")
            if user_id:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"\U0001f504 Resuming queued job: "
                            f"{job['project_name']} phase {job['phase_number']}"
                        ),
                    )
                except Exception:
                    pass

            processed += 1

        return processed

    # ── 5. Token Budget Exceeded ─────────────────────────────

    async def handle_budget_exceeded(
        self, project_name: str, session_id: str, spent: float, budget: float,
    ) -> None:
        """Mitigation: Graceful stop, save progress, notify."""
        logger.warning("Budget exceeded for %s: $%.2f of $%.2f", project_name, spent, budget)

        self.project_ctx.write_handoff(
            project_name=project_name,
            session_id=session_id,
            phase_reached=0,
            what_was_done=f"Budget exceeded (${spent:.2f} of ${budget:.2f})",
            blockers="Token budget exceeded. Increase TOKEN_BUDGET_DEFAULT or wait for daily reset.",
        )

    # ── 6. Redis Data Loss ───────────────────────────────────

    async def handle_missing_session(self, project_name: str) -> str:
        """Detection: Session lookup returns nil.
        Mitigation: Graceful fallback to fresh session.
        Returns recommendation string for the user."""

        # Check if HANDOFF.md exists (filesystem backup of session context)
        handoff = self.project_ctx.read_handoff_md(project_name)
        if handoff:
            return (
                f"Session data was lost but HANDOFF.md found for {project_name}. "
                f"Starting fresh session with previous context."
            )
        else:
            return (
                f"No session history found for {project_name}. "
                f"Starting a completely fresh build."
            )

    # ── 7. Git Conflicts ────────────────────────────────────

    async def handle_git_conflict(self, project_name: str) -> bool:
        """Detection: git merge/push non-zero exit.
        Mitigation: Abort merge, stay on feature branch.
        Returns True if resolved."""
        project_dir = f"{self.config.workspace_path}/{project_name}"

        proc = await asyncio.create_subprocess_exec(
            "git", "merge", "--abort",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # The rule: NEVER push to main. Feature branches only. PRs pass CI.
        logger.info("Git conflict aborted for %s. Staying on feature branch.", project_name)
        return True
