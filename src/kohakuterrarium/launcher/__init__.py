"""KohakuTerrarium launcher — thin wrapper that owns the framework venv.

This package is the Briefcase desktop bundle's *only* runtime entry
point.  It bootstraps a user-owned virtualenv under
``~/.kohakuterrarium/runtime/venv/`` (per the user's
``app-settings.json``), installs the framework into it from the
configured source (PyPI / git / local / bundled wheels), and ``exec``s
the venv's ``python -m kohakuterrarium`` to replace itself with the
real framework process.

**Strict isolation rule.**  Modules under
:mod:`kohakuterrarium.launcher` must NOT import from any other
``kohakuterrarium.*`` package.  The launcher runs BEFORE the
framework's site-packages have been installed in some scenarios (clean
first launch, recovery), and a missing transitive import would crash
the wrapper.  A dep-graph guard test in ``tests/unit/`` enforces this.

The package ``__init__`` deliberately re-exports NOTHING (no
``from .bootloader import main`` here) so that ``import
kohakuterrarium.launcher.<submodule>`` from any submodule's top of
file does not trigger a circular import chain through ``bootloader``
→ ``update_runner`` → ``settings`` → … back to ``launcher.__init__``.
Callers (``__main__``, ``__briefcase__``) import :func:`bootloader.main`
directly.

Wrapper-only dependencies (intentionally small):

- stdlib: ``argparse``, ``json``, ``os``, ``pathlib``, ``subprocess``,
  ``venv``, ``http.server``, ``threading``, ``shutil``, ``logging``
- third-party: ``pip`` (any version), ``packaging`` (any), ``pywebview``
  (splash UI — optional; Tk fallback if missing)
"""
