"""Plugin loader — discovers and loads plugins from a directory.

Each plugin is a subdirectory containing:
- plugin.json: metadata (name, version, type, entry_point)
- Python file(s) referenced by entry_point

Plugin types:
- "effect": Registers new visual effects into the effects registry
- "template": Registers new video templates into the template registry
- "render_backend": Registers new render backends

Security: Plugin source code is validated via AST analysis before loading.
Plugins cannot import blocked modules (subprocess, socket, etc.) or call
dangerous builtins (exec, eval, __import__).

Plugins integrate with the existing registries:
- Effects go into connectors.video_pipeline.effects.registry
- Templates go into connectors.video_pipeline.templates.registry
- Render backends are returned for the orchestrator to use
"""

import json
import importlib.util
import logging
from pathlib import Path
from typing import Optional

from .validator import validate_plugin_code
from .interface import PluginBase, EffectPlugin, TemplatePlugin, RenderBackendPlugin

logger = logging.getLogger(__name__)

VALID_PLUGIN_TYPES = {"effect", "template", "render_backend"}


class PluginLoadError(Exception):
    """Raised when a plugin fails to load."""
    pass


class PluginManifest:
    """Parsed plugin.json metadata."""

    def __init__(self, name: str, version: str, plugin_type: str,
                 entry_point: str, description: str = "",
                 author: str = "", dependencies: list[str] | None = None):
        self.name = name
        self.version = version
        self.plugin_type = plugin_type
        self.entry_point = entry_point
        self.description = description
        self.author = author
        self.dependencies = dependencies or []

    @classmethod
    def from_file(cls, manifest_path: Path) -> "PluginManifest":
        """Parse a plugin.json file.

        Args:
            manifest_path: Path to plugin.json

        Returns:
            Parsed manifest

        Raises:
            PluginLoadError: If manifest is invalid
        """
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise PluginLoadError(f"Invalid plugin.json: {e}")

        required = {"name", "version", "type", "entry_point"}
        missing = required - data.keys()
        if missing:
            raise PluginLoadError(
                f"plugin.json missing required fields: {sorted(missing)}"
            )

        plugin_type = data["type"]
        if plugin_type not in VALID_PLUGIN_TYPES:
            raise PluginLoadError(
                f"Unknown plugin type '{plugin_type}'. "
                f"Must be one of: {sorted(VALID_PLUGIN_TYPES)}"
            )

        return cls(
            name=data["name"],
            version=data["version"],
            plugin_type=plugin_type,
            entry_point=data["entry_point"],
            description=data.get("description", ""),
            author=data.get("author", ""),
            dependencies=data.get("dependencies"),
        )


class LoadedPlugin:
    """A successfully loaded plugin with its manifest and instance."""

    def __init__(self, manifest: PluginManifest, instance: PluginBase, path: Path):
        self.manifest = manifest
        self.instance = instance
        self.path = path

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def plugin_type(self) -> str:
        return self.manifest.plugin_type

    def __repr__(self) -> str:
        return (
            f"LoadedPlugin({self.name!r}, type={self.plugin_type!r}, "
            f"v={self.manifest.version!r})"
        )


