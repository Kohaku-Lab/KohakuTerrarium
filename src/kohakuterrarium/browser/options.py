"""Browser session options: defaults, validation and normalisation."""

import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any

from kohakuterrarium.browser.policy import ExternalCheck, HostPattern, NetworkPolicy
from kohakuterrarium.browser.secrets import parse_secrets
from kohakuterrarium.errors import ConfigError

VIEWPORT_WIDTH = (320, 7680)
VIEWPORT_HEIGHT = (200, 4320)
TIMEOUT_RANGE = (0.0, 3600.0)
IDLE_CLOSE_RANGE = (0.0, 86400.0)
SNAPSHOT_CHARS_RANGE = (1000, 1_000_000)

_VIEWPORT_TEXT = re.compile(r"\s*(\d+)\s*[xX]\s*(\d+)\s*")


@dataclass(frozen=True)
class BrowserOptions:
    """The ``browser`` plugin options, validated; build with :meth:`from_mapping`."""

    headless: bool = True
    channel: str | None = None
    executable_path: str | None = None
    viewport: tuple[int, int] = (1280, 800)
    profile_dir: str | None = None
    allow_hosts: tuple[str, ...] = ()
    block_hosts: tuple[str, ...] = ()
    allow_loopback: bool = False
    allow_private: bool = False
    secrets: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({}), hash=False
    )
    nav_timeout_s: float = 30.0
    action_timeout_s: float = 5.0
    snapshot_max_chars: int = 20000
    idle_close_s: float = 600.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "BrowserOptions":
        """Validate and normalise a plain options mapping; raises ``ConfigError``."""
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise ConfigError("browser options must be a mapping")
        known = {f.name for f in fields(cls)}
        unknown = sorted(str(key) for key in data if key not in known)
        if unknown:
            raise ConfigError(f"unknown browser option(s): {', '.join(unknown)}")
        values = {key: _PARSERS[key](key, value) for key, value in data.items()}
        return cls(**values)

    def network_policy(
        self, external_check: ExternalCheck | None = None
    ) -> NetworkPolicy:
        """The :class:`NetworkPolicy` these options describe."""
        return NetworkPolicy(
            allow_hosts=self.allow_hosts,
            block_hosts=self.block_hosts,
            allow_loopback=self.allow_loopback,
            allow_private=self.allow_private,
            external_check=external_check,
        )


def _fail(key: str, message: str) -> ConfigError:
    return ConfigError(f"browser option {key!r}: {message}")


def _bool(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _fail(key, "must be true or false")
    return value


def _optional_text(key: str, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if not isinstance(value, str) or not value.strip():
        raise _fail(key, "must be a non-empty string")
    return value.strip()


def _viewport(key: str, value: Any) -> tuple[int, int]:
    if isinstance(value, str):
        match = _VIEWPORT_TEXT.fullmatch(value)
        if match is None:
            raise _fail(key, "must look like '1280x800'")
        pair = [int(match.group(1)), int(match.group(2))]
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        pair = list(value)
    else:
        raise _fail(key, "must be 'WIDTHxHEIGHT' or [width, height]")
    for number, (low, high) in zip(pair, (VIEWPORT_WIDTH, VIEWPORT_HEIGHT)):
        if not _is_int(number) or not low <= number <= high:
            raise _fail(key, f"width {VIEWPORT_WIDTH} and height {VIEWPORT_HEIGHT}")
    return pair[0], pair[1]


def _hosts(key: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _fail(key, "must be a list of host patterns")
    result: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise _fail(key, f"{entry!r} is not a string")
        try:
            text = HostPattern.parse(entry).text
        except ValueError as exc:
            raise _fail(key, str(exc)) from None
        if text not in result:
            result.append(text)
    return tuple(result)


def _secrets(key: str, value: Any) -> Mapping[str, Mapping[str, Any]]:
    try:
        return parse_secrets(value)
    except ConfigError as exc:
        raise _fail(key, str(exc)) from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _seconds(bounds: tuple[float, float]) -> Callable[[str, Any], float]:
    def parse(key: str, value: Any) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise _fail(key, "must be a number of seconds")
        low, high = bounds
        if not math.isfinite(value) or not low < value <= high:
            raise _fail(key, f"must be greater than {low:g} and at most {high:g}")
        return float(value)

    return parse


def _snapshot_chars(key: str, value: Any) -> int:
    low, high = SNAPSHOT_CHARS_RANGE
    if not _is_int(value) or not low <= value <= high:
        raise _fail(key, f"must be an integer from {low} to {high}")
    return value


_PARSERS = {
    "headless": _bool,
    "channel": _optional_text,
    "executable_path": _optional_text,
    "viewport": _viewport,
    "profile_dir": _optional_text,
    "allow_hosts": _hosts,
    "block_hosts": _hosts,
    "allow_loopback": _bool,
    "allow_private": _bool,
    "secrets": _secrets,
    "nav_timeout_s": _seconds(TIMEOUT_RANGE),
    "action_timeout_s": _seconds(TIMEOUT_RANGE),
    "snapshot_max_chars": _snapshot_chars,
    "idle_close_s": _seconds(IDLE_CLOSE_RANGE),
}
