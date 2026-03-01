"""Template registry and loader.

Manages registration and lookup of video templates. Built-in templates
are auto-registered on import. Custom templates can be registered at runtime.
"""

import logging
from typing import Optional

from .base import BaseTemplate

logger = logging.getLogger(__name__)


class TemplateRegistry:
    """Central registry for video templates.

    Holds all available templates indexed by name. Supports listing,
    lookup, and runtime registration of custom templates.
    """

    def __init__(self):
        self._templates: dict[str, BaseTemplate] = {}

    def register(self, template: BaseTemplate) -> None:
        """Register a template instance.

        Args:
            template: BaseTemplate subclass instance

        Raises:
            ValueError: If a template with the same name is already registered
        """
        if template.name in self._templates:
            raise ValueError(
                f"Template '{template.name}' already registered. "
                f"Use a different name or unregister first."
            )
        self._templates[template.name] = template
        logger.debug("Registered template: %s", template.name)

    def unregister(self, name: str) -> None:
        """Remove a template by name.

        Args:
            name: Template name to remove

        Raises:
            KeyError: If template not found
        """
        if name not in self._templates:
            raise KeyError(f"Template '{name}' not found")
        del self._templates[name]

    def get(self, name: str) -> BaseTemplate:
        """Look up a template by name.

        Args:
            name: Template name

        Returns:
            The template instance

        Raises:
            ValueError: If template not found
        """
        if name not in self._templates:
            raise ValueError(
                f"Unknown template '{name}'. "
                f"Available: {sorted(self._templates.keys())}"
            )
        return self._templates[name]

    def list(self) -> list[dict]:
        """List all registered templates with metadata.

        Returns:
            List of dicts with name, description, supported_platforms, etc.
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "supported_platforms": t.supported_platforms,
                "default_duration_seconds": t.default_duration_seconds,
                "min_images": t.min_images,
                "max_images": t.max_images,
            }
            for t in sorted(self._templates.values(), key=lambda t: t.name)
        ]

    def list_names(self) -> "list[str]":
        """Return sorted list of all template names."""
        return sorted(self._templates.keys())

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, name: str) -> bool:
        return name in self._templates


# Module-level default registry instance
_DEFAULT_REGISTRY = TemplateRegistry()


def register_template(template: BaseTemplate) -> None:
    """Register a template in the default registry."""
    _DEFAULT_REGISTRY.register(template)


def get_template(name: str) -> BaseTemplate:
    """Look up a template in the default registry."""
    return _DEFAULT_REGISTRY.get(name)


def list_templates() -> list[dict]:
    """List all templates in the default registry."""
    return _DEFAULT_REGISTRY.list()


def _register_builtins() -> None:
    """Register all built-in templates. Called once on module import."""
    from .builtin.instagram_reel_slideshow import InstagramReelSlideshow
    from .builtin.instagram_reel_quote import InstagramReelQuote
    from .builtin.tiktok_before_after import TikTokBeforeAfter
    from .builtin.product_showcase import ProductShowcase
    from .builtin.day_in_life import DayInLife
    from .builtin.tutorial_steps import TutorialSteps
    from .builtin.text_story import TextStory
    from .builtin.photo_montage import PhotoMontage
    from .builtin.countdown import Countdown
    from .builtin.audiogram import Audiogram

    builtins = [
        InstagramReelSlideshow(),
        InstagramReelQuote(),
        TikTokBeforeAfter(),
        ProductShowcase(),
        DayInLife(),
        TutorialSteps(),
        TextStory(),
        PhotoMontage(),
        Countdown(),
        Audiogram(),
    ]

    for template in builtins:
        try:
            _DEFAULT_REGISTRY.register(template)
        except ValueError:
            pass  # Already registered (e.g. during reload)


# Auto-register builtins on import
_register_builtins()
