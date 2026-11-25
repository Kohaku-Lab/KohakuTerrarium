"""Per-call gates of the ``browser`` plugin: the sandbox link and the host-resources rule."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.terrarium.creature.host_resources import host_resources_allowed

NETWORK_POLICY = "network_policy"
WRITE_POLICY = "write_policy"
CONFINEMENT_REFUSAL = (
    "the browser cannot start: the sandbox requires OS-level confinement "
    "(os_enforce=require) and Chromium cannot run under the sandbox's "
    "subprocess confinement"
)

Services = dict[str, Any]


class SandboxLink:
    """The sandbox's policy services as the browser's network and file-write checks.

    Every answer reads the services in force at that moment: ``live``
    returns the host's current runtime services, or ``None`` when no host is
    reachable; then the services last given to ``bind`` stand in. ``check``
    is what the proxy consults for every connection and every re-judge;
    ``check_write`` judges a download path.
    """

    def __init__(self, live: Callable[[], Services | None] | None = None) -> None:
        self._live = live
        self._bound: Services = {}

    def bind(self, services: Services | None) -> None:
        """Keep one tool call's ``services`` for when no host is reachable."""
        self._bound = dict(services or {})

    def services(self) -> Services:
        """The runtime services in force now."""
        live = self._live() if self._live is not None else None
        return self._bound if live is None else live

    @property
    def policy(self) -> Any:
        """The sandbox's ``network_policy`` service now; ``None`` without a sandbox."""
        return self.services().get(NETWORK_POLICY)

    def check(self, host: str, port: int) -> str | None:
        """Why the sandbox refuses ``host:port``, or ``None`` to allow it."""
        policy = self.policy
        return None if policy is None else policy.check(host, port)

    def check_write(self, path: Path) -> str | None:
        """Why the sandbox refuses writing ``path``, or ``None`` to allow it."""
        policy = self.services().get(WRITE_POLICY)
        return None if policy is None else policy.check(path)

    @property
    def requires_confinement(self) -> bool:
        """Whether the sandbox demands kernel confinement for every process."""
        return bool(getattr(self.policy, "requires_os_confinement", False))


def host_resource_refusal(options: BrowserOptions, source: Any) -> str | None:
    """Why ``options`` are refused for the creature behind ``source``, or ``None``.

    Where host resources are off, only a headless browser with a throwaway
    profile, the bundled Chromium and public addresses is allowed.
    """
    if host_resources_allowed(source):
        return None
    used = [
        label
        for label, on in (
            ("headless: false", not options.headless),
            ("profile_dir", options.profile_dir is not None),
            ("executable_path", options.executable_path is not None),
            ("channel", options.channel is not None),
            ("allow_loopback", options.allow_loopback),
            ("allow_private", options.allow_private),
        )
        if on
    ]
    if not used:
        return None
    return (
        "this creature may not use host resources here, so the browser runs only "
        "headless with a throwaway profile, the bundled Chromium and public "
        "addresses; refused options: " + ", ".join(used)
    )
