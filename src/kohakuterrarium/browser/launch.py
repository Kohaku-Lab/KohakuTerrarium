"""Launch planning for the browser: executables, arguments, proxy, profile, viewport.

Pure data with no Playwright import; ``driver`` turns a :class:`LaunchPlan`
into Playwright calls with :meth:`LaunchPlan.launch_kwargs` and
:meth:`LaunchPlan.context_kwargs`.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.browser.errors import BrowserUnavailableError
from kohakuterrarium.browser.options import BrowserOptions

LAUNCH_TIMEOUT_S = 60.0
LOOPBACK_BYPASS_ARG = "--proxy-bypass-list=<-loopback>"
NETWORK_ARGS = (
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    LOOPBACK_BYPASS_ARG,
)
SYSTEM_CHANNELS = ("chrome", "msedge")


@dataclass(frozen=True)
class Candidate:
    """One browser to try: an explicit executable, a Playwright channel, or bundled Chromium."""

    label: str
    executable_path: str | None = None
    channel: str | None = None


@dataclass(frozen=True)
class LaunchPlan:
    """Everything needed to start one browser, in the order executables are tried.

    ``profile_dir`` of ``None`` means a throwaway profile (a plain launch plus
    a fresh context); otherwise a persistent context on that directory.
    """

    candidates: tuple[Candidate, ...]
    headless: bool
    args: tuple[str, ...]
    proxy_server: str
    viewport: tuple[int, int]
    profile_dir: str | None = None
    device_scale_factor: float = 1.0
    timeout_s: float = LAUNCH_TIMEOUT_S

    @property
    def persistent(self) -> bool:
        return self.profile_dir is not None

    def launch_kwargs(self, candidate: Candidate) -> dict[str, Any]:
        """Keyword arguments for ``chromium.launch`` with ``candidate``."""
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": list(self.args),
            "proxy": {"server": self.proxy_server},
            "handle_sigint": False,
            "timeout": self.timeout_s * 1000,
        }
        if candidate.executable_path:
            kwargs["executable_path"] = candidate.executable_path
        if candidate.channel:
            kwargs["channel"] = candidate.channel
        return kwargs

    def context_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``new_context`` (and the context half of a persistent launch)."""
        width, height = self.viewport
        return {
            "viewport": {"width": width, "height": height},
            "device_scale_factor": self.device_scale_factor,
            "accept_downloads": True,
            "service_workers": "allow",
        }


BUNDLED = Candidate("bundled chromium")


def candidates_for(
    options: BrowserOptions, bundled_only: bool = False
) -> tuple[Candidate, ...]:
    """Executables to try: an explicit path or channel alone, else bundled then system browsers.

    ``bundled_only`` gives the bundled Chromium alone, whatever the options name.
    """
    if bundled_only:
        return (BUNDLED,)
    if options.executable_path:
        return (Candidate("executable_path", executable_path=options.executable_path),)
    if options.channel:
        return (Candidate(f"channel {options.channel}", channel=options.channel),)
    system = tuple(Candidate(f"system {c}", channel=c) for c in SYSTEM_CHANNELS)
    return (BUNDLED,) + system


def plan_launch(
    options: BrowserOptions, proxy_server: str, *, bundled_only: bool = False
) -> LaunchPlan:
    """The launch plan for ``options`` with all traffic sent to ``proxy_server``."""
    return LaunchPlan(
        candidates=candidates_for(options, bundled_only),
        headless=options.headless,
        args=NETWORK_ARGS,
        proxy_server=proxy_server,
        viewport=options.viewport,
        profile_dir=options.profile_dir,
    )


def check_event_loop(
    loop: asyncio.AbstractEventLoop | None = None, platform: str = sys.platform
) -> None:
    """Raise ``BrowserUnavailableError`` when the loop cannot start Playwright's driver.

    On Windows only the Proactor loop supports subprocesses.
    """
    if platform != "win32":
        return
    current = loop or asyncio.get_running_loop()
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    if proactor is None or not isinstance(current, proactor):
        raise BrowserUnavailableError(
            "the browser needs the Proactor event loop on Windows, but this process "
            f"runs {type(current).__name__}; remove any WindowsSelectorEventLoopPolicy "
            "set before the agent starts"
        )
