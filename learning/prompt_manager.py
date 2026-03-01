"""Prompt versioning and automatic rollback for the blog generation prompt.

How it works
------------
- The current active prompt is stored in data/prompt_state.json
- Every 7 days (TRIAL_DAYS), Claude is asked to suggest a small tweak to the
  current prompt based on recent performance data (top keywords, reward trend).
- The tweaked prompt runs for TRIAL_DAYS.
- After TRIAL_DAYS the bandit's reward_history is checked:
    * If the 7-day moving average IMPROVED by at least IMPROVEMENT_THRESHOLD
      → promote the candidate → it becomes the new stable prompt
    * Otherwise → rollback to the previous stable prompt + log reason
- A full version history is kept (up to HISTORY_CAP entries) so you can
  always see what changed and why.

State file: data/prompt_state.json
"""

import json
import os
import logging
import datetime
from pathlib import Path
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

PROMPT_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "prompt_state.json"

# How many days a candidate prompt runs before evaluation
TRIAL_DAYS = 7

# Minimum lift in average reward to promote a candidate (absolute, in [0,1])
IMPROVEMENT_THRESHOLD = 0.02

# How many recent rewards to compare (pre-trial vs trial window)
EVAL_WINDOW = 7

# Max prompt history entries to keep
HISTORY_CAP = 20

# The original baseline prompt — never auto-modified, always rollback target
BASELINE_PROMPT = """Write a blog post using NARRATIVE STORYTELLING (NOT prescriptive advice).

Input: Topic: "{topic}"
Audience: Adults seeking genuine human connections (lonely, burned out, seeking community).
{prompt_knobs}
{learning_context}

CRITICAL: Use STORY-DRIVEN VOICE (like Vice, The Outline, personal essay):
- Open with a SPECIFIC MOMENT (person, place, feeling) — NOT a statistic
- Use narrative arc (tension → insight → path forward)
- Show through scenes/dialogue, don't just tell
- Avoid prescriptive tone: "you should", "it's important to", "simply", "just"
- Embrace: "Here's what happened", "Nobody talks about", "Turns out"

Examples of GOOD narrative hooks:
- "Sarah's therapist asked her to name three friends. She stared at the ceiling for 47 seconds."
- "The last time Mike had a genuine conversation was three weeks ago. He remembers because it made him uncomfortable."
- "Nobody warned Emma that making friends after 30 would feel like dating, except more awkward."

Sarcasm/Humor Rules (dry, not mean):
- ~20% increase: subtle, observational (NOT aggressive or cynical)
- Example: "Therapists call it 'social atrophy.' Your bank account calls it 'Friday night savings.'"
- Use dark humor that acknowledges pain (NOT toxic positivity)

Structure Rules:
1. Hook: Specific scene (50-100 words)
2. Tension: Show the cost/stakes through story
3. Insight: Psychological mechanism (HOW it works, not just WHAT)
4. Path: Actionable gesture (not bossy prescription)
5. Close: Question or observation (not "reach out" platitude)

Content Constraints:
- Dashes: Do NOT use em-dashes except for numeric ranges
- NO listicles ("5 ways to...")
- NO generic pain points ("many people struggle")
- Cite mechanisms when making claims (avoid "studies show" without explanation)
- When referencing research, explain the MECHANISM (e.g. "cortisol drops when..." not just "studies show connection helps")
- 800-1200 words, HTML: <p>, <h2>, <ul>, <li>

Category (REQUIRED): "Learning" or "Lifestyle"
Summary (REQUIRED): ONE sentence, clickbait-style, ending with "..."

Return ONLY valid JSON (properly escape all quotes/apostrophes inside strings):
{{
  "title": "Provocative/narrative title (50-100 chars, NOT how-to format)",
  "category": "Lifestyle",
  "summary": "One sentence ending with ...",
  "content": "Full HTML body (narrative structure, specific scenes) — use straight quotes only, avoid smart quotes/apostrophes",
  "tags": ["tag1", "tag2", "tag3"],
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}

CRITICAL: Use only straight ASCII quotes/apostrophes. Escape them in dialogue: She said \\"hello\\" not She said "hello"."""


def _load_prompt_state() -> dict:
    if PROMPT_STATE_PATH.exists():
        try:
            return json.loads(PROMPT_STATE_PATH.read_text())
        except Exception:
            pass
    return _fresh_prompt_state()


