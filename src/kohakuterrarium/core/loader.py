"""Loading of custom components from files and installed packages."""

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, TypeVar

from kohakuterrarium.packages.resolve import (
    ensure_module_importable,
    resolve_package_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ModuleLoadError(Exception):
    """Raised when a configured module or object cannot be loaded."""

    pass


def _init_keyword_names(cls: type) -> set[str] | None:
    """Keyword names ``cls.__init__`` accepts; ``None`` means it takes ``**kwargs``."""
    try:
        params = inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return {
        name
        for name, param in params.items()
        if param.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and name != "self"
    }


def instantiate_with_options(cls: type[T], options: dict[str, Any] | None) -> T:
    """Build ``cls`` from an options mapping under either constructor contract.

    Configured classes take the mapping expanded (``cls(**options)``) or as one
    argument (``cls(options=options)``); both are accepted. A class that takes
    neither keeps the mapping on ``self.options`` so option validation still
    sees it.
    """
    opts = dict(options or {})
    if not opts:
        return cls()
    names = _init_keyword_names(cls)
    if names is None or (set(opts) <= names and "options" not in names):
        return cls(**opts)
    if "options" in names:
        return cls(options=opts)
    instance = cls()
    instance.options = opts
    return instance


class ModuleLoader:
    """Resolve configured classes and objects from agent files or packages."""

    def __init__(self, agent_path: Path | None = None):
        """Initialize with the base directory for relative custom modules."""
        self.agent_path = agent_path
        self._loaded_modules: dict[str, Any] = {}
        self._module_counter = 0

    def load_class(
        self,
        module_path: str,
        class_name: str,
        module_type: str = "custom",
    ) -> type[T]:
        """Load a named class from a custom file or package module."""
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
        """Load a class and instantiate it with configured options."""
        cls = self.load_class(module_path, class_name, module_type)
        return instantiate_with_options(cls, options)

    def load_config_object(
        self,
        module_path: str,
        object_name: str,
        module_type: str = "custom",
    ) -> Any:
        """Load a named module-level configuration object."""
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

        cache_key = str(full_path)
        if cache_key in self._loaded_modules:
            return self._loaded_modules[cache_key]

        # File-backed modules need unique names so identical stems do not collide.
        self._module_counter += 1
        module_name = f"kohaku_custom_{self._module_counter}_{full_path.stem}"

        # Agent-local modules may import siblings by name; package references and
        # absolute files do not inherit this temporary search path.
        agent_custom_path = (
            str(self.agent_path / "custom") if self.agent_path is not None else None
        )
        path_added = False
        if agent_custom_path is not None and agent_custom_path not in sys.path:
            sys.path.insert(0, agent_custom_path)
            path_added = True

        try:
            spec = importlib.util.spec_from_file_location(module_name, full_path)
            if spec is None or spec.loader is None:
                raise ModuleLoadError(f"Cannot create module spec for: {full_path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            self._loaded_modules[cache_key] = module

            logger.debug("Loaded module from file", path=str(full_path))
            return module

        finally:
            if path_added and agent_custom_path in sys.path:
                sys.path.remove(agent_custom_path)

    def _load_from_package(self, module_path: str, class_name: str) -> type:
        """Load class from installed package."""
        try:
            module = self._import_package_module(module_path)
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

    @staticmethod
    def _import_package_module(module_path: str) -> Any:
        """Import a dotted module, retrying once after locating its installed package."""
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            top = module_path.split(".", 1)[0]
            if e.name != top or not ensure_module_importable(module_path):
                raise
            importlib.invalidate_caches()
            return importlib.import_module(module_path)

    def clear_cache(self) -> None:
        """Clear loaded module cache."""
        self._loaded_modules.clear()


def load_custom_module(
    agent_path: Path,
    module_path: str,
    class_name: str,
    module_type: str = "custom",
    options: dict[str, Any] | None = None,
) -> Any:
    """Load and instantiate one custom component without retaining a loader."""
    loader = ModuleLoader(agent_path)
    return loader.load_instance(module_path, class_name, module_type, options)
