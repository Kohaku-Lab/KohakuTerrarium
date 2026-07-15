"""KohakuTerrarium entry point.

Dispatch ``python -m kohakuterrarium`` to the Briefcase launcher when the
embedded runtime has no CLI arguments; otherwise run the normal CLI.
"""

import sys
from pathlib import Path

from kohakuterrarium.utils.logging import configure_utf8_stdio


def _is_briefcase_bundle() -> bool:
    """Detect Briefcase's embedded Python by its executable-adjacent ``._pth``."""
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
