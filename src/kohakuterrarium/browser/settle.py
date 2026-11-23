"""Waiting for a page to settle after an action: navigation grace, then DOM quiescence.

Never waits for network idle. A page that keeps mutating hits the cap and
returns; a navigation that destroys the script context mid-wait is waited
out and the quiet check retried.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from kohakuterrarium.browser.errors import (
    ContextDestroyedError,
    DriverDisconnectedError,
    DriverError,
    DriverTimeoutError,
    PageClosedError,
)
from kohakuterrarium.browser.protocol import PageDriver

NAV_GRACE_S = 0.15
QUIET_MS = 150
CAP_S = 2.0
ATTEMPTS = 3
MIN_STEP_S = 0.05

QUIET_JS = """([quietMs, capMs]) => new Promise((resolve) => {
  const start = performance.now();
  let last = start;
  const observer = new MutationObserver(() => { last = performance.now(); });
  observer.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  const tick = () => {
    const now = performance.now();
    if (now - last >= quietMs) { observer.disconnect(); resolve("quiet"); }
    else if (now - start >= capMs) { observer.disconnect(); resolve("cap"); }
    else setTimeout(tick, 25);
  };
  setTimeout(tick, 25);
})"""


@dataclass(frozen=True)
class Settled:
    """How settling ended: ``quiet``, ``cap`` or ``error``, and whether a navigation started."""

    reason: str
    navigated: bool
    elapsed: float


async def settle(
    page: PageDriver,
    navigation: asyncio.Event,
    *,
    grace: float = NAV_GRACE_S,
    quiet_ms: int = QUIET_MS,
    cap: float = CAP_S,
    clock: Callable[[], float] = time.monotonic,
) -> Settled:
    """Wait for ``page`` to settle; ``navigation`` is set by the session when one starts.

    Only a lost browser or a closed page raises; every other failure ends
    the wait with reason ``error``.
    """
    start = clock()

    def left() -> float:
        return cap - (clock() - start)

    try:
        await asyncio.wait_for(navigation.wait(), min(grace, max(left(), 0)))
    except asyncio.TimeoutError:
        pass
    navigated = navigation.is_set()
    reason = "cap"
    try:
        if navigated:
            await _load(page, left())
        for _ in range(ATTEMPTS):
            remaining = left()
            if remaining <= 0:
                break
            try:
                reason = await asyncio.wait_for(
                    page.evaluate(
                        QUIET_JS,
                        [quiet_ms, int(remaining * 1000)],
                        timeout=remaining + MIN_STEP_S,
                    ),
                    remaining + MIN_STEP_S,
                )
                break
            except ContextDestroyedError:
                navigated = True
                await _load(page, left())
    except (DriverDisconnectedError, PageClosedError):
        raise
    except (asyncio.TimeoutError, DriverTimeoutError):
        reason = "cap"
    except DriverError:
        reason = "error"
    if reason not in ("quiet", "cap"):
        reason = "error"
    return Settled(reason, navigated, clock() - start)


async def _load(page: PageDriver, remaining: float) -> None:
    if remaining <= 0:
        return
    try:
        await asyncio.wait_for(page.wait_for_load(remaining), remaining + MIN_STEP_S)
    except (asyncio.TimeoutError, ContextDestroyedError):
        pass
    except (DriverDisconnectedError, PageClosedError):
        raise
    except DriverError:
        pass