def _fresh_prompt_state() -> dict:
    return {
        "active_prompt": BASELINE_PROMPT,
        "stable_prompt": BASELINE_PROMPT,
        "candidate_prompt": None,
        "candidate_started_at": None,
        "last_tweak_at": None,
        # Snapshot of reward_history length at the time candidate was activated
        "candidate_baseline_reward_idx": 0,
        "version": 1,
        "history": [
            {
                "version": 1,
                "prompt": BASELINE_PROMPT,
                "activated_at": datetime.datetime.utcnow().isoformat(),
                "reason": "baseline",
                "avg_reward": None,
            }
        ],
    }


def _save_prompt_state(state: dict) -> None:
    PROMPT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_STATE_PATH.write_text(json.dumps(state, indent=2))


class PromptManager:
    """Manages blog generation prompt versioning and rollback."""

    def __init__(self) -> None:
        self._state = _load_prompt_state()

    def get_active_prompt(self) -> str:
        """Return the currently active blog generation prompt."""
        return self._state["active_prompt"]

    def maybe_propose_tweak(self, reward_history: list, keyword_scores: dict) -> bool:
        """Propose a prompt tweak if TRIAL_DAYS have elapsed since last tweak.

        Returns True if a new candidate was proposed and activated.
        """
        last_tweak_str = self._state.get("last_tweak_at")
        if last_tweak_str:
            last_tweak = datetime.datetime.fromisoformat(last_tweak_str)
            days_since = (datetime.datetime.utcnow() - last_tweak).days
            if days_since < TRIAL_DAYS:
                return False

        # Don't propose if there's already a candidate running
        if self._state.get("candidate_prompt") is not None:
            return False

        # Need enough reward history to propose meaningfully
        if len(reward_history) < EVAL_WINDOW:
            logger.info("PromptManager: not enough reward history yet (%d < %d)",
                        len(reward_history), EVAL_WINDOW)
            return False

        candidate = self._generate_candidate(reward_history, keyword_scores)
        if candidate is None:
            return False

        # Activate candidate
        self._state["candidate_prompt"] = candidate
        self._state["candidate_started_at"] = datetime.datetime.utcnow().isoformat()
        self._state["candidate_baseline_reward_idx"] = len(reward_history)
        self._state["active_prompt"] = candidate
        self._state["last_tweak_at"] = datetime.datetime.utcnow().isoformat()
        _save_prompt_state(self._state)

        logger.info("PromptManager: activated candidate prompt (version %d+1)",
                    self._state["version"])
        return True

    def maybe_evaluate_candidate(self, reward_history: list) -> Optional[bool]:
        """Evaluate candidate after TRIAL_DAYS. Returns True=promoted, False=rolled back, None=not due.

        Call this from the analytics poll job (safe to call frequently — checks elapsed time).
        """
        candidate = self._state.get("candidate_prompt")
        if candidate is None:
            return None  # No active candidate

        started_str = self._state.get("candidate_started_at")
        if not started_str:
            return None

        started = datetime.datetime.fromisoformat(started_str)
        days_elapsed = (datetime.datetime.utcnow() - started).days
        if days_elapsed < TRIAL_DAYS:
            return None  # Trial not complete yet

        # Evaluate: compare pre-trial avg reward vs trial avg reward
        baseline_idx = self._state.get("candidate_baseline_reward_idx", 0)
        pre_trial = reward_history[max(0, baseline_idx - EVAL_WINDOW):baseline_idx]
        trial = reward_history[baseline_idx:baseline_idx + EVAL_WINDOW]

        if not pre_trial or not trial:
            logger.info("PromptManager: insufficient rewards for evaluation — keeping candidate")
            return None

        pre_avg = sum(pre_trial) / len(pre_trial)
        trial_avg = sum(trial) / len(trial)
        lift = trial_avg - pre_avg

        logger.info(
            "PromptManager: evaluation — pre_avg=%.4f trial_avg=%.4f lift=%.4f threshold=%.4f",
            pre_avg, trial_avg, lift, IMPROVEMENT_THRESHOLD,
        )

        if lift >= IMPROVEMENT_THRESHOLD:
            # Promote
            self._promote_candidate(candidate, trial_avg, lift)
            return True
        else:
            # Rollback
            self._rollback(pre_avg, trial_avg, lift)
            return False

    # ── Private helpers ───────────────────────────────────────────────────────

    def _generate_candidate(self, reward_history: list, keyword_scores: dict) -> Optional[str]:
        """Ask Claude to suggest a small improvement to the current prompt."""
        api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("PromptManager: no API key — cannot generate candidate")
            return None

        recent_avg = sum(reward_history[-EVAL_WINDOW:]) / EVAL_WINDOW
        top_kw = sorted(keyword_scores, key=lambda k: keyword_scores[k], reverse=True)[:5]

        meta_prompt = f"""You are a prompt engineer helping improve a blog generation prompt.

Current prompt:
---
{self._state['stable_prompt']}
---

Recent performance (avg reward last {EVAL_WINDOW} posts): {recent_avg:.3f} (scale 0-1, higher is better)
Top-performing keywords: {', '.join(top_kw) if top_kw else 'none yet'}

Make ONE small, targeted improvement to this prompt that might improve reader engagement and SEO.
- Do NOT change the JSON output schema or field names
- Keep the placeholders {{topic}}, {{learning_context}}, {{prompt_knobs}} exactly as shown
- Keep changes minimal — one sentence or one instruction added/changed
- Return ONLY the complete updated prompt text with no explanation or markdown fencing"""

        try:
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1024,
                temperature=0.7,  # slight creativity for prompt tweaks
                messages=[{"role": "user", "content": meta_prompt}],
            )
            candidate = message.content[0].text.strip()
            # Sanity check: must still contain the required JSON keys
            required = ['"title"', '"content"', '"excerpt"', '"tags"', '"keywords"']
            if all(k in candidate for k in required):
                return candidate
            else:
                logger.warning("PromptManager: candidate missing required JSON keys — discarding")
                return None
        except Exception as e:
            logger.warning("PromptManager: failed to generate candidate: %s", e)
            return None

    def _promote_candidate(self, candidate: str, trial_avg: float, lift: float) -> None:
        """Promote the candidate to become the new stable prompt."""
        self._state["version"] += 1
        self._state["stable_prompt"] = candidate
        self._state["active_prompt"] = candidate
        self._state["candidate_prompt"] = None
        self._state["candidate_started_at"] = None

        entry = {
            "version": self._state["version"],
            "prompt": candidate,
            "activated_at": datetime.datetime.utcnow().isoformat(),
            "reason": f"promoted: lift={lift:+.4f} trial_avg={trial_avg:.4f}",
            "avg_reward": trial_avg,
        }
        history = self._state.setdefault("history", [])
        history.append(entry)
        if len(history) > HISTORY_CAP:
            self._state["history"] = history[-HISTORY_CAP:]

        _save_prompt_state(self._state)
        logger.info(
            "PromptManager: PROMOTED candidate to v%d (lift=%.4f)",
            self._state["version"], lift,
        )

    def _rollback(self, pre_avg: float, trial_avg: float, lift: float) -> None:
        """Roll back to the stable prompt and log why."""
        stable = self._state["stable_prompt"]
        self._state["active_prompt"] = stable
        self._state["candidate_prompt"] = None
        self._state["candidate_started_at"] = None

        entry = {
            "version": self._state["version"],
            "prompt": f"[ROLLBACK to v{self._state['version']}]",
            "activated_at": datetime.datetime.utcnow().isoformat(),
            "reason": (
                f"rolled back: lift={lift:+.4f} "
                f"pre_avg={pre_avg:.4f} trial_avg={trial_avg:.4f} — below threshold"
            ),
            "avg_reward": trial_avg,
        }
        history = self._state.setdefault("history", [])
        history.append(entry)
        if len(history) > HISTORY_CAP:
            self._state["history"] = history[-HISTORY_CAP:]

        _save_prompt_state(self._state)
        logger.info(
            "PromptManager: ROLLED BACK to stable v%d (lift=%.4f < threshold %.4f)",
            self._state["version"], lift, IMPROVEMENT_THRESHOLD,
        )

    def status(self) -> dict:
        """Return a summary dict for logging / CLI display."""
        return {
            "version": self._state["version"],
            "has_candidate": self._state.get("candidate_prompt") is not None,
            "candidate_started_at": self._state.get("candidate_started_at"),
            "last_tweak_at": self._state.get("last_tweak_at"),
            "history_count": len(self._state.get("history", [])),
        }
