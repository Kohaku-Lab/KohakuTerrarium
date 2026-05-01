"""Sandbox runtime configuration types."""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.modules.sandbox.parse import parse_profile
from kohakuterrarium.modules.sandbox.profile import SandboxProfile


@dataclass(slots=True)
class SandboxConfig:
    """Runtime-adjustable sandbox configuration."""

    enabled: bool = True
    audit: bool = False
    backend: str = "auto"
    profile: SandboxProfile = field(default_factory=lambda: parse_profile("WORKSPACE"))
    fs_read: str | None = None
    fs_write: str | None = None
    network: str | None = None
    syscall: str | None = None
    env: str | None = None
    tmp: str | None = None
    fs_deny: tuple[str, ...] = field(default_factory=tuple)
    network_allowlist: tuple[str, ...] = field(default_factory=tuple)
    blocked_tools: tuple[str, ...] = field(default_factory=tuple)
    allow_tools: tuple[str, ...] = field(default_factory=tuple)
    tool_overrides: dict[str, SandboxProfile] = field(default_factory=dict)

    def effective_cap(self) -> SandboxProfile:
        """Return the creature/session cap after scalar option overrides."""
        values: dict[str, Any] = {
            "name": self.profile.name,
            "fs_read": self.fs_read or self.profile.fs_read,
            "fs_write": self.fs_write or self.profile.fs_write,
            "network": self.network or self.profile.network,
            "syscall": self.syscall or self.profile.syscall,
            "env": self.env or self.profile.env,
            "tmp": self.tmp or self.profile.tmp,
            "risk": self.profile.risk,
            "fs_deny": tuple(self.profile.fs_deny) + tuple(self.fs_deny),
            "network_allowlist": (
                tuple(self.network_allowlist) or tuple(self.profile.network_allowlist)
            ),
        }
        return SandboxProfile.from_dict(values)
