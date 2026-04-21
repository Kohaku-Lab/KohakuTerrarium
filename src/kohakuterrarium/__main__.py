"""KohakuTerrarium entry point.

When executed as ``python -m kohakuterrarium``, this module decides which path
to take:

* **Briefcase bundle** — the stub launcher sets no CLI args and
  ``BRIEFCASE_MAIN_MODULE`` is absent, so we detect the bundle via the
  embedded Python ``python3XX._pth`` marker and call ``__briefcase__.main()``
  directly.
* **Normal CLI** — falls through to ``cli.main()`` as usual.
"""

import sys
from pathlib import Path

from kohakuterrarium.utils.logging import configure_utf8_stdio


def _is_briefcase_bundle() -> bool:
    """Detect whether we are running inside a Briefcase-packaged app.

    Briefcase Windows bundles use an embedded Python distribution which places
    a ``python3XX._pth`` file next to the stub exe.  Normal Python installs
    never have a ``._pth`` file beside ``sys.executable``.
    """
    exe_dir = Path(sys.executable).resolve().parent
    return any(exe_dir.glob("python3*._pth"))


def main() -> int:
    configure_utf8_stdio(log=True)
    if _is_briefcase_bundle() and len(sys.argv) <= 1:
        from kohakuterrarium.__briefcase__ import main as briefcase_main

        briefcase_main()
        return 0

    from kohakuterrarium.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
