"""``python -m kohakuterrarium.cli`` entry point.

Used by the launcher's ``os.execv`` post-first-install — bypasses
``kohakuterrarium.__main__`` (which contains the briefcase-bundle
detector that would otherwise re-enter the launcher and loop).
"""

import sys

from kohakuterrarium.cli import main

if __name__ == "__main__":
    sys.exit(main())
