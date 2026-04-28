"""Low-tier package management — install/list/resolve installed kt packages.

This package is the storage / resolution layer for ``@<pkg>/<path>``
references and the metadata behind the four manifest scanners. It is
imported by ``bootstrap``, ``core/loader``, ``compose``, and
``terrarium/recipe`` — all of which are below the ``studio/`` tier and
therefore cannot depend on it.

Submodules:

- :mod:`.locations` — filesystem layout (``PACKAGES_DIR``, link files, root lookup).
- :mod:`.manifest` — manifest IO (``kohaku.yaml`` loader, validation, deps install).
- :mod:`.walk` — full-package enumeration (``list_packages``, ``get_package_modules``).
- :mod:`.resolve` — ``@pkg/path`` and per-kind resolvers (tools / io / triggers).
- :mod:`.install` — install / update / uninstall.
- :mod:`.slots` — extra manifest slots (skills / commands / user_commands / prompts).

Importers must reach for the specific submodule — this ``__init__`` is
intentionally empty (no re-exports) so the dependency edges in the
graph reflect the real coupling.
"""
