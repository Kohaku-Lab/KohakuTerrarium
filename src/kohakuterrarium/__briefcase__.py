"""Briefcase desktop bundle entry point.

The Briefcase artifact ships a launcher (this file's
:func:`launcher.bootloader.prepare`) on top of a bundled Python +
pre-installed ``app_packages``. On every launch we:

1. Drive ``launcher.prepare()`` — first_install / maybe_update, splash
   UI, atomic pointer swap. Returns the active version tree path.
2. **Switch sys.path to the version tree** and clear the briefcase-
   bundled ``kohakuterrarium.*`` from ``sys.modules``, then import
   ``kohakuterrarium.cli`` — that import now resolves to the
   version-tree copy.

Why in-process ``sys.path`` swap instead of ``os.execv``:

- Briefcase Windows shells ship ``python313._pth`` with
  ``import site`` commented out → ``PYTHONPATH`` is ignored, so
  ``os.execv`` with a tweaked env can't redirect the framework
  import to the version tree.
- ``sys.executable`` on briefcase is the stub binary itself
  (``KohakuTerrarium.exe``), not a plain Python. Spawning it with
  ``-m ...`` would re-enter the launcher, infinite-loop.

Self-update therefore works as follows: the launcher writes the
``active`` pointer at update-time; on the user's next relaunch the
briefcase stub re-runs this module, ``prepare()`` reads the new
pointer, and the in-process import resolves the framework code from
the new version tree. The briefcase shell installer itself doesn't
need to be re-downloaded.
"""

import os
import sys

from kohakuterrarium.launcher.bootloader import prepare


def _swap_to_version_tree(site_packages: str) -> None:
    """Prepend the version's ``site-packages/`` to ``sys.path`` and
    evict the briefcase-bundled ``kohakuterrarium.*`` modules so the
    framework re-imports from the version tree.

    Keeps ``kohakuterrarium.launcher.*`` intact — that's still the
    code we want for ``self-update`` etc. after the framework is up.
    The framework's API layer imports ``launcher`` lazily anyway.
    """
    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)
    # Evict everything except the launcher subpackage so the
    # version-tree's framework code gets fresh imports.
    for mod in list(sys.modules):
        if mod == "kohakuterrarium" or (
            mod.startswith("kohakuterrarium.")
            and not mod.startswith("kohakuterrarium.launcher")
        ):
            del sys.modules[mod]


def main() -> int:
    result = prepare()
    if result.done:
        return result.exit_code
    if result.site_packages is None:
        return result.exit_code or 7

    _swap_to_version_tree(str(result.site_packages))

    # Sentinel the framework's serving layer reads to skip its
    # CLI-style "detach a subprocess" path — see
    # ``kohakuterrarium.serving.web._is_briefcase_runtime``. On
    # briefcase, the stub binary IS the GUI process and subprocess-
    # detaching via ``sys.executable -m ...`` doesn't work (the stub
    # routes -m straight into the kt CLI parser, child dies, parent
    # exits — that was the dev5 "runs for a while then turns off"
    # failure mode).
    os.environ["KT_LAUNCHER_EXEC"] = "1"

    # The launcher's already-imported ``kohakuterrarium`` package was
    # just evicted — re-import the *framework's* version (now first in
    # sys.path) and dispatch into its CLI entry.
    from kohakuterrarium.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
