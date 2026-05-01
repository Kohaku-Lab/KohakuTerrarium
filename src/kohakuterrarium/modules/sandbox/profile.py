"""Sandbox profile contract.

Profiles are declarative capability envelopes. They are intentionally small,
frozen, and dependency-light so they can be imported by core, builtins, plugins,
and third-party tools without pulling runtime machinery with them.
"""

from dataclasses import dataclass, field
from typing import Any

DEFAULT_DENY_PATHS: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.config/git",
    "~/.gnupg",
    "~/.bashrc",
    "~/.zshrc",
    "~/.profile",
    "~/.bash_profile",
    "~/.kohakuterrarium",
    "~/.config/kohakuterrarium",
    "/etc",
    "/private/etc",
    "%USERPROFILE%\\.ssh",
    "%APPDATA%\\KohakuTerrarium",
)

_FS_LEVELS: tuple[str, ...] = ("deny", "workspace", "broad")
_NETWORK_LEVELS: tuple[str, ...] = ("deny", "allow")
_SYSCALL_LEVELS: tuple[str, ...] = ("pure", "fs", "shell", "any")
_TMP_LEVELS: tuple[str, ...] = ("private", "shared")
_ENV_LEVELS: tuple[str, ...] = ("filtered", "inherit")
_RISK_LEVELS: tuple[str, ...] = ("safe", "low", "medium", "high")

_AXIS_LEVELS: dict[str, tuple[str, ...]] = {
    "fs_read": _FS_LEVELS,
    "fs_write": _FS_LEVELS,
    "network": _NETWORK_LEVELS,
    "syscall": _SYSCALL_LEVELS,
    "tmp": _TMP_LEVELS,
    "env": _ENV_LEVELS,
    "risk": _RISK_LEVELS,
}


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    """Declarative capability envelope for a tool or creature."""

    fs_read: str = "deny"
    fs_write: str = "deny"
    network: str = "deny"
    syscall: str = "pure"
    tmp: str = "private"
    env: str = "filtered"
    risk: str = "safe"
    fs_deny: tuple[str, ...] = field(default_factory=lambda: DEFAULT_DENY_PATHS)
    network_allowlist: tuple[str, ...] = field(default_factory=tuple)
    name: str = "custom"

    def __post_init__(self) -> None:
        for axis, levels in _AXIS_LEVELS.items():
            value = getattr(self, axis)
            if value not in levels:
                raise ValueError(
                    f"Invalid sandbox {axis} value {value!r}; "
                    f"expected one of: {', '.join(levels)}"
                )
        object.__setattr__(self, "fs_deny", tuple(str(p) for p in self.fs_deny))
        object.__setattr__(
            self,
            "network_allowlist",
            tuple(str(host).lower() for host in self.network_allowlist),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable JSON/YAML-friendly dict."""
        return {
            "name": self.name,
            "fs_read": self.fs_read,
            "fs_write": self.fs_write,
            "network": self.network,
            "syscall": self.syscall,
            "tmp": self.tmp,
            "env": self.env,
            "risk": self.risk,
            "fs_deny": list(self.fs_deny),
            "network_allowlist": list(self.network_allowlist),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SandboxProfile":
        """Build a profile from a partial dict."""
        return cls(
            fs_read=str(data.get("fs_read", "deny")),
            fs_write=str(data.get("fs_write", "deny")),
            network=str(data.get("network", "deny")),
            syscall=str(data.get("syscall", "pure")),
            tmp=str(data.get("tmp", "private")),
            env=str(data.get("env", "filtered")),
            risk=str(data.get("risk", "safe")),
            fs_deny=tuple(data.get("fs_deny", DEFAULT_DENY_PATHS) or ()),
            network_allowlist=tuple(data.get("network_allowlist") or ()),
            name=str(data.get("name", "custom")),
        )

    def with_overrides(self, **values: Any) -> "SandboxProfile":
        """Return a copy with selected fields replaced."""
        data = self.to_dict()
        data.update(values)
        return SandboxProfile.from_dict(data)


def narrower_axis(axis: str, left: str, right: str) -> str:
    """Return the narrower value on a lattice axis."""
    levels = _AXIS_LEVELS[axis]
    return left if levels.index(left) <= levels.index(right) else right


def wider_axis(axis: str, left: str, right: str) -> str:
    """Return the wider value on a lattice axis."""
    levels = _AXIS_LEVELS[axis]
    return left if levels.index(left) >= levels.index(right) else right


def risk_max(left: str, right: str) -> str:
    """Return the more dangerous risk level."""
    return wider_axis("risk", left, right)


def profile_intersection(
    left: SandboxProfile,
    right: SandboxProfile,
    *,
    name: str = "intersection",
) -> SandboxProfile:
    """Intersect two profiles, keeping the narrower capability on each axis."""
    return SandboxProfile(
        fs_read=narrower_axis("fs_read", left.fs_read, right.fs_read),
        fs_write=narrower_axis("fs_write", left.fs_write, right.fs_write),
        network=narrower_axis("network", left.network, right.network),
        syscall=narrower_axis("syscall", left.syscall, right.syscall),
        tmp=narrower_axis("tmp", left.tmp, right.tmp),
        env=narrower_axis("env", left.env, right.env),
        risk=risk_max(left.risk, right.risk),
        fs_deny=tuple(sorted(set(left.fs_deny) | set(right.fs_deny))),
        network_allowlist=_intersect_network_allowlist(
            left.network_allowlist,
            right.network_allowlist,
        ),
        name=name,
    )


def _intersect_network_allowlist(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> tuple[str, ...]:
    """Intersect exact-host allowlists; empty means unrestricted allow."""
    if not left:
        return tuple(sorted(set(right)))
    if not right:
        return tuple(sorted(set(left)))
    return tuple(sorted(set(left) & set(right)))
