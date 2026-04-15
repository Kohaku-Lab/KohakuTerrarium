"""KohakuTerrarium CLI entry point."""

import sys

from kohakuterrarium.cli import main


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


if __name__ == "__main__":
    _configure_utf8_stdio()
    sys.exit(main())
