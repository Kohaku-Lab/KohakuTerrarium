"""Per-call gates of the ``browser`` plugin: the sandbox link and the host-resources rule."""

from typing import Any

from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.terrarium.creature.host_resources import host_resources_allowed

NETWORK_POLICY = "network_policy"
CONFINEMENT_REFUSAL = (
    "the browser cannot start: the sandbox requires OS-level confinement "
    "(os_enforce=require) and Chromium cannot run under the sandbox's "
    "subprocess confinement"
)


class SandboxLink:
    """The sandbox's ``network_policy`` service as the browser proxy's external check.

    ``bind`` is called with every tool call's runtime services, so a sandbox
    that was disabled, re-enabled or reconfigured is followed from the next
    call on; ``check`` is what the proxy consults for every connection.
    """

    def __init__(self) -> None:
        self.policy: Any = None

    def bind(self, services: dict[str, Any] | None) -> bool:
        """Follow ``services``; whether the bound service changed."""
        policy = (services or {}).get(NETWORK_POLICY)
        changed = policy is not self.policy
        self.policy = policy
        return changed

    def check(self, host: str, port: int) -> str | None:
        """Why the sandbox refuses ``host:port``, or ``None`` to allow it."""
        policy = self.policy
        return None if policy is None else policy.check(host, port)

    @property
    def denies_all(self) -> bool:
        """Whether the bound sandbox denies all network access."""
        return getattr(self.policy, "mode", "allow") == "deny"

    @property
    def requires_confinement(self) -> bool:
        """Whether the bound sandbox demands kernel confinement for every process."""
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