class PluginLoader:
    """Discovers, validates, and loads plugins from a directory.

    Usage:
        loader = PluginLoader(Path("plugins/"), audit_callback=my_audit_fn)
        result = loader.load_all()
        # result.effects: list of effect names registered
        # result.templates: list of template names registered
        # result.render_backends: dict of backend_name -> RenderBackendPlugin
    """

    def __init__(
        self,
        plugin_dir: Path,
        audit_callback: Optional[callable] = None,
    ):
        """
        Args:
            plugin_dir: Directory containing plugin subdirectories
            audit_callback: Optional fn(event_type: str, payload: dict) for audit events
        """
        self.plugin_dir = Path(plugin_dir)
        self._audit = audit_callback or (lambda *a, **kw: None)
        self._loaded: list[LoadedPlugin] = []

    def load_all(self) -> "PluginLoadResult":
        """Discover and load all valid plugins from the plugin directory.

        Iterates over subdirectories, validates each plugin's source code,
        loads the module, and registers effects/templates into their
        respective registries.

        Returns:
            PluginLoadResult with lists of what was loaded
        """
        result = PluginLoadResult()

        if not self.plugin_dir.exists():
            logger.debug("Plugin directory does not exist: %s", self.plugin_dir)
            return result

        for subdir in sorted(self.plugin_dir.iterdir()):
            if not subdir.is_dir():
                continue

            manifest_path = subdir / "plugin.json"
            if not manifest_path.exists():
                continue

            try:
                loaded = self._load_single(subdir, manifest_path)
                self._loaded.append(loaded)
                self._register_plugin(loaded, result)
            except PluginLoadError as e:
                logger.warning("Skipping plugin %s: %s", subdir.name, e)
                self._audit("PLUGIN_LOAD_REJECTED", {
                    "plugin_dir": subdir.name,
                    "error": str(e)[:200],
                })
            except Exception as e:
                logger.error("Unexpected error loading plugin %s: %s", subdir.name, e)
                self._audit("PLUGIN_LOAD_ERROR", {
                    "plugin_dir": subdir.name,
                    "error": str(e)[:200],
                })

        return result

    def _load_single(self, plugin_dir: Path, manifest_path: Path) -> LoadedPlugin:
        """Load a single plugin from its directory.

        Steps:
        1. Parse plugin.json
        2. Validate source code security
        3. Load module via importlib
        4. Instantiate plugin class

        Args:
            plugin_dir: The plugin's directory
            manifest_path: Path to plugin.json

        Returns:
            LoadedPlugin instance

        Raises:
            PluginLoadError: If any step fails
        """
        # Step 1: Parse manifest
        manifest = PluginManifest.from_file(manifest_path)

        # Step 2: Locate entry point
        code_path = plugin_dir / manifest.entry_point
        if not code_path.exists():
            raise PluginLoadError(
                f"Entry point '{manifest.entry_point}' not found in {plugin_dir.name}"
            )

        # Step 3: Security validation
        violations = validate_plugin_code(code_path)
        if violations:
            violation_strs = [str(v) for v in violations[:5]]
            self._audit("PLUGIN_SECURITY_VIOLATION", {
                "plugin_name": manifest.name,
                "violations": violation_strs,
            })
            raise PluginLoadError(
                f"Security violations in '{manifest.name}': {violation_strs}"
            )

        # Step 4: Load the module
        module_name = f"plugin_{manifest.name}"
        spec = importlib.util.spec_from_file_location(module_name, str(code_path))
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Cannot create module spec for {code_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise PluginLoadError(f"Module execution failed: {e}")

        # Step 5: Find and instantiate the plugin class
        plugin_instance = self._find_plugin_instance(module, manifest)

        logger.info(
            "Loaded plugin: %s v%s (%s)",
            manifest.name, manifest.version, manifest.plugin_type,
        )

        return LoadedPlugin(manifest=manifest, instance=plugin_instance, path=plugin_dir)

    def _find_plugin_instance(self, module, manifest: PluginManifest) -> PluginBase:
        """Find the plugin class in a loaded module and instantiate it.

        Looks for:
        - A class named 'Plugin' (convention)
        - Any class that is a subclass of the appropriate plugin interface
        - A 'register' function (legacy effect plugin pattern)

        Args:
            module: Loaded Python module
            manifest: Plugin manifest for type checking

        Returns:
            Instantiated plugin

        Raises:
            PluginLoadError: If no suitable class found
        """
        expected_base = {
            "effect": EffectPlugin,
            "template": TemplatePlugin,
            "render_backend": RenderBackendPlugin,
        }[manifest.plugin_type]

        # Try 'Plugin' class first
        if hasattr(module, "Plugin"):
            cls = module.Plugin
            if isinstance(cls, type) and issubclass(cls, expected_base):
                return cls()

        # Search for any matching subclass
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type)
                    and issubclass(attr, expected_base)
                    and attr is not expected_base
                    and attr is not PluginBase):
                return attr()

        # Legacy support: effect plugins with a register() function
        if manifest.plugin_type == "effect" and hasattr(module, "register"):
            return _LegacyEffectAdapter(module, manifest)

        raise PluginLoadError(
            f"No class implementing {expected_base.__name__} found in "
            f"'{manifest.entry_point}'. "
            f"Define a class that inherits from {expected_base.__name__}."
        )

    def _register_plugin(self, loaded: LoadedPlugin, result: "PluginLoadResult"):
        """Register a loaded plugin into the appropriate system registry.

        Args:
            loaded: The loaded plugin
            result: PluginLoadResult to update
        """
        if loaded.plugin_type == "effect":
            self._register_effect_plugin(loaded, result)
        elif loaded.plugin_type == "template":
            self._register_template_plugin(loaded, result)
        elif loaded.plugin_type == "render_backend":
            self._register_backend_plugin(loaded, result)

        self._audit("PLUGIN_LOADED", {
            "plugin_name": loaded.name,
            "plugin_type": loaded.plugin_type,
            "plugin_version": loaded.manifest.version,
        })

    def _register_effect_plugin(self, loaded: LoadedPlugin, result: "PluginLoadResult"):
        """Register effects from an effect plugin into the effects registry."""
        from ..effects.registry import _REGISTRY

        instance = loaded.instance
        if isinstance(instance, EffectPlugin):
            effects = instance.get_effects()
            for effect_name, effect_fn in effects.items():
                _REGISTRY[effect_name] = effect_fn
                result.effects.append(effect_name)
                logger.debug("Registered plugin effect: %s", effect_name)
        elif isinstance(instance, _LegacyEffectAdapter):
            # Legacy: the module's register() was already called during instantiation
            result.effects.append(loaded.name)

    def _register_template_plugin(self, loaded: LoadedPlugin, result: "PluginLoadResult"):
        """Register templates from a template plugin into the template registry."""
        from ..templates.registry import register_template

        instance = loaded.instance
        if isinstance(instance, TemplatePlugin):
            templates = instance.get_templates()
            for template in templates:
                register_template(template)
                result.templates.append(template.name)
                logger.debug("Registered plugin template: %s", template.name)

    def _register_backend_plugin(self, loaded: LoadedPlugin, result: "PluginLoadResult"):
        """Store render backend plugins for later use by the orchestrator."""
        instance = loaded.instance
        if isinstance(instance, RenderBackendPlugin):
            backend_name = instance.get_backend_name()
            result.render_backends[backend_name] = instance
            logger.debug("Registered plugin render backend: %s", backend_name)

    @property
    def loaded_plugins(self) -> list[LoadedPlugin]:
        """Return all successfully loaded plugins."""
        return list(self._loaded)


