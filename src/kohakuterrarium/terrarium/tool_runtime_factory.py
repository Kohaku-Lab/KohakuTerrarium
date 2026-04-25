"""Runtime factory helper for terrarium tools."""

from typing import Any

from kohakuterrarium.terrarium.config import load_terrarium_config


def create_runtime_from_config(config_path: str, runtime_class: type[Any]) -> Any:
    """Create a terrarium runtime from a config path and runtime class."""
    config = load_terrarium_config(config_path)
    return runtime_class(config)
