"""Contextual bandit for blog topic and tone selection.

Upgrades over topic-only bandit:
- Contextual arms: (topic, tone) → reward (learns "corporate burnout + reflection")
- Posterior decay: α,β *= 0.98 each update (adapts to non-stationary rewards)
- Multi-objective reward: engagement + comment_rate (emotional resonance proxy)
- Bootstrapped priors: Beta(2,1) when keyword evidence exists

Algorithm: Thompson Sampling with Beta(α, β) per (topic, tone) arm.
- Arm key: "topic|tone" (e.g. "Overcoming loneliness|struggle")
- Decay before update: avoids converging to yesterday's reality
- Selection: sample each eligible arm, pick argmax

Reward: w1*engagement + w2*comment_rate, baseline-normalised.
State persisted to data/q_state.json.
"""

import json
import os
import logging
import random
import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "q_state.json"
HTTP_TIMEOUT = 15

# Thompson Sampling priors
BETA_ALPHA_INIT = 1.0
BETA_BETA_INIT = 1.0
BETA_BOOTSTRAP_ALPHA = 2.0  # When keyword evidence exists
BETA_BOOTSTRAP_BETA = 1.0

# Posterior decay (adapts to non-stationary rewards)
POSTERIOR_DECAY = 0.98

# Reward: engagement + comment_rate (proxy for emotional resonance)
ENGAGEMENT_WEIGHT = 0.6
COMMENT_RATE_WEIGHT = 0.4
COMMENT_RATE_SCALE = 0.05  # comments/views: 0.05 = 1% comment rate → full score

# Reward normalisation thresholds
VIEW_TARGET = 100.0
COMMENT_TARGET = 5.0

# Multi-horizon reward weights (must sum to 1.0)
HORIZON_WEIGHTS = {
    "6h":  0.20,
    "24h": 0.50,
    "72h": 0.30,
}
# Hours to wait before polling each horizon
HORIZON_HOURS = {"6h": 6, "24h": 24, "72h": 72}

# Baseline: rolling window for median normalisation
BASELINE_WINDOW = 14  # days

# Diversity: prevent topic repeat within this many days
DIVERSITY_WINDOW_DAYS = 14

# Keyword/tag score cap
KW_SCORE_CAP = 3.0
KW_ALPHA = 0.2

# Contextual bandit: valid tones
TONES = ("struggle", "aspiration", "conflict", "relief", "belonging", "reflection", "warning")
DEFAULT_TONE = "belonging"

# Rejected posts: apply this reward (0 = strong penalty, shifts posterior down)
REJECTED_REWARD = 0.0


def _arm_key(topic: str, tone: str) -> str:
    """Canonical key for (topic, tone) arm."""
    return f"{topic}|{tone}"


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text())
            # Migrate: old posteriors keyed by topic → topic_posteriors
            if "topic_posteriors" not in state and "posteriors" in state:
                topic_posteriors = {}
                contextual = {}
                for k, v in state["posteriors"].items():
                    if "|" not in k:
                        topic_posteriors[k] = v
                    else:
                        contextual[k] = v
                state["topic_posteriors"] = topic_posteriors
                state["posteriors"] = contextual if contextual else state["posteriors"]
            return state
        except Exception:
            pass
    return _fresh_state()


def _fresh_state() -> dict:
    return {
        # Contextual: per (topic,tone) Beta posterior {arm_key: [alpha, beta]}
        "posteriors": {},
        # Legacy topic-only posteriors (for backward compat with old ledger entries)
        "topic_posteriors": {},
        "posts_ledger": {},
        "reward_history": [],
        "keyword_scores": {},
        "tag_scores": {},
        "episode": 0,
        # Prompt knobs: learned parameters for safe prompt tuning
        "prompt_knobs": {
            "sarcasm_level": 0.20,  # 20% increase (dry, subtle)
            "painpoint_intensity": 0.75,  # 0-1 scale for psychological pain depth
            "audience_specificity": 0.80,  # 0-1 scale for targeting precision
        },
        # Image archetype tracking: {image_id: {tone, description, avg_reward}}
        "image_archetypes": {},
        # Title style tracking: {style: {count, total_reward, avg_reward}}
        "title_styles": {},
    }


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _beta_sample(alpha: float, beta: float) -> float:
    """Draw one sample from Beta(alpha, beta) using the standard library."""
    return random.betavariate(alpha, beta)


