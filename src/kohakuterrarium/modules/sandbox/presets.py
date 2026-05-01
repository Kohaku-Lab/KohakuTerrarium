"""Built-in sandbox profile presets."""

from kohakuterrarium.modules.sandbox.profile import SandboxProfile

PURE = SandboxProfile(
    name="PURE",
    fs_read="deny",
    fs_write="deny",
    network="deny",
    syscall="pure",
    tmp="private",
    env="filtered",
    risk="safe",
)

READ_ONLY = SandboxProfile(
    name="READ_ONLY",
    fs_read="broad",
    fs_write="deny",
    network="deny",
    syscall="fs",
    tmp="private",
    env="filtered",
    risk="low",
)

WORKSPACE = SandboxProfile(
    name="WORKSPACE",
    fs_read="broad",
    fs_write="workspace",
    network="allow",
    syscall="fs",
    tmp="private",
    env="filtered",
    risk="medium",
)

NETWORK = SandboxProfile(
    name="NETWORK",
    fs_read="deny",
    fs_write="deny",
    network="allow",
    syscall="pure",
    tmp="private",
    env="filtered",
    risk="medium",
)

SHELL = SandboxProfile(
    name="SHELL",
    fs_read="broad",
    fs_write="workspace",
    network="allow",
    syscall="shell",
    tmp="private",
    env="filtered",
    risk="high",
)

UNKNOWN_PROFILE = SandboxProfile(
    name="UNKNOWN_PROFILE",
    fs_read="deny",
    fs_write="deny",
    network="deny",
    syscall="pure",
    tmp="private",
    env="filtered",
    risk="high",
)

_PRESETS: dict[str, SandboxProfile] = {
    "PURE": PURE,
    "READ_ONLY": READ_ONLY,
    "WORKSPACE": WORKSPACE,
    "NETWORK": NETWORK,
    "SHELL": SHELL,
    "UNKNOWN_PROFILE": UNKNOWN_PROFILE,
}

_ALIASES: dict[str, str] = {
    "pure": "PURE",
    "read_only": "READ_ONLY",
    "readonly": "READ_ONLY",
    "read-only": "READ_ONLY",
    "workspace": "WORKSPACE",
    "network": "NETWORK",
    "shell": "SHELL",
    "unknown": "UNKNOWN_PROFILE",
    "unknown_profile": "UNKNOWN_PROFILE",
}


def get_profile(name: str) -> SandboxProfile:
    """Resolve a preset by name or alias."""
    key = str(name or "WORKSPACE")
    key = _ALIASES.get(key, key)
    key = _ALIASES.get(key.lower(), key)
    if key not in _PRESETS:
        raise ValueError(
            f"Unknown sandbox profile {name!r}; expected one of: "
            f"{', '.join(sorted(_PRESETS))}"
        )
    return _PRESETS[key]


def list_profiles() -> dict[str, SandboxProfile]:
    """Return every built-in profile keyed by canonical preset name."""
    return dict(_PRESETS)
