"""
Terrarium configuration loading.

Loads multi-agent terrarium config from YAML, resolving creature
config paths relative to the terrarium config directory.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kohakuterrarium.packages.resolve import resolve_any_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChannelConfig:
    """Configuration for a single terrarium channel."""

    name: str
    channel_type: str = "queue"  # "queue" or "broadcast"
    description: str = ""


@dataclass
class CreatureConfig:
    """Configuration for a creature in a terrarium.

    Uses the same agent config format as standalone creatures.
    The terrarium adds channel wiring as metadata on top.
    """

    name: str
    config_data: dict  # Full agent config dict (supports base_config inheritance)
    base_dir: Path  # Directory for resolving relative paths
    listen_channels: list[str] = field(default_factory=list)
    send_channels: list[str] = field(default_factory=list)
    output_log: bool = False
    output_log_size: int = 100


@dataclass
class RootConfig:
    """Optional root agent configuration.

    The root agent sits OUTSIDE the terrarium and manages it via
    terrarium tools. This is an inline agent config that supports
    base_config inheritance - the user can point to creatures/root
    and override I/O, model, etc.
    """

    config_data: dict  # Raw agent config dict (supports base_config inheritance)
    base_dir: Path  # Directory for resolving relative paths


@dataclass
class TerrariumConfig:
    """Top-level terrarium configuration."""

    name: str
    creatures: list[CreatureConfig]
    channels: list[ChannelConfig]
    root: RootConfig | None = None
    # Cap on runtime-spawned members; 0 means unbounded.
    max_creatures: int = 0


def _format_channel_block(
    ch_name: str,
    ch_by_name: dict[str, "ChannelConfig"],
    listen_set: set[str],
    send_set: set[str],
) -> str:
    """Format a single channel's prompt line for the topology section.

    Returns an empty string if the channel is not found in ch_by_name.
    """
    ch_cfg = ch_by_name.get(ch_name)
    if ch_cfg is None:
        return ""

    desc = f" -- {ch_cfg.description}" if ch_cfg.description else ""
    roles: list[str] = []
    if ch_name in listen_set:
        roles.append("listen")
    if ch_name in send_set:
        roles.append("send")
    role_str = f" ({', '.join(roles)})" if roles else ""

    return f"- `{ch_name}` [{ch_cfg.channel_type}]{role_str}{desc}"


def _find_terrarium_config(path: Path) -> Path:
    """
    Resolve the terrarium config file path.

    If *path* is a file, return it directly.
    If it is a directory, look for ``terrarium.yaml`` or ``terrarium.yml``.

    Raises:
        FileNotFoundError: If no config file can be located.
    """
    if path.is_file():
        return path

    for name in ("terrarium.yaml", "terrarium.yml"):
        candidate = path / name
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"No terrarium config found at {path} "
        "(expected terrarium.yaml or terrarium.yml)"
    )


def _parse_creature(data: dict, base_dir: Path) -> CreatureConfig:
    """Parse a single creature entry from raw YAML data.

    The creature entry is a standard agent config dict with optional
    terrarium wiring fields (channels, output_log). Everything else
    is passed through as agent config for build_agent_config().
    """
    data = dict(data)  # Don't mutate the original
    name = data.get("name", "")
    if not name:
        raise ValueError("Creature entry missing 'name'")

    # Extract terrarium-specific fields (not part of agent config)
    channels = data.pop("channels", {})
    output_log = data.pop("output_log", False)
    output_log_size = data.pop("output_log_size", 100)

    # Backward compat: if "config" key exists (old path-only format),
    # convert to base_config
    if "config" in data and "base_config" not in data:
        data["base_config"] = data.pop("config")

    return CreatureConfig(
        name=name,
        config_data=data,
        base_dir=base_dir,
        listen_channels=list(channels.get("listen", [])),
        send_channels=list(channels.get("can_send", [])),
        output_log=bool(output_log),
        output_log_size=int(output_log_size),
    )


def _parse_channels(raw: dict) -> list[ChannelConfig]:
    """Parse the channels mapping from raw YAML data."""
    result: list[ChannelConfig] = []
    for ch_name, ch_data in raw.items():
        if isinstance(ch_data, dict):
            result.append(
                ChannelConfig(
                    name=ch_name,
                    channel_type=ch_data.get("type", "queue"),
                    description=ch_data.get("description", ""),
                )
            )
        else:
            # Bare channel name with no extra config
            result.append(ChannelConfig(name=ch_name))
    return result


def load_terrarium_config(path: str | Path) -> TerrariumConfig:
    """
    Load terrarium configuration from a YAML file or directory.

    Supports both a direct file path and a directory containing
    ``terrarium.yaml``.  Creature ``config`` paths are resolved
    relative to the directory that holds the terrarium YAML file.

    Args:
        path: File or directory path, or a ``@pkg/...`` package
            reference.

    Returns:
        Parsed TerrariumConfig.

    Raises:
        FileNotFoundError: If config file cannot be found.
        PackageError: If a ``@pkg`` reference is malformed or names an
            uninstalled package.
        ValueError: If required fields are missing.
    """
    path = resolve_any_path(path)
    config_file = _find_terrarium_config(path)
    base_dir = config_file.parent

    logger.debug("Loading terrarium config", path=str(config_file))

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # The top-level key is "terrarium"
    terrarium_data = raw.get("terrarium", raw)

    name = terrarium_data.get("name", "terrarium")

    # Parse creatures
    creatures_raw = terrarium_data.get("creatures", [])
    creatures = [_parse_creature(c, base_dir) for c in creatures_raw]

    # Parse channels
    channels_raw = terrarium_data.get("channels", {})
    channels = _parse_channels(channels_raw)

    # Parse optional root agent (inline agent config with base_config support)
    root: RootConfig | None = None
    root_raw = terrarium_data.get("root")
    if root_raw:
        root = RootConfig(config_data=dict(root_raw), base_dir=base_dir)

    config = TerrariumConfig(
        name=name,
        creatures=creatures,
        channels=channels,
        root=root,
        max_creatures=int(terrarium_data.get("max_creatures", 0) or 0),
    )

    logger.info(
        "Terrarium config loaded",
        terrarium_name=config.name,
        creatures=len(config.creatures),
        channels=len(config.channels),
    )
    return config