def _compute_raw_reward(views: int, comments: int) -> float:
    """Multi-objective: engagement + comment_rate (emotional resonance proxy)."""
    view_score = min(views / VIEW_TARGET, 1.0)
    comment_abs = min(comments / COMMENT_TARGET, 1.0)
    # Comment rate: comments/views as proxy for emotional resonance
    comment_rate = comments / max(views, 1)
    comment_rate_score = min(comment_rate / COMMENT_RATE_SCALE, 1.0)
    return (
        ENGAGEMENT_WEIGHT * (0.6 * view_score + 0.4 * comment_abs)
        + COMMENT_RATE_WEIGHT * comment_rate_score
    )


def _normalise_against_baseline(raw: float, history: list) -> float:
    """Express reward as lift over 14-day rolling median.

    Result is clamped to [0, 1] for Beta posterior compatibility.
    If history is empty, return the raw reward unchanged.
    """
    if not history:
        return raw
    window = history[-BASELINE_WINDOW:]
    median = sorted(window)[len(window) // 2]
    eps = 1e-6
    lift = (raw - median) / (median + eps)
    # Map lift to [0, 1]: lift=0 → 0.5, lift=+1 → ~0.73, lift=-1 → ~0.27
    return max(0.0, min(1.0, 0.5 + lift * 0.25))


def _decay_posterior(alpha: float, beta: float) -> tuple:
    """Decay posterior to adapt to non-stationary rewards."""
    return alpha * POSTERIOR_DECAY, beta * POSTERIOR_DECAY


def _update_beta_posterior(alpha: float, beta: float, reward: float) -> tuple:
    """Bayesian update with prior decay. alpha += reward, beta += (1 - reward)."""
    a, b = _decay_posterior(alpha, beta)
    return a + reward, b + (1.0 - reward)


class BanditLearner:
    """Thompson Sampling bandit for blog topic selection."""

    def __init__(self) -> None:
        self._state = _load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def select_topic(self, topics: list) -> Optional[str]:
        """Select (topic, tone) using contextual Thompson Sampling.

        Returns (topic, tone) or None on cold start. For backward compat,
        returns topic only — use select_topic_and_tone() for full context.
        """
        result = self.select_topic_and_tone(topics)
        return result[0] if result else None

    def select_topic_and_tone(self, topics: list) -> Optional[tuple[str, str]]:
        """Select (topic, tone) using contextual bandit. Returns None on cold start."""
        posteriors = self._state.get("posteriors", {})
        topic_posteriors = self._state.get("topic_posteriors", {})
        ledger = self._state["posts_ledger"]

        # Diversity: no topic reused within DIVERSITY_WINDOW_DAYS (topic-level)
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=DIVERSITY_WINDOW_DAYS)
        recently_used_topics = set()
        for entry in ledger.values():
            created_at = entry.get("created_at", "")
            try:
                ts = datetime.datetime.fromisoformat(created_at)
                if ts > cutoff:
                    recently_used_topics.add(entry.get("topic", ""))
            except (ValueError, TypeError):
                pass

        eligible_topics = [t for t in topics if t not in recently_used_topics]
        if not eligible_topics:
            eligible_topics = topics

        # Build eligible (topic, tone) arms
        eligible_arms = [(t, tone) for t in eligible_topics for tone in TONES]

        # Cold start: prefer topic posteriors if we have them (backward compat)
        if not posteriors and topic_posteriors:
            # Fallback: sample topic only, pick default tone
            scores = {}
            for topic in topics:
                a, b = topic_posteriors.get(topic, [BETA_ALPHA_INIT, BETA_BETA_INIT])
                scores[topic] = _beta_sample(a, b)
            best_topic = max(scores, key=lambda t: scores[t])
            return (best_topic, DEFAULT_TONE)

        if not posteriors and not topic_posteriors:
            return None

        # Thompson Sampling over (topic, tone) arms
        kw_scores = self._state.get("keyword_scores", {})
        has_bootstrap = any(abs(kw_scores.get(k, 0)) > 0.3 for k in list(kw_scores)[:5])

        best_arm = None
        best_score = -1.0
        for topic, tone in eligible_arms:
            key = _arm_key(topic, tone)
            a, b = posteriors.get(key, [
                BETA_BOOTSTRAP_ALPHA if has_bootstrap else BETA_ALPHA_INIT,
                BETA_BOOTSTRAP_BETA if has_bootstrap else BETA_BETA_INIT,
            ])
            s = _beta_sample(a, b)
            if s > best_score:
                best_score = s
                best_arm = (topic, tone)

        return best_arm

    def get_learning_context_hint(self) -> str:
        """Return a paragraph injected into Claude's prompt for better SEO."""
        kw = self._state.get("keyword_scores", {})
        tag = self._state.get("tag_scores", {})

        if not kw and not tag:
            return ""

        top_kw = sorted(kw, key=lambda k: kw[k], reverse=True)[:5]
        top_tag = sorted(tag, key=lambda t: tag[t], reverse=True)[:5]

        lines = ["Performance insights from previous posts (use to improve SEO):"]
        if top_kw:
            lines.append(f"- High-engagement keywords: {', '.join(top_kw)}")
        if top_tag:
            lines.append(f"- High-engagement tags: {', '.join(top_tag)}")
        lines.append("Incorporate these where naturally appropriate.")
        return "\n".join(lines)

    def get_prompt_knobs_hint(self) -> str:
        """Return bandit-learned prompt knobs for injection (sarcasm, painpoint_intensity, etc)."""
        knobs = self._state.get("prompt_knobs", {})
        if not knobs:
            return ""

        # Format knobs as actionable guidance
        sarcasm = knobs.get("sarcasm_level", 0.20)
        painpoint = knobs.get("painpoint_intensity", 0.75)
        audience = knobs.get("audience_specificity", 0.80)

        lines = ["Learned parameters (optimized from engagement data):"]
        lines.append(f"- Sarcasm level: {int(sarcasm * 100)}% (dry, subtle tone)")

        if painpoint >= 0.8:
            lines.append("- Pain point depth: HIGH (specific, visceral psychological struggles)")
        elif painpoint >= 0.5:
            lines.append("- Pain point depth: MEDIUM (relatable emotional challenges)")
        else:
            lines.append("- Pain point depth: LOW (gentle, supportive framing)")

        if audience >= 0.8:
            lines.append("- Audience targeting: PRECISE (speak directly to lonely/burned-out adults)")
        elif audience >= 0.5:
            lines.append("- Audience targeting: MODERATE (relatable to broader audience)")
        else:
            lines.append("- Audience targeting: BROAD (general human connection themes)")

        return "\n".join(lines) + "\n"

    def record_draft_created(
        self,
        topic: str,
        post_id: Optional[str],
        slug: Optional[str],
        keywords: list,
        tags: list,
        tone: Optional[str] = None,
        title: Optional[str] = None,
        image_id: Optional[str] = None,
        image_description: Optional[str] = None,
    ) -> None:
        """Record a new draft. Rewards are filled in by multi-horizon polls."""
        if post_id is None:
            return
        tone = tone or "unknown"
        if tone not in TONES:
            tone = "unknown"
        arm = _arm_key(topic, tone)

        posteriors = self._state.setdefault("posteriors", {})
        topic_posteriors = self._state.setdefault("topic_posteriors", {})
        if arm not in posteriors:
            kw = self._state.get("keyword_scores", {})
            has_bootstrap = any(abs(kw.get(k, 0)) > 0.3 for k in list(kw)[:5])
            a = BETA_BOOTSTRAP_ALPHA if has_bootstrap else BETA_ALPHA_INIT
            b = BETA_BOOTSTRAP_BETA if has_bootstrap else BETA_BETA_INIT
            posteriors[arm] = [a, b]
        if topic not in topic_posteriors:
            topic_posteriors[topic] = [BETA_ALPHA_INIT, BETA_BETA_INIT]

        # Classify title style for tracking
        title_style = self._classify_title_style(title) if title else "unknown"

        self._state["posts_ledger"][str(post_id)] = {
            "topic": topic,
            "tone": tone,
            "slug": slug,
            "keywords": keywords,
            "tags": tags,
            "title": title,
            "title_style": title_style,
            "image_id": image_id,
            "image_description": image_description,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "horizons": {
                "6h":  {"views": None, "comments": None, "polled_at": None},
                "24h": {"views": None, "comments": None, "polled_at": None},
                "72h": {"views": None, "comments": None, "polled_at": None},
            },
            "final_reward": None,
        }
        _save_state(self._state)

    def record_rejected(
        self,
        topic: str,
        tone: Optional[str] = None,
        keywords: Optional[list] = None,
        tags: Optional[list] = None,
    ) -> None:
        """Record a rejected draft (quality gate failed). Applies negative reward."""
        tone = tone or "unknown"
        if tone not in TONES:
            tone = "unknown"
        self._apply_reward(
            topic=topic,
            tone=tone,
            reward=REJECTED_REWARD,
            keywords=keywords or [],
            tags=tags or [],
        )
        logger.info("Bandit: applied rejection penalty for topic=%r tone=%s", topic, tone)
        _save_state(self._state)

    def poll_and_update_wp_stats(self) -> int:
        """Poll WordPress for all pending horizon windows.

        Called multiple times a day (6h, 24h, 72h after each post).
        Returns count of horizon snapshots recorded.
        """
        base_url = os.environ.get("LLM_RELAY_SECRET_WP_BASE_URL", "").rstrip("/")
        username = os.environ.get("LLM_RELAY_SECRET_WP_USERNAME", "")
        app_password = os.environ.get("LLM_RELAY_SECRET_WP_APP_PASSWORD", "")

        if not base_url or not username or not app_password:
            logger.warning("Bandit: WP credentials not set — skipping analytics poll")
            return 0

        auth = (username, app_password)
        ledger = self._state["posts_ledger"]
        now = datetime.datetime.utcnow()
        snapshots_recorded = 0

        for post_id, entry in list(ledger.items()):
            created_at_str = entry.get("created_at", "")
            try:
                created_at = datetime.datetime.fromisoformat(created_at_str)
            except (ValueError, TypeError):
                continue

            age_hours = (now - created_at).total_seconds() / 3600.0
            horizons = entry.get("horizons", {})

            for label, min_hours in HORIZON_HOURS.items():
                h = horizons.get(label, {})
                if h.get("polled_at") is not None:
                    continue  # Already recorded
                if age_hours < min_hours:
                    continue  # Too early

                # Fetch stats
                views, comments = self._fetch_wp_stats(base_url, auth, post_id)
                if views is None:
                    continue

                h["views"] = views
                h["comments"] = comments
                h["polled_at"] = now.isoformat()
                horizons[label] = h
                snapshots_recorded += 1
                logger.info(
                    "Bandit: Recorded %s snapshot for post %s — views=%d comments=%d",
                    label, post_id, views, comments,
                )

            entry["horizons"] = horizons

            # Check if all three horizons are complete → compute final reward
            if entry.get("final_reward") is None:
                all_done = all(
                    horizons.get(lbl, {}).get("polled_at") is not None
                    for lbl in HORIZON_HOURS
                )
                if all_done:
                    final_reward = self._compute_weighted_reward(horizons)
                    entry["final_reward"] = final_reward
                    self._apply_reward(
                        topic=entry["topic"],
                        tone=entry.get("tone") or "unknown",
                        reward=final_reward,
                        keywords=entry.get("keywords", []),
                        tags=entry.get("tags", []),
                    )
                    # Track image archetype and title style performance
                    if entry.get("image_id"):
                        self._update_image_archetype_tracking(
                            image_id=entry["image_id"],
                            tone=entry.get("tone", "unknown"),
                            description=entry.get("image_description", ""),
                            reward=final_reward,
                        )
                    if entry.get("title_style"):
                        self._update_title_style_tracking(
                            title_style=entry["title_style"],
                            reward=final_reward,
                        )
                    logger.info(
                        "Bandit: Final reward %.4f for post %s (topic=%r tone=%s title_style=%s)",
                        final_reward, post_id, entry["topic"], entry.get("tone", "?"),
                        entry.get("title_style", "?"),
                    )

        self._state["episode"] += 1
        _save_state(self._state)
        return snapshots_recorded

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_wp_stats(
        self, base_url: str, auth: tuple, post_id: str
    ) -> tuple:
        """Fetch (views, comments) for a post. Returns (None, None) on failure."""
        try:
            url = f"{base_url}/wp-json/wp/v2/posts/{post_id}"
            resp = requests.get(url, auth=auth, timeout=HTTP_TIMEOUT)
            if not resp.ok:
                return None, None
            data = resp.json()
            comments = int(data.get("comment_count", 0))
            views = self._fetch_jetpack_views(base_url, auth, post_id)
            return views, comments
        except Exception as e:
            logger.warning("Bandit: Failed to fetch stats for post %s: %s", post_id, e)
            return None, None

    def _fetch_jetpack_views(self, base_url: str, auth: tuple, post_id: str) -> int:
        """Try Jetpack Stats API for view count. Returns 0 if unavailable."""
        try:
            url = f"{base_url}/wp-json/jetpack/v4/stats/post/{post_id}"
            resp = requests.get(url, auth=auth, timeout=HTTP_TIMEOUT)
            if resp.ok:
                return int(resp.json().get("views", 0))
        except Exception:
            pass
        return 0

    def _compute_weighted_reward(self, horizons: dict) -> float:
        """Weighted average reward across the three horizons."""
        total_weight = 0.0
        weighted_sum = 0.0
        history = self._state.get("reward_history", [])

        for label, weight in HORIZON_WEIGHTS.items():
            h = horizons.get(label, {})
            v = h.get("views")
            c = h.get("comments")
            if v is None:
                continue
            raw = _compute_raw_reward(v, c)
            normalised = _normalise_against_baseline(raw, history)
            weighted_sum += weight * normalised
            total_weight += weight

        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    def _apply_reward(
        self,
        topic: str,
        tone: str,
        reward: float,
        keywords: list,
        tags: list,
    ) -> None:
        """Update contextual (topic,tone) posterior with decay, plus keyword/tag EMA."""
        history = self._state.setdefault("reward_history", [])
        history.append(reward)
        if len(history) > BASELINE_WINDOW * 3:
            self._state["reward_history"] = history[-(BASELINE_WINDOW * 3):]

        arm = _arm_key(topic, tone)
        posteriors = self._state.setdefault("posteriors", {})
        a, b = posteriors.get(arm, [BETA_ALPHA_INIT, BETA_BETA_INIT])
        posteriors[arm] = list(_update_beta_posterior(a, b, reward))

        # Also update topic-only for backward compat / fallback
        topic_posteriors = self._state.setdefault("topic_posteriors", {})
        at, bt = topic_posteriors.get(topic, [BETA_ALPHA_INIT, BETA_BETA_INIT])
        topic_posteriors[topic] = list(_update_beta_posterior(at, bt, reward))

        self._update_kw_scores("keyword_scores", keywords, reward)
        self._update_kw_scores("tag_scores", tags, reward)
        self._update_prompt_knobs(reward)

    def _update_kw_scores(self, table_key: str, items: list, reward: float) -> None:
        """EMA update for keyword/tag scores with cap to prevent runaway feedback."""
        scores = self._state.setdefault(table_key, {})
        for item in items:
            old = scores.get(item, 0.0)
            updated = old + KW_ALPHA * (reward - old)
            scores[item] = max(-KW_SCORE_CAP, min(KW_SCORE_CAP, updated))

    def _update_prompt_knobs(self, reward: float) -> None:
        """Gradual prompt knobs tuning based on reward signal.

        Very conservative: requires 20+ posts before adjusting.
        Adjustments are small (±5%) to avoid instability.
        """
        history = self._state.get("reward_history", [])
        if len(history) < 20:
            return  # Wait for sufficient data

        knobs = self._state.setdefault("prompt_knobs", {
            "sarcasm_level": 0.20,
            "painpoint_intensity": 0.75,
            "audience_specificity": 0.80,
        })

        # Calculate recent trend (last 10 vs previous 10)
        recent = history[-10:]
        prev = history[-20:-10]
        recent_avg = sum(recent) / len(recent)
        prev_avg = sum(prev) / len(prev)
        trend = recent_avg - prev_avg

        # Only adjust if trend is significant (>0.05 delta)
        if abs(trend) < 0.05:
            return

        # Small adjustments based on reward trend
        step = 0.05 if trend > 0 else -0.05

        # Adjust painpoint_intensity: higher reward → increase intensity slightly
        old_pain = knobs.get("painpoint_intensity", 0.75)
        knobs["painpoint_intensity"] = max(0.5, min(0.95, old_pain + step))

        # Adjust audience_specificity: higher reward → increase targeting precision
        old_aud = knobs.get("audience_specificity", 0.80)
        knobs["audience_specificity"] = max(0.6, min(0.95, old_aud + step * 0.5))

        logger.info(
            "Prompt knobs adjusted: painpoint=%.2f audience=%.2f (trend=%.3f)",
            knobs["painpoint_intensity"],
            knobs["audience_specificity"],
            trend,
        )

    def _classify_title_style(self, title: str) -> str:
        """Classify title into: question, how-to, provocative, or statement."""
        if not title:
            return "unknown"

        lower = title.lower().strip()

        # Question style: ends with ? or starts with question words
        if "?" in title or lower.startswith(("why ", "how ", "what ", "when ", "where ", "who ", "can ", "should ", "is ", "are ", "do ", "does ")):
            return "question"

        # How-to style: instructional
        if lower.startswith(("how to ", "ways to ", "steps to ", "guide to ")):
            return "how-to"

        # Provocative style: strong emotion words, "you", negations
        provocative_markers = ["won't believe", "shocking", "secret", "truth about", "nobody tells", "quietly", "silently", "steal", "destroy"]
        if any(marker in lower for marker in provocative_markers):
            return "provocative"

        # Default: statement
        return "statement"

    def _update_image_archetype_tracking(self, image_id: str, tone: str, description: str, reward: float) -> None:
        """Track which image archetypes (tone + visual elements) drive engagement."""
        if not image_id:
            return

        archetypes = self._state.setdefault("image_archetypes", {})

        if image_id not in archetypes:
            archetypes[image_id] = {
                "tone": tone,
                "description": description[:100],
                "total_reward": 0.0,
                "count": 0,
                "avg_reward": 0.0,
            }

        arch = archetypes[image_id]
        arch["total_reward"] += reward
        arch["count"] += 1
        arch["avg_reward"] = arch["total_reward"] / arch["count"]

    def _update_title_style_tracking(self, title_style: str, reward: float) -> None:
        """Track which title styles (question, how-to, provocative, statement) perform best."""
        if not title_style or title_style == "unknown":
            return

        styles = self._state.setdefault("title_styles", {})

        if title_style not in styles:
            styles[title_style] = {
                "total_reward": 0.0,
                "count": 0,
                "avg_reward": 0.0,
            }

        s = styles[title_style]
        s["total_reward"] += reward
        s["count"] += 1
        s["avg_reward"] = s["total_reward"] / s["count"]


# Keep backward-compatible alias so existing imports (blog_scheduler.py) still work
QLearner = BanditLearner
