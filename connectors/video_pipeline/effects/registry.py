"""Central registry for all visual effects.

Effects are pure functions with signature:
    (frame: PIL.Image, progress: float, params: dict) -> PIL.Image

- frame: The input frame (RGB PIL Image)
- progress: 0.0-1.0, how far through the clip we are (for animation)
- params: Effect-specific parameters

This design means effects are:
- Composable (chain them in any order)
- Testable (give it an image, check the output)
- Stateless (no side effects)
"""

from PIL import Image
from typing import Callable

EffectFunction = Callable[[Image.Image, float, dict], Image.Image]

_REGISTRY: dict[str, EffectFunction] = {}


def register_effect(name: str):
    """Decorator to register an effect function.

    Usage:
        @register_effect("brightness")
        def brightness(frame, progress, params):
            ...
    """
    def decorator(func: EffectFunction) -> EffectFunction:
        _REGISTRY[name] = func
        return func
    return decorator


def get_effect(name: str) -> EffectFunction:
    """Look up a registered effect by name.

    Raises:
        ValueError: If the effect name is not registered
    """
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown effect '{name}'. Available: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def list_effects() -> list[str]:
    """Return sorted list of all registered effect names."""
    return sorted(_REGISTRY.keys())
