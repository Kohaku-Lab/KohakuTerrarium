"""Launch the active framework release inside the Briefcase process.

Briefcase cannot hand off through ``PYTHONPATH`` or a Python subprocess because
its isolated runtime disables normal site loading and exposes an application
stub as ``sys.executable``. The entry point therefore redirects imports to the
selected release in process while retaining the bundled launcher modules.
"""

import os
import sys

from kohakuterrarium.launcher.bootloader import prepare


def _swap_to_version_tree(site_packages: str) -> None:
    """Redirect framework imports to a release tree while retaining launcher state."""
    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)
    # Launcher modules must survive because they own the active release selection.
    for mod in list(sys.modules):
        if mod == "kohakuterrarium" or (
            mod.startswith("kohakuterrarium.")
            and not mod.startswith("kohakuterrarium.launcher")
        ):
            del sys.modules[mod]


def main() -> int:
    """Prepare the active release and run its CLI in the Briefcase process."""
    result = prepare()
    if result.done:
        return result.exit_code
    if result.site_packages is None:
        return result.exit_code or 7

    _swap_to_version_tree(str(result.site_packages))

    # Serving must stay in process because the Briefcase stub cannot run modules.
    os.environ["KT_LAUNCHER_EXEC"] = "1"

    # Import only after eviction so the CLI resolves from the selected release.
    from kohakuterrarium.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
