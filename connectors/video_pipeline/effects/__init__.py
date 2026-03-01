"""Visual Effects & Animation Toolkit for the video pipeline.

Provides a library of compositing effects applied to frames:
- Color grading (brightness, contrast, saturation, warm/cool tone)
- Blur effects (motion blur, gaussian blur, depth-of-field)
- Film effects (grain, vignette, letterbox, light leaks)
- Glitch effects (chromatic aberration, scanlines, RGB shift, pixel sort)
- Animated shapes (geometric overlays — circles, lines, grids)
- Particle systems (snow, rain, dust, confetti)

Every effect is a pure function with signature:
    (frame: PIL.Image, progress: float, params: dict) -> PIL.Image

Effects are:
- Composable (chain them in any order via EffectChain)
- Testable (give it an image, check the output)
- Stateless (no side effects — enables parallel rendering)

The EffectChain processor applies multiple effects in order per frame.
Integration with the compositor is via the Clip.effects field.
"""

__version__ = "0.1.0"

# Import all effect modules to trigger registration
from . import color   # noqa: F401
from . import blur    # noqa: F401
from . import film    # noqa: F401
from . import glitch  # noqa: F401
from . import shapes  # noqa: F401
from . import particles  # noqa: F401

from .registry import get_effect, list_effects, register_effect  # noqa: F401
from .pipeline import EffectChain  # noqa: F401
