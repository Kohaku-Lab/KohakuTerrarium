"""Briefcase desktop bundle entry point.

The Briefcase artifact is a thin wrapper around a managed venv that
owns the framework install — see ``src/kohakuterrarium/launcher/`` and
``plans/1.5.0-roadmap/06-app-update/`` for the design.  This module
delegates to the launcher's ``main`` and intentionally imports nothing
from the framework itself; the launcher is responsible for creating /
refreshing the venv and ``exec``-ing the framework's ``kt`` entry once
the venv is ready.
"""

import sys

from kohakuterrarium.launcher.bootloader import main

if __name__ == "__main__":
    sys.exit(main())
