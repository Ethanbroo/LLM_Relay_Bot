"""Plugin interface contracts.

Defines the ABCs that plugins must implement depending on their type.
Each plugin type maps to an existing system:
- EffectPlugin: registers new visual effects into the effects registry
- TemplatePlugin: registers new video templates into the template registry
- RenderBackendPlugin: provides a new render backend (AbstractRenderProvider)
"""

from abc import ABC, abstractmethod
from typing import Optional

from PIL import Image

from ..effects.registry import EffectFunction
from ..templates.base import BaseTemplate, TemplateInput
from ..cloud.base_provider import AbstractRenderProvider


class PluginBase(ABC):
    """Base interface all plugins must satisfy."""

    @property
    @abstractmethod
    def plugin_name(self) -> str:
        """Unique name for this plugin."""
        ...

    @property
    @abstractmethod
    def plugin_version(self) -> str:
        """Semantic version string (e.g. '1.0.0')."""
        ...

    @property
    @abstractmethod
    def plugin_type(self) -> str:
        """One of: 'effect', 'template', 'render_backend'."""
        ...


class EffectPlugin(PluginBase):
    """Plugin that provides one or more visual effects.

    Effects are pure functions with signature:
        (frame: PIL.Image, progress: float, params: dict) -> PIL.Image

    The plugin's register() method is called during loading to register
    its effects into the global effects registry.
    """

    @property
    def plugin_type(self) -> str:
        return "effect"

    @abstractmethod
    def get_effects(self) -> dict[str, EffectFunction]:
        """Return a mapping of effect_name -> effect_function.

        Each function must have signature:
            (frame: PIL.Image.Image, progress: float, params: dict) -> PIL.Image.Image

        Returns:
            Dict mapping effect names to their implementations
        """
        ...


class TemplatePlugin(PluginBase):
    """Plugin that provides one or more video templates.

    Each template must be a subclass of BaseTemplate with a unique name.
    """

    @property
    def plugin_type(self) -> str:
        return "template"

    @abstractmethod
    def get_templates(self) -> list[BaseTemplate]:
        """Return template instances to register.

        Returns:
            List of BaseTemplate subclass instances
        """
        ...


class RenderBackendPlugin(PluginBase):
    """Plugin that provides a custom render backend.

    The backend must implement AbstractRenderProvider.
    """

    @property
    def plugin_type(self) -> str:
        return "render_backend"

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return a unique backend identifier (e.g. 'custom_gpu').

        This is used in config to select the backend.
        """
        ...

    @abstractmethod
    def create_provider(self, config: Optional[dict] = None) -> AbstractRenderProvider:
        """Create a configured render provider instance.

        Args:
            config: Backend-specific configuration from core.yaml

        Returns:
            Ready-to-use render provider
        """
        ...
