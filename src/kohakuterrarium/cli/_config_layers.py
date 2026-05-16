"""Layered configuration loader for ``kt serve`` / ``kt lab-client`` / ``kt-aio``.

Precedence (highest wins):

1. CLI flags (``--port 9002``)              — argparse defaults
2. Environment variables (``KT_HTTP_PORT``) — operator shell / systemd
3. YAML config files (``/etc/kohakuterrarium/host.yaml``) — packaging
4. Built-in defaults (in this module)       — last-resort

A single ``load_layered_config(role, cli_overrides)`` returns the
resolved dict, ready to feed back into the CLI command body.  The
``role`` argument selects which schema applies; the same loader
serves all three deployment commands so the precedence is consistent.

Roles:

- ``"host"``   — ``kt serve --mode lab-host``
- ``"client"`` — ``kt lab-client``
- ``"all"``    — ``kt-aio``

YAML search order, per role:

- ``/etc/kohakuterrarium/<role>.yaml``        (system-wide)
- ``$KT_CONFIG_DIR/<role>.yaml``              (per-user)
- the path in ``KT_CONFIG_FILE`` env-var, if set (explicit override)

Earlier files are overridden by later files in the search order.
"""

import argparse
import os
from pathlib import Path
from typing import Any

import yaml

_BUILTIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "host": {
        "http": {"host": "127.0.0.1", "port": 8001},
        "lab": {"bind": "127.0.0.1:8100", "token": ""},
        "home_dir": "",
        "log_level": "INFO",
    },
    "client": {
        "host_url": "",
        "host_token": "",
        "client_name": "",
        "home_dir": "",
        "session_dir": "",
        "heartbeat_interval": 5.0,
        "log_level": "INFO",
    },
    "all": {
        "http": {"host": "0.0.0.0", "port": 8001},
        "lab": {"bind": "0.0.0.0:8100", "token": ""},
        "client_name": "local-1",
        "home_dir": "",
    },
}


_ENV_MAP: dict[str, dict[str, tuple[str, ...]]] = {
    "host": {
        "KT_HTTP_HOST": ("http", "host"),
        "KT_HTTP_PORT": ("http", "port"),
        "KT_LAB_BIND": ("lab", "bind"),
        "KT_LAB_TOKEN": ("lab", "token"),
        "KT_HOST_TOKEN": ("lab", "token"),
        "KT_CONFIG_DIR": ("home_dir",),
        "KT_LOG_LEVEL": ("log_level",),
    },
    "client": {
        "KT_HOST_URL": ("host_url",),
        "KT_HOST_TOKEN": ("host_token",),
        "KT_CLIENT_NAME": ("client_name",),
        "KT_CONFIG_DIR": ("home_dir",),
        "KT_SESSION_DIR": ("session_dir",),
        "KT_HEARTBEAT_INTERVAL": ("heartbeat_interval",),
        "KT_LOG_LEVEL": ("log_level",),
    },
    "all": {
        "KT_HTTP_HOST": ("http", "host"),
        "KT_HTTP_PORT": ("http", "port"),
        "KT_LAB_BIND": ("lab", "bind"),
        "KT_LAB_TOKEN": ("lab", "token"),
        "KT_HOST_TOKEN": ("lab", "token"),
        "KT_CLIENT_NAME": ("client_name",),
        "KT_CONFIG_DIR": ("home_dir",),
    },
}


_INT_KEYS = {("http", "port"), ("heartbeat_interval",)}
_FLOAT_KEYS = {("heartbeat_interval",)}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return a NEW dict: ``overlay`` keys win, nested dicts merged."""
    out: dict[str, Any] = {}
    for key, value in base.items():
        out[key] = dict(value) if isinstance(value, dict) else value
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _set_nested(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Set ``d[path[0]][path[1]]…`` to ``value``, creating sub-dicts."""
    cursor = d
    for key in path[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[key] = nxt
        cursor = nxt
    cursor[path[-1]] = value


def _coerce(path: tuple[str, ...], value: str) -> Any:
    if path in _INT_KEYS:
        try:
            return int(value)
        except ValueError:
            pass
    if path in _FLOAT_KEYS:
        try:
            return float(value)
        except ValueError:
            pass
    return value


def _candidate_yaml_paths(role: str) -> list[Path]:
    """Filesystem locations the loader scans, lowest precedence first."""
    out: list[Path] = []
    out.append(Path("/etc/kohakuterrarium") / f"{role}.yaml")
    config_dir = os.environ.get("KT_CONFIG_DIR")
    if config_dir:
        out.append(Path(config_dir).expanduser() / f"{role}.yaml")
    else:
        out.append(Path("~/.kohakuterrarium").expanduser() / f"{role}.yaml")
    explicit = os.environ.get("KT_CONFIG_FILE")
    if explicit:
        out.append(Path(explicit).expanduser())
    return out


def _load_yaml_layer(path: Path) -> dict[str, Any]:
    """Read a single YAML file; missing-file / empty-file → ``{}``."""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"top-level YAML in {path} must be a mapping, got {type(data).__name__}"
        )
    return data


def _env_overlay(role: str) -> dict[str, Any]:
    """Build a fresh overlay dict from process env per ``_ENV_MAP``."""
    out: dict[str, Any] = {}
    for env_name, path in _ENV_MAP.get(role, {}).items():
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        _set_nested(out, path, _coerce(path, value))
    return out


def load_layered_config(
    role: str,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the layered config for ``role``.

    Args:
        role: one of ``"host"``, ``"client"``, ``"all"``.
        cli_overrides: optional dict of values to apply last (highest
            precedence). Keys MAY be nested dicts mirroring the schema
            (``{"http": {"port": 9002}}``) or single top-level scalars.

    Returns:
        A dict with the same shape as ``_BUILTIN_DEFAULTS[role]``.
    """
    if role not in _BUILTIN_DEFAULTS:
        raise ValueError(
            f"unknown config role {role!r}; expected one of "
            f"{sorted(_BUILTIN_DEFAULTS)}"
        )

    layered = dict(_BUILTIN_DEFAULTS[role])
    layered = _deep_merge(_BUILTIN_DEFAULTS[role], {})  # deep copy

    for path in _candidate_yaml_paths(role):
        layered = _deep_merge(layered, _load_yaml_layer(path))

    layered = _deep_merge(layered, _env_overlay(role))

    if cli_overrides:
        layered = _deep_merge(layered, cli_overrides)

    return layered


def apply_cli_to_overrides(
    args: argparse.Namespace,
    mapping: dict[str, tuple[str, ...]],
    *,
    skip_falsy: bool = True,
) -> dict[str, Any]:
    """Translate argparse attrs into a nested overrides dict.

    ``mapping`` maps each argparse attribute name to its target nested
    path in the layered config (``"port" → ("http", "port")``).  When
    ``skip_falsy`` is True (the default), empty strings / None / 0 are
    treated as "user did not pass this flag" and skipped, so the YAML
    / env layers stay authoritative.  Pass ``skip_falsy=False`` if 0
    is a meaningful value for some key.
    """
    out: dict[str, Any] = {}
    for attr, path in mapping.items():
        if not hasattr(args, attr):
            continue
        value = getattr(args, attr)
        if skip_falsy and not value:
            continue
        _set_nested(out, path, value)
    return out


__all__ = [
    "load_layered_config",
    "apply_cli_to_overrides",
]
