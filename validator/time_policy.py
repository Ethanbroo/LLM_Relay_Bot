"""
Time policy implementation for deterministic timestamps.

Supports two modes:
- recorded: Capture timestamp at ingress, propagate everywhere
- frozen: Use fixed timestamp for entire run (stricter determinism)
"""

from datetime import datetime, timezone
from typing import Optional
import yaml


class TimePolicy:
    """Manages timestamp generation according to configured policy."""

    def __init__(self, mode: str = "recorded", frozen_time: Optional[str] = None):
        """
        Initialize time policy.

        Args:
            mode: "recorded" or "frozen"
            frozen_time: ISO 8601 timestamp for frozen mode (optional)
        """
        if mode not in ("recorded", "frozen"):
            raise ValueError(f"Invalid time policy mode: {mode}")

        self.mode = mode
        self._frozen_time: Optional[datetime] = None
        self._recorded_time: Optional[datetime] = None

        if mode == "frozen":
            if frozen_time:
                self._frozen_time = datetime.fromisoformat(frozen_time)
            else:
                # Default: use current time as frozen time
                self._frozen_time = datetime.now(timezone.utc)

    def get_timestamp(self) -> str:
        """
        Get timestamp according to policy.

        Returns:
            ISO 8601 timestamp string with UTC timezone
        """
        if self.mode == "frozen":
            return self._frozen_time.isoformat()

        # Recorded mode: capture once, reuse
        if self._recorded_time is None:
            self._recorded_time = datetime.now(timezone.utc)

        return self._recorded_time.isoformat()

    def reset_recorded(self):
        """Reset recorded timestamp (for new message ingress)."""
        if self.mode == "recorded":
            self._recorded_time = None

    @classmethod
    def from_config(cls, config_path: str = "config/core.yaml") -> "TimePolicy":
        """
        Load time policy from configuration file.

        Args:
            config_path: Path to core.yaml

        Returns:
            Configured TimePolicy instance
        """
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        time_config = config.get('time_policy', {})
        mode = time_config.get('mode', 'recorded')
        frozen_time = time_config.get('frozen_time')

        return cls(mode=mode, frozen_time=frozen_time)
