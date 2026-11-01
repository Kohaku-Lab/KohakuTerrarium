"""Pick the host's kernel confinement backend and wrap commands with it."""

from pathlib import Path

import kohakuterrarium.builtins.plugins.sandbox.landlock as landlock
import kohakuterrarium.builtins.plugins.sandbox.seatbelt as seatbelt
from kohakuterrarium.modules.sandbox.profile import SandboxProfile

SEATBELT = "seatbelt"
LANDLOCK = "landlock"


def confinement_backend() -> str | None:
    """``seatbelt`` on macOS, ``landlock`` on Linux, ``None`` where neither works."""
    if seatbelt.seatbelt_available():
        return SEATBELT
    if landlock.landlock_available():
        return LANDLOCK
    return None


def confine_argv(
    argv: list[str],
    backend: str,
    profile: SandboxProfile,
    workspace: Path,
    *,
    write_allow: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """``argv`` wrapped so ``backend`` enforces ``profile``'s write axis."""
    match backend:
        case "seatbelt":
            text = seatbelt.build_write_profile(
                profile, workspace, write_allow=write_allow
            )
            return seatbelt.confine_argv(argv, text)
        case "landlock":
            grants = landlock.write_grants(profile, workspace, write_allow=write_allow)
            return landlock.confine_argv(argv, grants)
        case _:
            raise ValueError(f"unknown confinement backend: {backend!r}")
