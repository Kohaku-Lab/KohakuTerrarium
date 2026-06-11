"""Package management — install / list / resolve ``@<pkg>`` references.

Public façade
-------------

The names below are importable directly from ``kohakuterrarium.packages``
(resolved lazily via module ``__getattr__`` so importing a submodule
does not drag in the marketplace / installer stack)::

    from kohakuterrarium import packages

    packages.ensure("@kt-biome")                  # idempotent install
    path = packages.resolve_package_path("@kt-biome/creatures/swe")
    for pkg in packages.list_packages():
        print(pkg["name"], pkg["version"])

- Install lifecycle: :func:`ensure`, :func:`install_package_spec`,
  :func:`install_package`, :func:`update_package`,
  :func:`uninstall_package`.
- Reference resolution: :func:`is_package_ref`,
  :func:`resolve_package_path`, :func:`resolve_any_path`.
- Enumeration / layout: :func:`list_packages`,
  :func:`get_package_modules`, :func:`packages_dir`,
  :func:`get_package_root`, :func:`find_package_root_for_path`.
- Manifest-slot resolvers: :func:`resolve_package_tool`,
  :func:`resolve_package_io`, :func:`resolve_package_trigger`,
  :func:`resolve_package_command`, :func:`resolve_package_user_command`,
  :func:`resolve_package_prompt`, :func:`resolve_package_skills`,
  :func:`get_package_framework_hints`.
- Typed errors (re-exported from :mod:`kohakuterrarium.errors`):
  :class:`PackageError`, :class:`PackageRefError`,
  :class:`PackageNotInstalledError`, :class:`PackagePathNotFoundError`.

Internal layering
-----------------

This package is the storage / resolution layer for ``@<pkg>/<path>``
references and the metadata behind the manifest scanners. It is
imported by ``bootstrap``, ``core/loader``, ``compose``, and
``terrarium/recipe`` — all of which are below the ``studio/`` tier.
**Framework-internal importers must keep importing the specific
submodule** (``from kohakuterrarium.packages.resolve import ...``) so
the dependency edges in the graph reflect the real coupling; the lazy
façade exists for user code.

Submodules:

- :mod:`.locations` — filesystem layout (``packages_dir``, link files, root lookup).
- :mod:`.manifest` — manifest IO (``kohaku.yaml`` loader, validation, deps install).
- :mod:`.walk` — full-package enumeration (``list_packages``, ``get_package_modules``).
- :mod:`.resolve` — ``@pkg/path`` and per-kind resolvers (tools / io / triggers).
- :mod:`.slots` — extra manifest slots (skills / commands / user_commands / prompts).
- :mod:`.install` — install / update / uninstall / ensure.
- :mod:`.marketplace` — ``@name`` install-spec resolution against TerrariumMarket.
"""

import importlib

# name → module that defines it. Resolution is deferred to first
# attribute access (PEP 562) so ``import kohakuterrarium.packages.resolve``
# from the config loaders does not eagerly import the installer /
# marketplace stack (httpx et al.).
_EXPORTS = {
    # locations
    "packages_dir": "kohakuterrarium.packages.locations",
    "get_package_root": "kohakuterrarium.packages.locations",
    "find_package_root_for_path": "kohakuterrarium.packages.locations",
    # resolve
    "is_package_ref": "kohakuterrarium.packages.resolve",
    "resolve_package_path": "kohakuterrarium.packages.resolve",
    "resolve_any_path": "kohakuterrarium.packages.resolve",
    "resolve_package_tool": "kohakuterrarium.packages.resolve",
    "resolve_package_io": "kohakuterrarium.packages.resolve",
    "resolve_package_trigger": "kohakuterrarium.packages.resolve",
    # slots
    "resolve_package_command": "kohakuterrarium.packages.slots",
    "resolve_package_user_command": "kohakuterrarium.packages.slots",
    "resolve_package_prompt": "kohakuterrarium.packages.slots",
    "resolve_package_skills": "kohakuterrarium.packages.slots",
    # walk
    "list_packages": "kohakuterrarium.packages.walk",
    "get_package_modules": "kohakuterrarium.packages.walk",
    # install
    "ensure": "kohakuterrarium.packages.install",
    "install_package": "kohakuterrarium.packages.install",
    "install_package_spec": "kohakuterrarium.packages.install",
    "update_package": "kohakuterrarium.packages.install",
    "uninstall_package": "kohakuterrarium.packages.install",
    # manifest
    "get_package_framework_hints": "kohakuterrarium.packages.manifest",
    # typed errors
    "PackageError": "kohakuterrarium.errors",
    "PackageRefError": "kohakuterrarium.errors",
    "PackageNotInstalledError": "kohakuterrarium.errors",
    "PackagePathNotFoundError": "kohakuterrarium.errors",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # cache — next access skips __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