class PluginLoadResult:
    """Summary of what was loaded by PluginLoader.load_all()."""

    def __init__(self):
        self.effects: list[str] = []
        self.templates: list[str] = []
        self.render_backends: dict[str, RenderBackendPlugin] = {}
        self.errors: list[str] = []

    @property
    def total_loaded(self) -> int:
        return len(self.effects) + len(self.templates) + len(self.render_backends)

    def __repr__(self) -> str:
        return (
            f"PluginLoadResult(effects={len(self.effects)}, "
            f"templates={len(self.templates)}, "
            f"render_backends={len(self.render_backends)})"
        )


class _LegacyEffectAdapter(EffectPlugin):
    """Adapter for effect plugins that use a bare register() function.

    Supports the simpler pattern where a plugin module has:
        def register():
            from connectors.video_pipeline.effects.registry import register_effect
            @register_effect("my_effect")
            def my_effect(frame, progress, params): ...
    """

    def __init__(self, module, manifest: PluginManifest):
        self._module = module
        self._manifest = manifest
        # Call register() to register effects
        module.register()

    @property
    def plugin_name(self) -> str:
        return self._manifest.name

    @property
    def plugin_version(self) -> str:
        return self._manifest.version

    def get_effects(self) -> dict:
        return {}  # Already registered via register() call
