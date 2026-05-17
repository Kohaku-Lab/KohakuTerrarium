"""Legacy-bundle detection + one-shot migration banner support.

The 1.5.0 release ships two Briefcase artifacts side-by-side:

- **Legacy bundle** — frozen full framework (the only shape before 1.5).
- **Wrapper bundle** — the thin launcher introduced by topic 06.

Existing legacy-bundle installs see a one-shot Admin → Updates banner
prompting "switch to the new auto-updating bundle (one-time
download)."  Once dismissed (or once the user has migrated), the
banner doesn't reappear.  This module provides the probe the API uses
to decide whether to render the banner.
"""

import sys
from pathlib import Path

from kohakuterrarium.launcher.paths import wrapper_marker_path


def is_wrapper_install() -> bool:
    """Return True when this process runs inside a wrapper-managed venv.

    The wrapper drops ``.kt-wrapper-marker`` at the root of the venv it
    creates; the running interpreter must be that venv's Python for the
    marker to be authoritative.  See :func:`paths.wrapper_marker_path`.
    """
    marker = wrapper_marker_path()
    if not marker.is_file():
        return False
    # The sentinel could exist from a prior wrapper install while THIS
    # interpreter is something else (system python, dev venv).  Cross-
    # check that ``sys.executable`` is under the marker's parent venv.
    try:
        venv_root = marker.parent.resolve()
        this_py = Path(sys.executable).resolve()
    except OSError:
        return False
    try:
        this_py.relative_to(venv_root)
    except ValueError:
        return False
    return True


def is_legacy_bundle() -> bool:
    """Best-effort: did the user launch a frozen full-framework Briefcase
    bundle (the pre-1.5.0 shape)?

    Heuristic: NOT a wrapper install, AND the interpreter sits under a
    Briefcase-style ``app_packages`` layout.  Briefcase puts each
    bundle's Python under
    ``.../<formal_name>.app/Contents/Resources/app_packages/`` on macOS,
    ``.../<formal_name>/app/`` on Windows, and a similar layout on
    Linux AppImages.  The shared marker across all three is the
    ``app_packages`` substring.

    The wrapper bundle does NOT have ``app_packages`` in the runtime
    interpreter path because it runs the user-owned venv's Python after
    ``os.execv`` (see :func:`bootloader.main`).
    """
    if is_wrapper_install():
        return False
    exe = sys.executable.replace("\\", "/").lower()
    return "/app_packages/" in exe or exe.endswith("/app_packages")


__all__ = ["is_legacy_bundle", "is_wrapper_install"]
