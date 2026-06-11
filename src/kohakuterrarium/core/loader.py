"""
Module loader for custom components.

Loads tools, sub-agents, input/output modules, and triggers from:
1. Built-in modules (default)
2. Agent folder custom modules (Python files)
3. External packages (pip-installed)
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, TypeVar

from kohakuterrarium.packages.resolve import resolve_package_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ModuleLoadError(Exception):
    """Error loading a module."""

    pass


class ModuleLoader:
    """
    Load custom modules from various sources.

    Supports three module types:
    - builtin: Load from kohakuterrarium.builtins.*
    - custom: Load from Python file in agent folder
    - package: Load from installed Python package

    Usage:
        loader = ModuleLoader(agent_path=Path("agents/my_agent"))

        # Load a custom tool
        tool_cls = loader.load_class(
            module_path="./custom/tools/my_tool.py",
            class_name="MyTool",
            module_type="custom",
        )
        tool = tool_cls(**options)

        # Load from package
        input_cls = loader.load_class(
            module_path="my_package.input.asr",
            class_name="ASRInput",
            module_type="package",
        )
    """

    def __init__(self, agent_path: Path | None = None):
        """
        Initialize loader.

        Args:
            agent_path: Path to agent folder (required for custom modules)
        """
        self.agent_path = agent_path
        self._loaded_modules: dict[str, Any] = {}
        self._module_counter = 0

    def load_class(
        self,
        module_path: str,
        class_name: str,
        module_type: str = "custom",
    ) -> type[T]:
        """
        Load a class from module path.

        Args:
            module_path: Path to module:
                - custom: "./custom/tool.py" (relative to agent folder)
                - package: "package.module.submodule"
            class_name: Name of class to import
            module_type: "custom" or "package"

        Returns:
            The loaded class

        Raises:
            ModuleLoadError: If module or class cannot be loaded
        """
        try:
            match module_type:
                case "custom":
                    return self._load_from_file(module_path, class_name)
                case "package":
                    return self._load_from_package(module_path, class_name)
                case _:
                    raise ModuleLoadError(f"Unknown module type: {module_type}")
        except ModuleLoadError:
            raise
        except Exception as e:
            raise ModuleLoadError(
                f"Failed to load {class_name} from {module_path}: {e}"
            ) from e

    def load_instance(
        self,
        module_path: str,
        class_name: str,
        module_type: str = "custom",
        options: dict[str, Any] | None = None,
    ) -> T:
        """
        Load and instantiate a class.

        Args:
            module_path: Path to module
            class_name: Name of class to import
            module_type: "custom" or "package"
            options: Kwargs to pass to constructor

        Returns:
            Instance of the loaded class
        """
        cls = self.load_class(module_path, class_name, module_type)
        options = options or {}
        return cls(**options)

    def load_config_object(
        self,
        module_path: str,
        object_name: str,
        module_type: str = "custom",
    ) -> Any:
        """
        Load a config object (not a class) from module.

        Useful for loading SubAgentConfig instances defined at module level.

        Args:
            module_path: Path to module
            object_name: Name of object to import
            module_type: "custom" or "package"

        Returns:
            The loaded object
        """
        try:
            match module_type:
                case "custom":
                    module = self._load_module_from_file(module_path)
                case "package":
                    module = importlib.import_module(module_path)
                case _:
                    raise ModuleLoadError(f"Unknown module type: {module_type}")

            if not hasattr(module, object_name):
                raise ModuleLoadError(
                    f"Object '{object_name}' not found in module {module_path}"
                )

            return getattr(module, object_name)

        except ModuleLoadError:
            raise
        except Exception as e:
            raise ModuleLoadError(
                f"Failed to load {object_name} from {module_path}: {e}"
            ) from e

    def _load_from_file(self, rel_path: str, class_name: str) -> type:
        """Load class from Python file in agent folder."""
        module = self._load_module_from_file(rel_path)

        if not hasattr(module, class_name):
            raise ModuleLoadError(
                f"Class '{class_name}' not found in module {rel_path}"
            )

        cls = getattr(module, class_name)
        logger.debug(
            "Loaded class from file",
            class_name=class_name,
            path=rel_path,
        )
        return cls

    def _load_module_from_file(self, rel_path: str) -> Any:
        """Load module from Python file.

        ``rel_path`` may also be a ``@pkg/...`` package reference or an
        absolute path — both bypass the agent-folder join.
        """
        if rel_path.startswith("@"):
            full_path = resolve_package_path(rel_path)
        else:
            candidate = Path(rel_path).expanduser()
            if candidate.is_absolute():
                full_path = candidate.resolve()
            else:
                if self.agent_path is None:
                    raise ModuleLoadError(
                        "agent_path required for custom modules. "
                        "Set agent_path when creating ModuleLoader."
                    )
                full_path = (self.agent_path / candidate).resolve()

        if not full_path.exists():
            raise ModuleLoadError(f"Module file not found: {full_path}")

        if not full_path.suffix == ".py":
            raise ModuleLoadError(f"Module must be a Python file: {full_path}")

        # Check cache
        cache_key = str(full_path)
        if cache_key in self._loaded_modules:
            return self._loaded_modules[cache_key]

        # Generate unique module name to avoid conflicts
        self._module_counter += 1
        module_name = f"kohaku_custom_{self._module_counter}_{full_path.stem}"

        # Add agent folder to sys.path temporarily for relative imports
        # (no agent folder for @pkg / absolute-path modules).
        agent_custom_path = (
            str(self.agent_path / "custom") if self.agent_path is not None else None
        )
        path_added = False
        if agent_custom_path is not None and agent_custom_path not in sys.path:
            sys.path.insert(0, agent_custom_path)
            path_added = True

        try:
            # Load module from file
            spec = importlib.util.spec_from_file_location(module_name, full_path)
            if spec is None or spec.loader is None:
                raise ModuleLoadError(f"Cannot create module spec for: {full_path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Cache for reuse
            self._loaded_modules[cache_key] = module

            logger.debug("Loaded module from file", path=str(full_path))
            return module

        finally:
            # Clean up sys.path
            if path_added and agent_custom_path in sys.path:
                sys.path.remove(agent_custom_path)

    def _load_from_package(self, module_path: str, class_name: str) -> type:
        """Load class from installed package."""
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ModuleLoadError(
                f"Cannot import package '{module_path}'. "
                f"Make sure it's installed: {e}"
            ) from e

        if not hasattr(module, class_name):
            raise ModuleLoadError(
                f"Class '{class_name}' not found in package {module_path}"
            )

        cls = getattr(module, class_name)
        logger.debug(
            "Loaded class from package",
            class_name=class_name,
            package=module_path,
        )
        return cls

    def clear_cache(self) -> None:
        """Clear loaded module cache."""
        self._loaded_modules.clear()


# Convenience function for one-off loading
def load_custom_module(
    agent_path: Path,
    module_path: str,
    class_name: str,
    module_type: str = "custom",
    options: dict[str, Any] | None = None,
) -> Any:
    """
    Load and instantiate a custom module.

    Convenience function for one-off module loading.

    Args:
        agent_path: Path to agent folder
        module_path: Path to module
        class_name: Class name to load
        module_type: "custom" or "package"
        options: Constructor options

    Returns:
        Instance of the loaded class
    """
    loader = ModuleLoader(agent_path)
    return loader.load_instance(module_path, class_name, module_type, options)
