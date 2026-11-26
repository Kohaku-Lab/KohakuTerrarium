"""Pick the host's kernel confinement backend and wrap commands with it."""

import os
from dataclasses import dataclass
from pathlib import Path

import kohakuterrarium.builtins.plugins.sandbox.landlock as landlock
import kohakuterrarium.builtins.plugins.sandbox.seatbelt as seatbelt
from kohakuterrarium.modules.sandbox.profile import SandboxProfile

SEATBELT = "seatbelt"
LANDLOCK = "landlock"


@dataclass(frozen=True)
class ConfinedCommand:
    """A command ready to spawn, plus the descriptors its spawn must pass on.

    ``backend`` is ``None`` for a command left unconfined. ``grant_count`` is
    the number of Landlock write grants, ``None`` for other backends. The
    spawner passes ``pass_fds`` to the child and calls :meth:`close` after.
    """

    argv: list[str]
    backend: str | None = None
    pass_fds: tuple[int, ...] = ()
    grant_count: int | None = None

    def close(self) -> None:
        """Close this process's copies of ``pass_fds``."""
        for fd in self.pass_fds:
            os.close(fd)


def confinement_backend() -> str | None:
    """``seatbelt`` on macOS, ``landlock`` on Linux, ``None`` where neither works."""
    if seatbelt.seatbelt_available():
        return SEATBELT
    if landlock.landlock_available():
        return LANDLOCK
    return None


def confine_command(
    argv: list[str],
    backend: str,
    profile: SandboxProfile,
    workspace: Path,
    *,
    write_allow: tuple[str, ...] | list[str] = (),
) -> ConfinedCommand:
    """``argv`` wrapped so ``backend`` enforces ``profile``'s write axis."""
    match backend:
        case "seatbelt":
            text = seatbelt.build_write_profile(
                profile, workspace, write_allow=write_allow
            )
            return ConfinedCommand(seatbelt.confine_argv(argv, text), backend)
        case "landlock":
            grants = landlock.write_grants(profile, workspace, write_allow=write_allow)
            fd = landlock.grants_fd(grants)
            return ConfinedCommand(
                landlock.confine_argv(argv, fd),
                backend,
                pass_fds=(fd,),
                grant_count=len(grants),
            )
        case _:
            raise ValueError(f"unknown confinement backend: {backend!r}")
