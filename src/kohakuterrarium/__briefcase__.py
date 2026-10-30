"""Launch the active framework release inside the Briefcase process.

Briefcase cannot hand off through ``PYTHONPATH`` or a Python subprocess because
its isolated runtime disables normal site loading and exposes an application
stub as ``sys.executable``. The entry point therefore redirects imports to the
selected release in process while retaining the bundled launcher modules.
"""

import os
import sys

from kohakuterrarium.launcher import interpreter_mode
from kohakuterrarium.launcher.bootloader import prepare


def main() -> int:
    """Prepare the active release and run its CLI in the Briefcase process.

    ``--kt-python`` short-circuits to interpreter mode so child processes can use
    this stub as ``python``.
    """
    os.environ[interpreter_mode.STUB_ENV] = sys.executable
    if interpreter_mode.requested(sys.argv):
        return interpreter_mode.run(sys.argv)

    result = prepare()
    if result.done:
        return result.exit_code
    if result.site_packages is None:
        return result.exit_code or 7

    # Launcher modules must survive because they own the active release selection.
    interpreter_mode.activate_release(str(result.site_packages))

    # Serving must stay in process because the Briefcase stub cannot run modules.
    os.environ["KT_LAUNCHER_EXEC"] = "1"

    # Import only after eviction so the CLI resolves from the selected release.
    from kohakuterrarium.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
