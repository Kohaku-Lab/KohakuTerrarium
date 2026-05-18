"""KohakuTerrarium launcher — thin wrapper that owns the runtime tree.

This package is the briefcase desktop bundle's *only* runtime entry
point. It manages versioned installs under
``~/.kohakuterrarium/runtime/versions/<X.Y.Z>/``, atomically swaps the
``runtime/active`` pointer, and ``os.execv``'s into the active
version's ``scripts/kt`` to replace itself with the real framework
process.

**Strict isolation rule.** Modules under
:mod:`kohakuterrarium.launcher` must NOT import from any other
``kohakuterrarium.*`` package. The launcher runs BEFORE any framework
code is on ``sys.path`` (first launch, recovery), and a missing
transitive import would crash the wrapper. A dep-graph guard test in
``tests/unit/`` enforces this.

The package ``__init__`` deliberately re-exports NOTHING so that
``import kohakuterrarium.launcher.<submodule>`` from any submodule's
top of file does not trigger a circular import chain through
``bootloader`` → ``update_runner`` → ``settings`` → … back to
``launcher.__init__``. Callers (``__main__``, ``__briefcase__``)
import :func:`bootloader.main` directly.

Wrapper-only dependencies (intentionally small):

- stdlib: ``argparse``, ``json``, ``os``, ``pathlib``, ``subprocess``,
  ``shutil``, ``hashlib``, ``urllib``, ``tarfile``, ``http.server``,
  ``threading``, ``logging``
- third-party: ``packaging`` (any), ``pywebview`` (splash UI — optional;
  Tk fallback if missing), ``zstandard`` (optional; ``.tar.gz`` fallback
  if missing)

Notably **not** required: ``pip``, ``venv``, ``ensurepip``, ``git``,
``installer``. The launcher never installs Python packages on the
user's machine — it downloads pre-built site-packages trees.
"""
