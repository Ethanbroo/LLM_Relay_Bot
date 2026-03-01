"""Effect chain processor.

Applies a chain of effects to a frame. Effects are specified as a list
of (effect_name, params) tuples and applied in order.

Usage:
    chain = EffectChain([
        ("warm_tone", {"intensity": 0.1}),
        ("film_grain", {"intensity": 0.2}),
        ("vignette", {"strength": 0.4}),
        ("letterbox", {"bar_height_percent": 0.1}),
    ])
    output_frame = chain.apply(input_frame, progress=0.5)
"""

import logging
from PIL import Image
from .registry import get_effect

logger = logging.getLogger(__name__)


class EffectChain:
    """Applies a sequence of effects to a frame.

    Effects are validated at construction time (fail-fast if an effect
    name is unknown). At render time, each effect is applied in order.
    """

    def __init__(self, effects: list[tuple[str, dict]]):
        """
        Args:
            effects: List of (effect_name, params) tuples.
                     Applied in order (first in list = first applied).

        Raises:
            ValueError: If any effect name is not registered
        """
        self.effects = []
        for name, params in effects:
            func = get_effect(name)  # Raises ValueError if not found
            self.effects.append((name, func, params))

    def apply(self, frame: Image.Image, progress: float) -> Image.Image:
        """Apply all effects in sequence.

        Args:
            frame: Input PIL Image (RGB)
            progress: 0.0-1.0, how far through the clip

        Returns:
            Processed PIL Image (RGB)
        """
        for name, func, params in self.effects:
            try:
                frame = func(frame, progress, params)
            except Exception as e:
                logger.warning(
                    "Effect '%s' failed (progress=%.2f): %s — skipping",
                    name, progress, e,
                )
        return frame

    def __len__(self) -> int:
        return len(self.effects)

    def __repr__(self) -> str:
        names = [name for name, _, _ in self.effects]
        return f"EffectChain({names})"
