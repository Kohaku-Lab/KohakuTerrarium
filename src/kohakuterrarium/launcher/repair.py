"""Reinstall the release shipped with the shell, without needing a working release.

The shell runs this with its own interpreter while the backend is stopped. Progress
goes to stdout as JSON lines; the last line is the result.
"""

import json
import sys

from kohakuterrarium.launcher.sidecar import emit_progress
from kohakuterrarium.launcher.update_runner import reset


def main() -> int:
    result = reset(emit_progress)
    print(
        json.dumps({"ok": result.ok, "version": result.version, "error": result.error}),
        flush=True,
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
