"""Persistent browser profiles claimed by browsers this process launches.

Two Chromium processes on one user data directory corrupt it, so a launch on
a persistent profile first claims it: the claim waits, bounded, until every
browser that ran on that profile is known to have ended its processes, and
otherwise refuses. Throwaway profiles are never claimed.
"""

import asyncio
import os
from typing import Protocol

from kohakuterrarium.browser.errors import BrowserUnavailableError

RELEASE_WAIT_S = 5.0
POLL_S = 0.05


class Exiting(Protocol):
    """A launched browser, as far as its profile claim needs it."""

    def exited(self) -> bool: ...


class ProfileClaim:
    """One launch's hold on a profile: pending, held by a browser, or dropped."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.browser: Exiting | None = None
        self._dropped = False

    def held_by(self, browser: Exiting) -> None:
        """The launch may have started ``browser``; the profile stays claimed until it exits."""
        self.browser = browser

    def drop(self) -> None:
        """The launch started no browser; the profile is free again."""
        self._dropped = True
        _forget(self)

    def released(self) -> bool:
        """Whether this claim no longer keeps the profile."""
        if self.browser is None:
            return self._dropped
        return self.browser.exited()


_claims: dict[str, list[ProfileClaim]] = {}


def profile_key(profile_dir: str) -> str:
    """One spelling per directory: absolute, links resolved, case folded where the OS does."""
    return os.path.normcase(os.path.realpath(profile_dir))


async def claim_profile(profile_dir: str) -> ProfileClaim:
    """Claim ``profile_dir`` for one launch, waiting up to ``RELEASE_WAIT_S`` for it.

    Raises ``BrowserUnavailableError`` while an earlier browser on the same
    profile is not known to have exited (or its launch is still running).
    """
    key = profile_key(profile_dir)
    deadline = asyncio.get_running_loop().time() + RELEASE_WAIT_S
    while True:
        held = [claim for claim in _claims.get(key, []) if not claim.released()]
        if not held:
            claim = ProfileClaim(key)
            _claims[key] = [claim]
            return claim
        _claims[key] = held
        if asyncio.get_running_loop().time() >= deadline:
            raise BrowserUnavailableError(
                f"the browser profile {profile_dir} is in use by a browser that could "
                "not be confirmed to have exited, and two browsers on one profile "
                "corrupt it; try again once that browser has exited, or end its "
                "processes"
            )
        await asyncio.sleep(POLL_S)


def forget_if_released(claim: ProfileClaim) -> None:
    """Drop ``claim`` from the books once it no longer keeps its profile."""
    if claim.released():
        _forget(claim)


def _forget(claim: ProfileClaim) -> None:
    rest = [c for c in _claims.get(claim.key, []) if c is not claim]
    if rest:
        _claims[claim.key] = rest
    else:
        _claims.pop(claim.key, None)
