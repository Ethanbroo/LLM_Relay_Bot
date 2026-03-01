"""Community & Plugin Architecture for the video pipeline.

Allows third-party effects, templates, and render backends to be loaded
at runtime from a plugin directory. Plugins are validated for security
before execution.

Plugin types:
- effect: New visual effects registered into the effects registry
- template: New video templates registered into the template registry
- render_backend: New render backends for the cloud orchestrator

Usage:
    from connectors.video_pipeline.plugins.loader import PluginLoader
    loader = PluginLoader(Path("plugins/"))
    result = loader.load_all()
"""

__version__ = "0.1.0"

from .interface import PluginBase, EffectPlugin, TemplatePlugin, RenderBackendPlugin
from .loader import PluginLoader, PluginLoadResult, PluginLoadError
from .validator import validate_plugin_code

__all__ = [
    "PluginBase",
    "EffectPlugin",
    "TemplatePlugin",
    "RenderBackendPlugin",
    "PluginLoader",
    "PluginLoadResult",
    "PluginLoadError",
    "validate_plugin_code",
]
