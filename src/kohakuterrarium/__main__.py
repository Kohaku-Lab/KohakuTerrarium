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


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _is_briefcase_bundle() -> bool:
    """Detect whether we are running inside a Briefcase-packaged app.

    Briefcase Windows bundles use an embedded Python distribution which places
    a ``python3XX._pth`` file next to the stub exe.  Normal Python installs
    never have a ``._pth`` file beside ``sys.executable``.
    """
    exe_dir = Path(sys.executable).resolve().parent
    return any(exe_dir.glob("python3*._pth"))


if __name__ == "__main__":
    _configure_utf8_stdio()
    if _is_briefcase_bundle() and len(sys.argv) <= 1:
        from kohakuterrarium.__briefcase__ import main

        main()
    else:
        from kohakuterrarium.cli import main

        sys.exit(main())
